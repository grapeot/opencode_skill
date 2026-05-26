from __future__ import annotations

import sqlite3

import pytest

from opencode_skill import migrate, selector


def _counts(db_path):
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    r = (
        c.execute("SELECT COUNT(*) FROM session").fetchone()[0],
        c.execute("SELECT COUNT(*) FROM message").fetchone()[0],
        c.execute("SELECT COUNT(*) FROM part").fetchone()[0],
        c.execute("SELECT COUNT(*) FROM project").fetchone()[0],
    )
    c.close()
    return r


def test_plan_counts_correctly(src_db_with_sessions):
    sel = selector.from_session_ids(["S_A", "S_A1"])
    p = migrate.plan(src_db_with_sessions, sel)
    assert p.session_count == 2
    assert p.message_count == 2
    assert p.part_count == 4


def test_copy_then_verify_then_delete_full_round_trip(
    src_db_with_sessions, fresh_dst_db
):
    src = src_db_with_sessions
    dst = fresh_dst_db

    root_sel = selector.from_title_prefix("batch-")
    expanded = selector.expand_with_descendants(src, root_sel)
    # Roots: S_A, S_C; descendants of S_A: S_A1, S_A2, S_A2x → 5 total
    assert len(expanded.params) == 5

    # COPY
    cp = migrate.copy(src, dst, expanded)
    assert cp.sessions_inserted == 5
    assert cp.messages_inserted == 5
    assert cp.parts_inserted == 10

    # project also co-migrated
    s_dst = _counts(dst)
    assert s_dst == (5, 5, 10, 1)

    # VERIFY
    v = migrate.verify(src, dst, expanded)
    assert v.ok, v.issues

    # DELETE from src
    d = migrate.delete_from_src(src, expanded.params)
    assert d == {"sessions": 5, "messages": 5, "parts": 10}

    # src now has only S_B left
    s_src = _counts(src)
    assert s_src == (1, 1, 2, 1)


def test_copy_idempotent_on_rerun(src_db_with_sessions, fresh_dst_db):
    src = src_db_with_sessions
    dst = fresh_dst_db
    sel = selector.from_session_ids(["S_B"])

    cp1 = migrate.copy(src, dst, sel)
    assert cp1.sessions_inserted == 1
    cp2 = migrate.copy(src, dst, sel)  # second run on same dst
    # No new rows because PK collision
    assert cp2.sessions_inserted == 0
    assert cp2.messages_inserted == 0
    assert cp2.parts_inserted == 0

    # dst still consistent
    assert _counts(dst)[:3] == (1, 1, 2)


def test_verify_detects_missing_data(src_db_with_sessions, fresh_dst_db):
    src = src_db_with_sessions
    dst = fresh_dst_db
    # Don't copy. Verify should report mismatch.
    sel = selector.from_session_ids(["S_A"])
    v = migrate.verify(src, dst, sel)
    assert not v.ok
    assert any("missing" in s for s in v.issues)


def test_descendant_selector_keeps_subtree_intact(
    src_db_with_sessions, fresh_dst_db
):
    """If S_A is moved, S_A2x must move too — no orphan grandkids in src."""
    src = src_db_with_sessions
    dst = fresh_dst_db

    sel = selector.from_session_ids(["S_A"])
    expanded = selector.expand_with_descendants(src, sel)
    migrate.copy(src, dst, expanded)
    v = migrate.verify(src, dst, expanded)
    assert v.ok
    migrate.delete_from_src(src, expanded.params)

    # No descendant of S_A* should remain in src.
    c = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    leftover = c.execute(
        "SELECT id FROM session WHERE id IN ('S_A','S_A1','S_A2','S_A2x')"
    ).fetchall()
    c.close()
    assert leftover == []


def test_delete_empty_id_list_is_noop(src_db_with_sessions):
    r = migrate.delete_from_src(src_db_with_sessions, [])
    assert r == {"sessions": 0, "messages": 0, "parts": 0}


def test_message_part_co_migration_includes_time_updated(
    src_db_with_sessions, fresh_dst_db
):
    """Regression: time_updated column must be in MESSAGE/PART_COLUMNS."""
    src = src_db_with_sessions
    dst = fresh_dst_db
    sel = selector.from_session_ids(["S_B"])
    cp = migrate.copy(src, dst, sel)
    assert cp.messages_inserted == 1
    assert cp.parts_inserted == 2

    c = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
    msg_t = c.execute("SELECT time_updated FROM message WHERE session_id=?", ("S_B",)).fetchone()
    part_t = c.execute("SELECT time_updated FROM part WHERE session_id=?", ("S_B",)).fetchone()
    c.close()
    assert msg_t[0] is not None
    assert part_t[0] is not None
