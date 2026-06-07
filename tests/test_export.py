from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from opencode_skill import cli, export


def _ts(date_str: str) -> int:
    """Local-midnight ms epoch for a YYYY-MM-DD date."""
    return int(datetime.fromisoformat(date_str).timestamp() * 1000)


def _seed_session(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    title: str,
    time_created: int,
    turns: list[tuple[str, str]],  # (role, text)
    model_id: str = "synthetic/model",
    directory: str = "/synthetic/project",
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO project (id, worktree, vcs, time_created, time_updated, sandboxes) VALUES (?,?,?,?,?,?)",
        ("Px", directory, "git", time_created, time_created, "[]"),
    )
    conn.execute(
        "INSERT INTO session (id, project_id, slug, directory, title, version, time_created, time_updated) VALUES (?,?,?,?,?,?,?,?)",
        (session_id, "Px", session_id, directory, title, "0.0.0", time_created, time_created),
    )
    for i, (role, text) in enumerate(turns):
        msg_id = f"{session_id}_m{i}"
        msg_ts = time_created + i
        msg_data: dict = {"role": role}
        if role == "assistant":
            msg_data["modelID"] = model_id
        conn.execute(
            "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?)",
            (msg_id, session_id, msg_ts, msg_ts, json.dumps(msg_data)),
        )
        conn.execute(
            "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?,?)",
            (
                f"{msg_id}_p0",
                msg_id,
                session_id,
                msg_ts,
                msg_ts,
                json.dumps({"type": "text", "text": text}),
            ),
        )


@pytest.fixture
def export_db(tmp_path: Path, empty_template_db: Path) -> Path:
    db = tmp_path / "export.db"
    shutil.copyfile(empty_template_db, db)
    conn = sqlite3.connect(str(db))
    try:
        _seed_session(
            conn,
            session_id="S_chat",
            title="Synthetic chat",
            time_created=_ts("2026-06-01"),
            turns=[
                ("user", "synthetic question one"),
                ("assistant", "synthetic answer one"),
                ("user", "synthetic question two"),
            ],
        )
        # No user turn -> must be skipped.
        _seed_session(
            conn,
            session_id="S_nouser",
            title="Assistant only",
            time_created=_ts("2026-06-02"),
            turns=[("assistant", "no human here")],
        )
        # Subagent fan-out -> skipped by default.
        _seed_session(
            conn,
            session_id="S_sub",
            title="@explore subagent: look around",
            time_created=_ts("2026-06-03"),
            turns=[("user", "find things")],
        )
        conn.commit()
    finally:
        conn.close()
    return db


def test_render_markdown_contract():
    record = export.SessionExport(
        session_id="abc",
        title="My Session",
        date="2026-06-01",
        messages=[
            export.MessageTurn("user", "hi"),
            export.MessageTurn("assistant", "hello"),
        ],
        models_used=["synthetic/model"],
    )
    md = export.render_markdown(record)
    assert md.startswith("---\n")
    assert "source: opencode" in md
    assert 'date: "2026-06-01"' in md
    assert "## User" in md
    assert "## Assistant" in md


def test_export_writes_only_sessions_with_user_turns(export_db, tmp_path):
    out = tmp_path / "md"
    result = export.export_sessions(export_db, out)
    assert result["exported"] == 1
    assert result["scanned"] == 3
    files = list(out.glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "synthetic question one" in text
    assert "synthetic answer one" in text
    assert text.count("## User") == 2
    assert text.count("## Assistant") == 1


def test_export_include_subagent(export_db, tmp_path):
    out = tmp_path / "md"
    result = export.export_sessions(export_db, out, skip_subagent=False)
    # S_chat + S_sub (both have user turns); S_nouser still skipped.
    assert result["exported"] == 2


def test_export_since_filter(export_db, tmp_path):
    out = tmp_path / "md"
    result = export.export_sessions(export_db, out, since_ms=_ts("2026-06-02"))
    # S_chat (06-01) excluded by cutoff; remaining have no user turn / are subagent.
    assert result["exported"] == 0


def test_export_dry_run_writes_nothing(export_db, tmp_path):
    out = tmp_path / "md"
    result = export.export_sessions(export_db, out, dry_run=True)
    assert result["exported"] == 1
    assert not out.exists()


def test_export_missing_db_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        export.export_sessions(tmp_path / "nope.db", tmp_path / "out")


def test_cli_export_json(export_db, tmp_path, capsys):
    out = tmp_path / "md"
    rc = cli.main([
        "--main", str(export_db),
        "--old-archive", "/nonexistent/path.db",
        "export",
        "--out", str(out),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["exported"] == 1
    assert payload["source"] == "opencode"
    assert len(payload["files"]) == 1


def test_cli_export_missing_db_returns_3(tmp_path, capsys):
    rc = cli.main([
        "--main", str(tmp_path / "missing.db"),
        "--old-archive", "/nonexistent/path.db",
        "export",
        "--out", str(tmp_path / "out"),
    ])
    assert rc == 3
