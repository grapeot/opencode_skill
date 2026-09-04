from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import pytest

from opencode_skill import cli, throughput


def _seed_throughput(db_path: Path, entries: list[dict]) -> None:
    """Seed assistant messages (+ optional tool parts) with controlled timing.

    Each entry: mid, provider, model, created, completed, output, reasoning,
    finish (optional), tool_start (optional ms).
    """
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO project (id, worktree, vcs, time_created, time_updated, sandboxes) VALUES (?,?,?,?,?,?)",
        ("P", "/tmp", "git", 0, 0, "[]"),
    )
    cur.execute(
        "INSERT OR IGNORE INTO session (id, project_id, slug, directory, title, version, time_created, time_updated) VALUES (?,?,?,?,?,?,?,?)",
        ("S", "P", "s", "/tmp", "t", "0.0.0", 0, 0),
    )
    for e in entries:
        data = {
            "role": "assistant",
            "providerID": e["provider"],
            "modelID": e["model"],
            "tokens": {
                "input": 10,
                "output": e.get("output", 0),
                "reasoning": e.get("reasoning", 0),
                "cache": {"read": 0, "write": 0},
            },
            "time": {"created": e["created"], "completed": e["completed"]},
        }
        if e.get("finish"):
            data["finish"] = e["finish"]
        cur.execute(
            "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?)",
            (e["mid"], "S", e["created"], e["completed"], json.dumps(data)),
        )
        if e.get("tool_start") is not None:
            part_data = {
                "type": "tool",
                "tool": "bash",
                "state": {"status": "completed", "time": {"start": e["tool_start"], "end": e["completed"]}},
            }
            cur.execute(
                "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?,?)",
                (f"prt_{e['mid']}", e["mid"], "S", e["tool_start"], e["completed"], json.dumps(part_data)),
            )
    conn.commit()
    conn.close()


def _mk_db(tmp_path: Path, name: str, empty_template_db: Path) -> Path:
    import shutil

    db = tmp_path / name
    shutil.copyfile(empty_template_db, db)
    return db


def test_percentile_known_values():
    assert throughput.percentile([1, 2, 3, 4, 5], 0.5) == 3
    assert throughput.percentile([1, 2, 3, 4, 5], 0.0) == 1
    assert throughput.percentile([1, 2, 3, 4, 5], 1.0) == 5
    assert throughput.percentile([1, 2, 3, 4], 0.5) == 2.5
    assert throughput.percentile([42], 0.5) == 42
    assert math.isnan(throughput.percentile([], 0.5))


def test_measure_basic(tmp_path, empty_template_db):
    db = _mk_db(tmp_path, "main.db", empty_template_db)
    _seed_throughput(
        db,
        [
            {"mid": "m1", "provider": "prov", "model": "A", "created": 0, "completed": 1000, "output": 100},
            {"mid": "m2", "provider": "prov", "model": "A", "created": 0, "completed": 2000, "output": 100},
            {"mid": "m3", "provider": "prov", "model": "A", "created": 0, "completed": 500, "output": 100},
        ],
    )
    res = throughput.measure_throughput(db, archive_dbs=[])
    assert len(res) == 1
    r = res[0]
    assert r.model == "A"
    assert r.n == 3
    assert r.total_output_tokens == 300
    assert r.p50 == pytest.approx(100.0)
    assert r.min == pytest.approx(50.0)
    assert r.max == pytest.approx(200.0)


def test_reasoning_tokens_counted(tmp_path, empty_template_db):
    db = _mk_db(tmp_path, "main.db", empty_template_db)
    _seed_throughput(
        db,
        [{"mid": "m1", "provider": "prov", "model": "A", "created": 0, "completed": 1000, "output": 50, "reasoning": 50}],
    )
    res = throughput.measure_throughput(db, archive_dbs=[])
    # 100 total tokens over 1s = 100 tps
    assert res[0].p50 == pytest.approx(100.0)
    assert res[0].total_output_tokens == 100


def test_tool_calls_truncation(tmp_path, empty_template_db):
    db = _mk_db(tmp_path, "main.db", empty_template_db)
    # completed=5000 would give 1000/5s=200 tps; tool starts at 2000 -> 1000/2s=500 tps
    _seed_throughput(
        db,
        [
            {
                "mid": "m1", "provider": "prov", "model": "A",
                "created": 0, "completed": 5000, "output": 1000,
                "finish": "tool-calls", "tool_start": 2000,
            }
        ],
    )
    res = throughput.measure_throughput(db, archive_dbs=[])
    assert res[0].p50 == pytest.approx(500.0)


def test_min_output_filter(tmp_path, empty_template_db):
    db = _mk_db(tmp_path, "main.db", empty_template_db)
    _seed_throughput(
        db,
        [
            {"mid": "m1", "provider": "prov", "model": "A", "created": 0, "completed": 1000, "output": 10},
            {"mid": "m2", "provider": "prov", "model": "A", "created": 0, "completed": 1000, "output": 100},
        ],
    )
    res = throughput.measure_throughput(db, archive_dbs=[], min_output=50)
    assert len(res) == 1
    assert res[0].n == 1
    assert res[0].total_output_tokens == 100


def test_filter_provider_and_model(tmp_path, empty_template_db):
    db = _mk_db(tmp_path, "main.db", empty_template_db)
    _seed_throughput(
        db,
        [
            {"mid": "m1", "provider": "p1", "model": "A", "created": 0, "completed": 1000, "output": 100},
            {"mid": "m2", "provider": "p2", "model": "B", "created": 0, "completed": 1000, "output": 100},
        ],
    )
    assert [r.model for r in throughput.measure_throughput(db, archive_dbs=[], provider="p2")] == ["B"]
    assert [r.model for r in throughput.measure_throughput(db, archive_dbs=[], model="A")] == ["A"]


def test_time_window(tmp_path, empty_template_db):
    db = _mk_db(tmp_path, "main.db", empty_template_db)
    _seed_throughput(
        db,
        [
            {"mid": "m1", "provider": "prov", "model": "A", "created": 1000, "completed": 2000, "output": 100},
            {"mid": "m2", "provider": "prov", "model": "A", "created": 9000, "completed": 10000, "output": 100},
        ],
    )
    res = throughput.measure_throughput(db, archive_dbs=[], since_ms=5000)
    assert len(res) == 1
    assert res[0].n == 1


def test_skips_malformed_and_missing_time(tmp_path, empty_template_db):
    db = _mk_db(tmp_path, "main.db", empty_template_db)
    _seed_throughput(
        db,
        [
            {"mid": "ok", "provider": "prov", "model": "A", "created": 0, "completed": 1000, "output": 100},
        ],
    )
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?)",
        ("bad", "S", 0, 0, "not json"),
    )
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?)",
        ("no_complete", "S", 0, 0, json.dumps(
            {"role": "assistant", "providerID": "prov", "model": "A",
             "tokens": {"output": 100}, "time": {"created": 0}}
        )),
    )
    conn.commit()
    conn.close()
    res = throughput.measure_throughput(db, archive_dbs=[])
    assert res[0].n == 1


def test_render_markdown_report():
    r = throughput.ModelThroughput(
        provider="prov", model="A", n=3, total_output_tokens=300,
        p10=50, p25=75, p50=100, p75=150, p90=190, min=50, max=200, _samples=[50, 100, 200],
    )
    md = throughput.render_markdown_report([r], since_label="30d", min_output=50)
    assert "# OpenCode Inference Throughput Report" in md
    assert "Window: 30d" in md
    assert "`prov/A`" in md
    assert "100.0" in md


def test_render_chart_writes_png(tmp_path, empty_template_db):
    pytest.importorskip("matplotlib")
    db = _mk_db(tmp_path, "main.db", empty_template_db)
    entries = [
        {"mid": f"m{i}", "provider": "prov", "model": "A", "created": 0, "completed": 1000 + i * 100, "output": 100}
        for i in range(5)
    ]
    _seed_throughput(db, entries)
    res = throughput.measure_throughput(db, archive_dbs=[])
    out = tmp_path / "chart.png"
    throughput.render_chart(res, out, top=5)
    assert out.exists()
    assert out.stat().st_size > 0


def test_cli_throughput_json(tmp_path, empty_template_db, capsys):
    db = _mk_db(tmp_path, "main.db", empty_template_db)
    _seed_throughput(
        db,
        [
            {"mid": "m1", "provider": "prov", "model": "A", "created": 0, "completed": 1000, "output": 100},
            {"mid": "m2", "provider": "prov", "model": "A", "created": 0, "completed": 2000, "output": 100},
        ],
    )
    rc = cli.main([
        "--main", str(db),
        "--archive", str(tmp_path / "no.db"),
        "--old-archive", str(tmp_path / "no2.db"),
        "throughput", "--json",
    ])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 1
    assert data[0]["model"] == "A"
    assert data[0]["n"] == 2


def test_cli_throughput_report(tmp_path, empty_template_db, capsys):
    db = _mk_db(tmp_path, "main.db", empty_template_db)
    _seed_throughput(
        db,
        [
            {"mid": f"m{i}", "provider": "prov", "model": "A", "created": 0, "completed": 1000 + i * 100, "output": 100}
            for i in range(4)
        ],
    )
    out = tmp_path / "report.md"
    rc = cli.main([
        "--main", str(db),
        "--archive", str(tmp_path / "no.db"),
        "--old-archive", str(tmp_path / "no2.db"),
        "throughput", "--out", str(out),
    ])
    assert rc == 0
    assert out.exists()
    content = out.read_text()
    assert "# OpenCode Inference Throughput Report" in content
    assert "`prov/A`" in content
    assert "throughput_chart.png" in content
    assert (tmp_path / "throughput_chart.png").exists()
    # table also printed to stdout
    assert "`prov/A`" in capsys.readouterr().out


def test_cli_throughput_no_chart(tmp_path, empty_template_db, capsys):
    db = _mk_db(tmp_path, "main.db", empty_template_db)
    _seed_throughput(
        db,
        [{"mid": "m1", "provider": "prov", "model": "A", "created": 0, "completed": 1000, "output": 100}],
    )
    out = tmp_path / "report.md"
    rc = cli.main([
        "--main", str(db),
        "--archive", str(tmp_path / "no.db"),
        "--old-archive", str(tmp_path / "no2.db"),
        "throughput", "--out", str(out), "--no-chart",
    ])
    assert rc == 0
    assert "throughput_chart.png" not in out.read_text()
    assert not (tmp_path / "throughput_chart.png").exists()


def test_multiple_tool_starts_earliest_wins(tmp_path, empty_template_db):
    db = _mk_db(tmp_path, "main.db", empty_template_db)
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO project (id, worktree, vcs, time_created, time_updated, sandboxes) VALUES (?,?,?,?,?,?)",
        ("P", "/tmp", "git", 0, 0, "[]"),
    )
    cur.execute(
        "INSERT OR IGNORE INTO session (id, project_id, slug, directory, title, version, time_created, time_updated) VALUES (?,?,?,?,?,?,?,?)",
        ("S", "P", "s", "/tmp", "t", "0.0.0", 0, 0),
    )
    cur.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?)",
        ("m1", "S", 0, 5000, json.dumps(
            {"role": "assistant", "providerID": "prov", "model": "A", "finish": "tool-calls",
             "tokens": {"output": 1000}, "time": {"created": 0, "completed": 5000}}
        )),
    )
    # Two tool parts; earliest start (1000) should win over 3000.
    for pid, start in [("p_early", 1000), ("p_late", 3000)]:
        cur.execute(
            "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?,?)",
            (pid, "m1", "S", start, 5000, json.dumps(
                {"type": "tool", "tool": "bash", "state": {"status": "completed", "time": {"start": start, "end": 5000}}}
            )),
        )
    conn.commit()
    conn.close()
    res = throughput.measure_throughput(db, archive_dbs=[])
    # 1000 tokens over (1000-0)ms = 1s -> 1000 tps
    assert res[0].p50 == pytest.approx(1000.0)


def test_tool_calls_missing_start_skipped(tmp_path, empty_template_db):
    db = _mk_db(tmp_path, "main.db", empty_template_db)
    _seed_throughput(
        db,
        [
            # tool-calls but no tool part -> cannot isolate inference time -> skipped
            {"mid": "m1", "provider": "prov", "model": "A", "created": 0, "completed": 5000, "output": 1000, "finish": "tool-calls"},
            # normal completion -> counted
            {"mid": "m2", "provider": "prov", "model": "A", "created": 0, "completed": 1000, "output": 100},
        ],
    )
    res = throughput.measure_throughput(db, archive_dbs=[])
    assert res[0].n == 1
    assert res[0].total_output_tokens == 100


def test_non_object_message_data_skipped(tmp_path, empty_template_db):
    db = _mk_db(tmp_path, "main.db", empty_template_db)
    _seed_throughput(
        db,
        [{"mid": "ok", "provider": "prov", "model": "A", "created": 0, "completed": 1000, "output": 100}],
    )
    conn = sqlite3.connect(str(db))
    # valid JSON but not an object -> must be skipped, not crash
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?)",
        ("arr", "S", 0, 0, json.dumps([1, 2, 3])),
    )
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?)",
        ("badtokens", "S", 0, 0, json.dumps(
            {"role": "assistant", "providerID": "prov", "model": "A",
             "tokens": "not-a-dict", "time": {"created": 0, "completed": 1000}}
        )),
    )
    conn.commit()
    conn.close()
    res = throughput.measure_throughput(db, archive_dbs=[])
    assert res[0].n == 1
    assert res[0].total_output_tokens == 100


def test_deterministic_tie_break(tmp_path, empty_template_db):
    db = _mk_db(tmp_path, "main.db", empty_template_db)
    _seed_throughput(
        db,
        [
            {"mid": "m1", "provider": "prov", "model": "B", "created": 0, "completed": 1000, "output": 100},
            {"mid": "m2", "provider": "prov", "model": "A", "created": 0, "completed": 1000, "output": 100},
            {"mid": "m3", "provider": "prov", "model": "C", "created": 0, "completed": 1000, "output": 100},
        ],
    )
    res = throughput.measure_throughput(db, archive_dbs=[])
    # all n=1 -> sorted by provider, then model
    assert [r.model for r in res] == ["A", "B", "C"]


def test_archive_aggregation_and_main_only(tmp_path, empty_template_db):
    main = _mk_db(tmp_path, "main.db", empty_template_db)
    arch = _mk_db(tmp_path, "archive.db", empty_template_db)
    _seed_throughput(main, [{"mid": "m1", "provider": "prov", "model": "MainOnly", "created": 0, "completed": 1000, "output": 100}])
    _seed_throughput(arch, [{"mid": "m2", "provider": "prov", "model": "ArchOnly", "created": 0, "completed": 1000, "output": 100}])

    both = {r.model for r in throughput.measure_throughput(main, [arch], include_archive=True)}
    assert both == {"MainOnly", "ArchOnly"}

    main_only = {r.model for r in throughput.measure_throughput(main, [arch], include_archive=False)}
    assert main_only == {"MainOnly"}


def test_cli_missing_main_db(tmp_path, capsys):
    rc = cli.main([
        "--main", str(tmp_path / "does_not_exist.db"),
        "--archive", str(tmp_path / "no.db"),
        "--old-archive", str(tmp_path / "no2.db"),
        "throughput",
    ])
    assert rc == 1
    assert "main database not found" in capsys.readouterr().err


def test_cli_top_must_be_positive(tmp_path, empty_template_db, capsys):
    db = _mk_db(tmp_path, "main.db", empty_template_db)
    rc = cli.main([
        "--main", str(db),
        "--archive", str(tmp_path / "no.db"),
        "--old-archive", str(tmp_path / "no2.db"),
        "throughput", "--top", "0",
    ])
    assert rc == 2
    assert "--top must be a positive integer" in capsys.readouterr().err


def test_cli_json_writes_no_file(tmp_path, empty_template_db, capsys):
    db = _mk_db(tmp_path, "main.db", empty_template_db)
    _seed_throughput(
        db,
        [{"mid": "m1", "provider": "prov", "model": "A", "created": 0, "completed": 1000, "output": 100}],
    )
    rc = cli.main([
        "--main", str(db),
        "--archive", str(tmp_path / "no.db"),
        "--old-archive", str(tmp_path / "no2.db"),
        "throughput", "--json",
    ])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["model"] == "A"
    # --json must not write the default report or chart
    assert not (tmp_path / "throughput_report.md").exists()
    assert not (tmp_path / "throughput_chart.png").exists()