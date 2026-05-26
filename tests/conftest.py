from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

# Make the in-repo src/ importable without installing the package.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FIXTURES = ROOT / "tests" / "fixtures"
EMPTY_DB = FIXTURES / "empty_opencode.db"


@pytest.fixture
def empty_template_db() -> Path:
    """Path to the OpenCode-bootstrapped empty DB. Don't write to it."""
    assert EMPTY_DB.exists(), f"missing fixture: {EMPTY_DB}"
    return EMPTY_DB


@pytest.fixture
def fresh_dst_db(tmp_path: Path, empty_template_db: Path) -> Path:
    """A writable copy of the empty bootstrap DB to use as archive target."""
    dst = tmp_path / "archive.db"
    shutil.copyfile(empty_template_db, dst)
    return dst


@pytest.fixture
def src_db_with_sessions(tmp_path: Path, empty_template_db: Path):
    """Build a src DB with deterministic test data and return (path, helpers).

    Layout:
      project P1
      sessions:
        S_A (root, title="batch-foo", project=P1)
          ├── S_A1 (parent=S_A, title="look_at: a1")
          └── S_A2 (parent=S_A, title="look_at: a2")
              └── S_A2x (parent=S_A2, title="look_at: a2x")
        S_B (root, title="some other session", project=P1)
        S_C (root, title="batch-bar", project=P1) -- no children
    Each session has 1 message and 2 parts.
    """
    src = tmp_path / "src.db"
    shutil.copyfile(empty_template_db, src)

    conn = sqlite3.connect(str(src))
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO project (id, worktree, vcs, time_created, time_updated, sandboxes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("P1", "/tmp/test_project", "git", 1000, 2000, "[]"),
    )

    sessions = [
        ("S_A",   None,  "batch-foo",         1100),
        ("S_A1",  "S_A", "look_at: a1",       1110),
        ("S_A2",  "S_A", "look_at: a2",       1120),
        ("S_A2x", "S_A2","look_at: a2x",      1125),
        ("S_B",   None,  "some other session", 1200),
        ("S_C",   None,  "batch-bar",         1300),
    ]
    for sid, parent, title, t in sessions:
        cur.execute(
            """
            INSERT INTO session (id, project_id, parent_id, slug, directory, title,
                                 version, time_created, time_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (sid, "P1", parent, sid.lower(), "/tmp/test_project", title,
             "0.0.0-test", t, t + 100),
        )

    for sid, _, _, t in sessions:
        msg_id = f"msg_{sid}"
        cur.execute(
            "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?)",
            (msg_id, sid, t + 10, t + 10, '{"role":"user"}'),
        )
        for k in (0, 1):
            cur.execute(
                "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?,?)",
                (f"prt_{sid}_{k}", msg_id, sid, t + 10, t + 10, f'{{"text":"p{k}"}}'),
            )

    conn.commit()
    conn.close()
    return src
