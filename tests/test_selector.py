from __future__ import annotations

import pytest

from opencode_skill import selector


def test_from_session_ids_basic():
    s = selector.from_session_ids(["a", "b", "c"])
    assert "id IN (?,?,?)" in s.where_clause
    assert s.params == ("a", "b", "c")


def test_from_session_ids_rejects_empty():
    with pytest.raises(ValueError):
        selector.from_session_ids([])


def test_from_id_file(tmp_path):
    p = tmp_path / "ids.txt"
    p.write_text("session_1\nsession_2\n\n  session_3  \n")
    s = selector.from_id_file(p)
    assert s.params == ("session_1", "session_2", "session_3")


def test_from_title_prefix():
    s = selector.from_title_prefix("batch-")
    assert s.where_clause == "title LIKE ?"
    assert s.params == ("batch-%",)


def test_expand_includes_root_and_all_descendants(src_db_with_sessions):
    src = src_db_with_sessions
    sel = selector.from_title_prefix("batch-")
    expanded = selector.expand_with_descendants(src, sel)
    # batch-foo (S_A) drags S_A1, S_A2, S_A2x; batch-bar (S_C) has no kids
    assert set(expanded.params) == {"S_A", "S_A1", "S_A2", "S_A2x", "S_C"}


def test_expand_no_match_returns_unmatchable_selector(src_db_with_sessions):
    src = src_db_with_sessions
    sel = selector.from_title_prefix("nonexistent-")
    expanded = selector.expand_with_descendants(src, sel)
    # selector should be valid but match nothing
    import sqlite3
    c = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    n = c.execute(f"SELECT COUNT(*) FROM session WHERE {expanded.where_clause}", expanded.params).fetchone()[0]
    c.close()
    assert n == 0


def test_expand_id_only_no_children(src_db_with_sessions):
    src = src_db_with_sessions
    sel = selector.from_session_ids(["S_C"])
    expanded = selector.expand_with_descendants(src, sel)
    assert set(expanded.params) == {"S_C"}


def test_expand_grandchildren_included(src_db_with_sessions):
    src = src_db_with_sessions
    sel = selector.from_session_ids(["S_A"])
    expanded = selector.expand_with_descendants(src, sel)
    # S_A → S_A1, S_A2 → S_A2x
    assert set(expanded.params) == {"S_A", "S_A1", "S_A2", "S_A2x"}
