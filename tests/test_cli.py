from __future__ import annotations

import json
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


def test_parse_cutoff_invalid_raises():
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


def test_apply_requires_confirm(src_db_with_sessions, fresh_dst_db):
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


def test_cli_batch_dry_run(tmp_path, capsys):
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "alpha.md").write_text("# alpha\n", encoding="utf-8")
    template = tmp_path / "template.md"
    template.write_text("slug={{SLUG}}", encoding="utf-8")

    rc = cli.main([
        "--old-archive", "/nonexistent/path.db",
        "batch",
        "submit",
        "--template", str(template),
        "--specs", str(specs),
        "--output-root", str(tmp_path / "out"),
        "--dry-run",
        "--batch-id", "cli001",
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "submit"


def test_cli_submit_uses_prompt_file_and_preserves_session_by_default(tmp_path, monkeypatch, capsys):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Synthetic prompt", encoding="utf-8")

    class FakeClient:
        def create_session(self, _title):
            return "ses_cli"

        def send_message(self, *_args, **_kwargs):
            return {"ok": True}

        def wait_for_session_complete(self, *_args, **_kwargs):
            return True

        def delete_session(self, *_args, **_kwargs):
            raise AssertionError("delete_session should not be called by default")

    monkeypatch.setattr(cli, "OpenCodeClient", FakeClient)

    rc = cli.main([
        "submit",
        "--prompt-file", str(prompt_file),
        "--title", "Synthetic Job",
        "--model", "provider/model",
        "--json",
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["session_id"] == "ses_cli"
    assert payload["status"] == "submitted"
    assert payload["deleted"] is False


def test_cli_submit_dry_run_ignores_real_prompt_and_verifies_ok(tmp_path, monkeypatch, capsys):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Real task should not be sent", encoding="utf-8")

    class FakeClient:
        def __init__(self):
            self.sent_messages = []

        def create_session(self, title):
            assert title == "[dry-run] Synthetic Job"
            return "ses_dry"

        def send_message(self, session_id, message, **_kwargs):
            assert session_id == "ses_dry"
            self.sent_messages.append(message)
            assert "Real task should not be sent" not in message
            return {"ok": True}

        def wait_for_session_complete(self, *_args, **_kwargs):
            return True

        def get_session_messages(self, *_args, **_kwargs):
            return [{"info": {"role": "assistant"}, "parts": [{"text": "OK"}]}]

        def delete_session(self, *_args, **_kwargs):
            return True

    monkeypatch.setattr(cli, "OpenCodeClient", FakeClient)

    rc = cli.main([
        "submit",
        "--prompt-file", str(prompt_file),
        "--title", "Synthetic Job",
        "--model", "provider/model",
        "--dry-run",
        "--json",
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["session_id"] == "ses_dry"
    assert payload["status"] == "dry_run_ok_deleted"
    assert payload["dry_run"] is True
    assert payload["verification"] == "assistant_replied_ok"


def test_cli_submit_dry_run_returns_2_on_verification_failure(tmp_path, monkeypatch, capsys):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Real task should not be sent", encoding="utf-8")

    class FakeClient:
        def create_session(self, _title):
            return "ses_dry"

        def send_message(self, *_args, **_kwargs):
            return {"ok": True}

        def wait_for_session_complete(self, *_args, **_kwargs):
            return True

        def get_session_messages(self, *_args, **_kwargs):
            return [{"info": {"role": "assistant"}, "parts": [{"text": "NO"}]}]

        def delete_session(self, *_args, **_kwargs):
            return True

    monkeypatch.setattr(cli, "OpenCodeClient", FakeClient)

    rc = cli.main([
        "submit",
        "--prompt-file", str(prompt_file),
        "--title", "Synthetic Job",
        "--model", "provider/model",
        "--dry-run",
    ])

    assert rc == 2
    assert "dry run failed" in capsys.readouterr().err
