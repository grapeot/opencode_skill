from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class Selector:
    where_clause: str
    params: tuple[object, ...]
    description: str


def from_session_ids(ids: Sequence[str]) -> Selector:
    if not ids:
        raise ValueError("empty id list")
    placeholders = ",".join("?" * len(ids))
    return Selector(
        where_clause=f"id IN ({placeholders})",
        params=tuple(ids),
        description=f"session id list (n={len(ids)})",
    )


def from_id_file(path: Path) -> Selector:
    ids = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    return from_session_ids(ids)


def from_title_prefix(prefix: str) -> Selector:
    """Match sessions whose title starts with `prefix`. e.g. 'batch-'."""
    return Selector(
        where_clause="title LIKE ?",
        params=(f"{prefix}%",),
        description=f"title prefix '{prefix}'",
    )


def expand_with_descendants(db_path: Path, sel: Selector) -> Selector:
    """Resolve `sel` to session ids, then add all descendant sessions (parent_id chain).

    Why: batch sessions launch subagents (e.g. look_at: visual verification). Migrating
    only the parent leaves orphan subagents whose parent_id points nowhere. We want
    the whole subtree to move together.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT id FROM session WHERE {sel.where_clause}", sel.params)
        roots = {r[0] for r in cur.fetchall()}
        if not roots:
            return from_session_ids(["__never_matches__"])

        all_ids = set(roots)
        frontier = roots
        # BFS over parent_id graph until no more children
        while frontier:
            placeholders = ",".join("?" * len(frontier))
            cur.execute(
                f"SELECT id FROM session WHERE parent_id IN ({placeholders})",
                tuple(frontier),
            )
            next_frontier = {r[0] for r in cur.fetchall()} - all_ids
            all_ids |= next_frontier
            frontier = next_frontier
        return from_session_ids(sorted(all_ids))
    finally:
        conn.close()
