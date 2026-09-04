from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Sequence, TypeGuard

MIN_DURATION_S = 0.2
MAX_DURATION_S = 3600.0
_PART_BATCH = 400


def _is_num(v: object) -> TypeGuard[int | float]:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _as_int(v: object) -> int:
    return int(v) if _is_num(v) else 0


@dataclass
class ThroughputSample:
    message_id: str
    provider: str
    model: str
    output_tokens: int
    duration_s: float
    tps: float
    time_created: int


@dataclass
class ModelThroughput:
    provider: str
    model: str
    n: int
    total_output_tokens: int
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float
    min: float
    max: float
    _samples: list[float] = field(default_factory=list, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "n": self.n,
            "total_output_tokens": self.total_output_tokens,
            "p10": round(self.p10, 2),
            "p25": round(self.p25, 2),
            "p50": round(self.p50, 2),
            "p75": round(self.p75, 2),
            "p90": round(self.p90, 2),
            "min": round(self.min, 2),
            "max": round(self.max, 2),
        }


def percentile(values: Sequence[float], p: float) -> float:
    """Linear-interpolation percentile (numpy 'linear' method). p in [0, 1]."""
    if not values:
        return float("nan")
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def _tool_start_times(conn: sqlite3.Connection, message_ids: list[str]) -> dict[str, int]:
    """Earliest tool state.time.start (ms) per message, for truncating generation time."""
    result: dict[str, int] = {}
    for i in range(0, len(message_ids), _PART_BATCH):
        chunk = message_ids[i : i + _PART_BATCH]
        qmark = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT message_id, data FROM part WHERE message_id IN ({qmark})", chunk
        ).fetchall()
        for pid, pdata in rows:
            try:
                d = json.loads(pdata)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(d, dict) or d.get("type") != "tool":
                continue
            state = d.get("state")
            if not isinstance(state, dict):
                continue
            t = state.get("time")
            if not isinstance(t, dict):
                continue
            raw_start = t.get("start")
            if not _is_num(raw_start):
                continue
            start = int(raw_start)
            if pid not in result or start < result[pid]:
                result[pid] = start
    return result


def _iter_samples_one(
    conn: sqlite3.Connection,
    *,
    since_ms: int | None,
    until_ms: int | None,
    min_output: int,
) -> Iterator[ThroughputSample]:
    sql = "SELECT id, time_created, data FROM message"
    where: list[str] = []
    params: list[object] = []
    if since_ms is not None:
        where.append("time_created >= ?")
        params.append(since_ms)
    if until_ms is not None:
        where.append("time_created < ?")
        params.append(until_ms)
    if where:
        sql += " WHERE " + " AND ".join(where)

    msgs: list[tuple[str, int, int, int, str, str, str]] = []
    for mid, ts, data_str in conn.execute(sql, params):
        try:
            data = json.loads(data_str)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict) or data.get("role") != "assistant":
            continue
        tokens = data.get("tokens")
        if not isinstance(tokens, dict):
            tokens = {}
        out = _as_int(tokens.get("output")) + _as_int(tokens.get("reasoning"))
        if out < min_output:
            continue
        t = data.get("time")
        if not isinstance(t, dict):
            continue
        created = t.get("created")
        completed = t.get("completed")
        if not _is_num(created) or not _is_num(completed):
            continue
        provider = data.get("providerID") or "unknown"
        model = data.get("modelID") or "unknown"
        finish = data.get("finish")
        msgs.append(
            (mid, int(created), int(completed), int(out), finish if isinstance(finish, str) else "", provider, model)
        )

    tool_start = _tool_start_times(conn, [m[0] for m in msgs])
    for mid, created, completed, out, finish, provider, model in msgs:
        if finish == "tool-calls":
            start = tool_start.get(mid)
            if start is None or not (created <= start <= completed):
                # Can't isolate inference time from tool execution; skip.
                continue
            end = start
        else:
            end = completed
        dur = (end - created) / 1000.0
        if dur < MIN_DURATION_S or dur > MAX_DURATION_S:
            continue
        yield ThroughputSample(mid, provider, model, out, dur, out / dur, created)


def _build_stats(provider: str, model: str, vals: list[float], total_tokens: int) -> ModelThroughput:
    return ModelThroughput(
        provider=provider,
        model=model,
        n=len(vals),
        total_output_tokens=total_tokens,
        p10=percentile(vals, 0.10),
        p25=percentile(vals, 0.25),
        p50=percentile(vals, 0.50),
        p75=percentile(vals, 0.75),
        p90=percentile(vals, 0.90),
        min=min(vals),
        max=max(vals),
        _samples=list(vals),
    )


def measure_throughput(
    main_db: Path,
    archive_dbs: Sequence[Path] = (),
    *,
    since_ms: int | None = None,
    until_ms: int | None = None,
    min_output: int = 50,
    provider: str | None = None,
    model: str | None = None,
    include_archive: bool = True,
) -> list[ModelThroughput]:
    """Measure effective output throughput (tokens/s) per provider/model.

    Throughput = (output + reasoning tokens) / generation duration. Duration is
    time.created -> time.completed, truncated at the first tool call start when the
    message ended with tool-calls (tool execution is not model inference). Prefill
    time is included, so results are effective rates, not pure decode speed.
    """
    sources: list[tuple[str, Path]] = [("main", main_db)]
    if include_archive:
        for p in archive_dbs:
            if p.exists():
                sources.append((p.name, p))

    grouped: dict[tuple[str, str], list[float]] = {}
    totals: dict[tuple[str, str], int] = {}
    for _tag, db_path in sources:
        if not db_path.exists():
            continue
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            for s in _iter_samples_one(
                conn, since_ms=since_ms, until_ms=until_ms, min_output=min_output
            ):
                if provider and s.provider != provider:
                    continue
                if model and s.model != model:
                    continue
                key = (s.provider, s.model)
                grouped.setdefault(key, []).append(s.tps)
                totals[key] = totals.get(key, 0) + s.output_tokens
        finally:
            conn.close()

    results = [
        _build_stats(prov, mdl, vals, totals[(prov, mdl)])
        for (prov, mdl), vals in grouped.items()
    ]
    results.sort(key=lambda r: (-r.n, r.provider, r.model))
    return results


def render_markdown_report(
    results: Sequence[ModelThroughput],
    *,
    since_label: str | None = None,
    min_output: int = 50,
    chart_path: str | None = None,
    top: int | None = None,
) -> str:
    rows = results[:top] if top else list(results)
    lines: list[str] = []
    lines.append("# OpenCode Inference Throughput Report")
    lines.append("")
    if since_label:
        lines.append(f"Window: {since_label}")
    lines.append(f"Min output tokens per sample: {min_output}")
    lines.append("")
    lines.append("Effective output throughput = (output + reasoning tokens) / generation duration.")
    lines.append("Duration excludes tool execution time (truncated at the first tool call start).")
    lines.append("Prefill time is included, so these are effective rates, not pure decode speed.")
    lines.append("")
    lines.append("| Provider / Model | Samples | P10 | P25 | P50 | P75 | P90 | Total generated tokens |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        lines.append(
            f"| `{r.provider}/{r.model}` | {r.n:,} | {r.p10:.1f} | {r.p25:.1f} "
            f"| {r.p50:.1f} | {r.p75:.1f} | {r.p90:.1f} | {r.total_output_tokens:,} |"
        )
    lines.append("")
    if chart_path:
        lines.append(f"![Throughput distribution]({chart_path})")
        lines.append("")
    return "\n".join(lines)


def render_chart(results: Sequence[ModelThroughput], out_path: Path, *, top: int | None = None) -> Path:
    """Write a box plot of throughput per model. Raises ImportError if matplotlib is absent."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = list(results[:top] if top else results)
    if not rows:
        raise ValueError("no models to plot")
    labels = [f"{r.provider}/{r.model}" for r in rows]
    data = [r._samples for r in rows]
    fig, ax = plt.subplots(figsize=(max(8, len(rows) * 1.2), 6))
    try:
        ax.boxplot(data, tick_labels=labels, whis=(10, 90), showfliers=False)
        ax.set_ylabel("throughput (tokens/s)")
        ax.set_title("Inference throughput per model (box = P25-P75, whiskers = P10-P90)")
        ax.tick_params(axis="x", rotation=45)
        fig.tight_layout()
        fig.savefig(out_path, dpi=120)
    finally:
        plt.close(fig)
    return out_path