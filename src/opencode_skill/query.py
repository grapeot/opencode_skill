from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    if not value:
        return default
    return Path(value).expanduser()


_OC = Path.home() / ".local" / "share" / "opencode"
DEFAULT_MAIN_DB = _env_path("OPENCODE_MAIN_DB", _OC / "opencode.db")
DEFAULT_ARCHIVE_DBS: tuple[Path, ...] = (
    _env_path("OPENCODE_ARCHIVE_DB", _OC / "opencode_archive.db"),
    _env_path("OPENCODE_OLD_ARCHIVE_DB", _OC / "archive" / "old_sessions.db"),
)


@dataclass
class AssistantMessage:
    id: str
    session_id: str
    time_created: int  # ms epoch
    provider: str | None
    model: str | None
    tokens_input: int
    tokens_output: int
    tokens_reasoning: int
    tokens_cache_read: int
    tokens_cache_write: int
    source_db: str  # 'main' or archive filename


def iter_assistant_messages(
    since_ms: int | None = None,
    until_ms: int | None = None,
    *,
    main_db: Path = DEFAULT_MAIN_DB,
    archive_dbs: Sequence[Path] = DEFAULT_ARCHIVE_DBS,
    include_archive: bool = True,
) -> Iterator[AssistantMessage]:
    """Iterate assistant messages across main + (optionally) archive DBs.

    Two-DB policy (PRD §4.4 / RFC §5):
      - include_archive=True  (default): yield from main + every existing archive DB
        so callers like token analytics see the complete spend.
      - include_archive=False: only main DB. Use for export pipelines that should
        skip already-archived (typically batch) sessions.

    Missing archive DBs are skipped silently (warning would be noise during normal
    usage when no archive yet exists).
    """
    sources: list[tuple[str, Path]] = [("main", main_db)]
    if include_archive:
        for p in archive_dbs:
            if p.exists():
                sources.append((p.name, p))

    for tag, db_path in sources:
        if not db_path.exists():
            continue
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            yield from _iter_one(conn, tag, since_ms, until_ms)
        finally:
            conn.close()


def _iter_one(
    conn: sqlite3.Connection, tag: str, since_ms: int | None, until_ms: int | None
) -> Iterator[AssistantMessage]:
    # Filter assistant role and parse tokens in Python — json_extract aborts the
    # whole query on a single malformed row, which is too brittle for analytics.
    sql = "SELECT id, session_id, time_created, data FROM message"
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
    sql += " ORDER BY time_created"

    for mid, sid, ts, data_str in conn.execute(sql, params):
        try:
            data = json.loads(data_str)
        except (json.JSONDecodeError, TypeError):
            continue
        if data.get("role") != "assistant":
            continue
        tokens = data.get("tokens") or {}
        cache = tokens.get("cache") or {}
        yield AssistantMessage(
            id=mid,
            session_id=sid,
            time_created=ts,
            provider=data.get("providerID"),
            model=data.get("modelID"),
            tokens_input=tokens.get("input", 0) or 0,
            tokens_output=tokens.get("output", 0) or 0,
            tokens_reasoning=tokens.get("reasoning", 0) or 0,
            tokens_cache_read=cache.get("read", 0) or 0,
            tokens_cache_write=cache.get("write", 0) or 0,
            source_db=tag,
        )


def stats(
    main_db: Path = DEFAULT_MAIN_DB,
    archive_dbs: Sequence[Path] = DEFAULT_ARCHIVE_DBS,
) -> dict[str, Any]:
    """Return per-DB row counts and file sizes. Read-only.

    Used by the `stats` CLI subcommand and external sanity checks.
    """
    out = {"databases": []}
    for label, p in [("main", main_db)] + [(p.name, p) for p in archive_dbs]:
        if not p.exists():
            out["databases"].append({"label": label, "path": str(p), "exists": False})
            continue
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            counts = {}
            for tbl in ("session", "message", "part", "project"):
                try:
                    counts[tbl] = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                except sqlite3.OperationalError:
                    counts[tbl] = None
        finally:
            conn.close()
        out["databases"].append({
            "label": label,
            "path": str(p),
            "exists": True,
            "size_bytes": p.stat().st_size,
            "counts": counts,
        })
    return out
