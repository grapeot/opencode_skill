from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from opencode_skill import cli


def test_parse_cutoff_days():
    ms = cli._parse_cutoff_ms("30d")
    # ms is some recent value; just ensure positive and less than now
    import time
    now_ms = int(time.time() * 1000)
    assert 0 < ms < now_ms


def test_parse_cutoff_iso_date():
    ms = cli._parse_cutoff_ms("2026-04-09")
    # 2026-04-09 00:00 local (won't pin exact, just sanity check)
    from datetime import datetime
    expected = int(datetime(2026, 4, 9).timestamp() * 1000)
    assert ms == expected


def test_parse_cutoff_invalid_raises(monkeypatch):
    with pytest.raises(SystemExit):
        cli._parse_cutoff_ms("not a date")


def test_stats_command(src_db_with_sessions, fresh_dst_db, capsys):
    rc = cli.main([
        "--main", str(src_db_with_sessions),
        "--archive", str(fresh_dst_db),
        "--old-archive", "/nonexistent/path.db",
        "stats",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "main" in out
    assert "sessions=" in out


def test_stats_json(src_db_with_sessions, fresh_dst_db, capsys):
    rc = cli.main([
        "--main", str(src_db_with_sessions),
        "--archive", str(fresh_dst_db),
        "--old-archive", "/nonexistent/path.db",
        "stats", "--json",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "databases" in data
    assert any(db["label"] == "main" for db in data["databases"])


def test_plan_with_title_selector(src_db_with_sessions, fresh_dst_db, capsys):
    rc = cli.main([
        "--main", str(src_db_with_sessions),
        "--archive", str(fresh_dst_db),
        "--old-archive", "/nonexistent/path.db",
        "plan",
        "--selector", "title",
        "--prefix", "batch-",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    # Should pick up batch-foo (S_A) + descendants S_A1, S_A2, S_A2x + batch-bar (S_C) = 5
    assert "5" in out


def test_apply_requires_confirm(src_db_with_sessions, fresh_dst_db, capsys):
    rc = cli.main([
        "--main", str(src_db_with_sessions),
        "--archive", str(fresh_dst_db),
        "--old-archive", "/nonexistent/path.db",
        "apply",
        "--selector", "title",
        "--prefix", "batch-",
        "--dest", str(fresh_dst_db),
    ])
    assert rc == 2  # missing --confirm


def test_apply_full_flow(src_db_with_sessions, tmp_path, capsys):
    dst = tmp_path / "dest.db"
    rc = cli.main([
        "--main", str(src_db_with_sessions),
        "--archive", str(dst),  # not used for apply, but required arg
        "--old-archive", "/nonexistent/path.db",
        "apply",
        "--selector", "title",
        "--prefix", "batch-",
        "--dest", str(dst),
        "--confirm",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "VERIFY ok" in out
    assert "DELETE" in out

    conn = sqlite3.connect(f"file:{src_db_with_sessions}?mode=ro", uri=True)
    try:
        remaining = conn.execute(
            "SELECT id FROM session WHERE id IN ('S_A','S_A1','S_A2','S_A2x','S_C')"
        ).fetchall()
    finally:
        conn.close()
    assert remaining == []


def test_apply_no_expand_deletes_resolved_title_matches(src_db_with_sessions, tmp_path):
    dst = tmp_path / "dest.db"

    rc = cli.main([
        "--main", str(src_db_with_sessions),
        "--archive", str(dst),
        "--old-archive", "/nonexistent/path.db",
        "apply",
        "--selector", "title",
        "--prefix", "batch-",
        "--dest", str(dst),
        "--confirm",
        "--no-expand",
    ])

    assert rc == 0
    conn = sqlite3.connect(f"file:{src_db_with_sessions}?mode=ro", uri=True)
    try:
        removed = conn.execute(
            "SELECT id FROM session WHERE id IN ('S_A','S_C')"
        ).fetchall()
        descendants = conn.execute(
            "SELECT id FROM session WHERE id IN ('S_A1','S_A2','S_A2x')"
        ).fetchall()
    finally:
        conn.close()
    assert removed == []
    assert {row[0] for row in descendants} == {"S_A1", "S_A2", "S_A2x"}


def test_apply_no_delete(src_db_with_sessions, tmp_path):
    dst = tmp_path / "dest.db"
    pre_session_count = sqlite3.connect(f"file:{src_db_with_sessions}?mode=ro", uri=True).execute(
        "SELECT COUNT(*) FROM session"
    ).fetchone()[0]

    rc = cli.main([
        "--main", str(src_db_with_sessions),
        "--archive", str(dst),
        "--old-archive", "/nonexistent/path.db",
        "apply",
        "--selector", "ids", "--file", _write_id_file(tmp_path, ["S_B"]),
        "--dest", str(dst),
        "--confirm",
        "--no-delete",
    ])
    assert rc == 0
    post_session_count = sqlite3.connect(f"file:{src_db_with_sessions}?mode=ro", uri=True).execute(
        "SELECT COUNT(*) FROM session"
    ).fetchone()[0]
    # main untouched
    assert pre_session_count == post_session_count


def _write_id_file(tmp: Path, ids: list[str]) -> str:
    p = tmp / "ids.txt"
    p.write_text("\n".join(ids))
    return str(p)
