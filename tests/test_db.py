from __future__ import annotations

import sqlite3

import pytest

from opencode_skill import db as dbmod


def test_open_main_ro_rejects_writes(src_db_with_sessions):
    conn = dbmod.open_main_ro(src_db_with_sessions)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM session")
    finally:
        conn.close()


def test_attach_detach(tmp_path, fresh_dst_db, src_db_with_sessions):
    conn = sqlite3.connect(str(src_db_with_sessions))
    try:
        dbmod.attach(conn, fresh_dst_db, "dst", readonly=False)
        n_main = conn.execute("SELECT COUNT(*) FROM main.session").fetchone()[0]
        n_dst = conn.execute("SELECT COUNT(*) FROM dst.session").fetchone()[0]
        assert n_main == 6
        assert n_dst == 0
        dbmod.detach(conn, "dst")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("SELECT * FROM dst.session")
    finally:
        conn.close()


def test_init_archive_schema_from_template(tmp_path, empty_template_db):
    target = tmp_path / "out.db"
    dbmod.init_archive_schema_from_template(empty_template_db, target)
    conn = sqlite3.connect(str(target))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    # The bootstrap-style fixture must include all the runtime-required tables.
    assert {"session", "message", "part", "project"}.issubset(tables)
