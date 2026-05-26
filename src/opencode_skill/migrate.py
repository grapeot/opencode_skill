from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import db as dbmod
from .selector import Selector


BATCH_SIZE = 500


@dataclass
class PlanReport:
    session_count: int
    message_count: int
    part_count: int
    sample_titles: list[str]


@dataclass
class CopyReport:
    session_ids: list[str]
    sessions_inserted: int
    messages_inserted: int
    parts_inserted: int


@dataclass
class VerifyReport:
    ok: bool
    issues: list[str]
    src_session_count: int
    dst_session_count: int
    src_message_count: int
    dst_message_count: int
    src_part_count: int
    dst_part_count: int


def plan(src_db: Path, sel: Selector) -> PlanReport:
    conn = dbmod.open_main_ro(src_db)
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM session WHERE {sel.where_clause}", sel.params)
        session_count = cur.fetchone()[0]
        cur.execute(
            f"SELECT id FROM session WHERE {sel.where_clause}",
            sel.params,
        )
        ids = [r[0] for r in cur.fetchall()]
        message_count = _count_by_session_ids(conn, "message", ids)
        part_count = _count_by_session_ids(conn, "part", ids)

        cur.execute(
            f"SELECT title FROM session WHERE {sel.where_clause} LIMIT 10",
            sel.params,
        )
        titles = [r[0] for r in cur.fetchall()]
        return PlanReport(session_count, message_count, part_count, titles)
    finally:
        conn.close()


def copy(src_db: Path, dst_db: Path, sel: Selector) -> CopyReport:
    """Copy sessions matching selector + their dependencies from src to dst.

    Co-migrated: project rows referenced by selected sessions, all messages,
    all parts. Idempotent (INSERT OR IGNORE). Does NOT delete from src.

    Caller must have created dst_db from a bootstrap template
    (see db.init_archive_schema_from_template).
    """
    src_conn = sqlite3.connect(str(src_db))
    try:
        dbmod.attach(src_conn, dst_db, "dst", readonly=False)

        cur = src_conn.cursor()
        cur.execute(f"SELECT id FROM session WHERE {sel.where_clause}", sel.params)
        session_ids = [r[0] for r in cur.fetchall()]

        if not session_ids:
            dbmod.detach(src_conn, "dst")
            return CopyReport([], 0, 0, 0)

        sessions_before = _count_dst(src_conn, "session")
        messages_before = _count_dst(src_conn, "message")
        parts_before = _count_dst(src_conn, "part")

        # Co-migrate project rows first (FK target).
        project_cols = ", ".join(dbmod.PROJECT_COLUMNS)
        cur.execute(
            f"INSERT OR IGNORE INTO dst.project ({project_cols}) "
            f"SELECT {project_cols} FROM main.project "
            f"WHERE id IN (SELECT DISTINCT project_id FROM main.session WHERE {sel.where_clause})",
            sel.params,
        )

        session_cols = ", ".join(dbmod.SESSION_COLUMNS)
        cur.execute(
            f"INSERT OR IGNORE INTO dst.session ({session_cols}) "
            f"SELECT {session_cols} FROM main.session WHERE {sel.where_clause}",
            sel.params,
        )
        src_conn.commit()

        for batch in _batched(session_ids, BATCH_SIZE):
            ph = ",".join("?" * len(batch))
            msg_cols = ", ".join(dbmod.MESSAGE_COLUMNS)
            cur.execute(
                f"INSERT OR IGNORE INTO dst.message ({msg_cols}) "
                f"SELECT {msg_cols} FROM main.message WHERE session_id IN ({ph})",
                tuple(batch),
            )
            part_cols = ", ".join(dbmod.PART_COLUMNS)
            cur.execute(
                f"INSERT OR IGNORE INTO dst.part ({part_cols}) "
                f"SELECT {part_cols} FROM main.part WHERE session_id IN ({ph})",
                tuple(batch),
            )
            src_conn.commit()

        sessions_after = _count_dst(src_conn, "session")
        messages_after = _count_dst(src_conn, "message")
        parts_after = _count_dst(src_conn, "part")

        dbmod.detach(src_conn, "dst")

        return CopyReport(
            session_ids=session_ids,
            sessions_inserted=sessions_after - sessions_before,
            messages_inserted=messages_after - messages_before,
            parts_inserted=parts_after - parts_before,
        )
    finally:
        src_conn.close()


def verify(src_db: Path, dst_db: Path, sel: Selector) -> VerifyReport:
    """Verify dst contains every session matched by selector in src."""
    src_conn = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    dst_conn = sqlite3.connect(f"file:{dst_db}?mode=ro", uri=True)
    try:
        cur = src_conn.cursor()
        cur.execute(f"SELECT id FROM session WHERE {sel.where_clause}", sel.params)
        src_ids = {r[0] for r in cur.fetchall()}

        dcur = dst_conn.cursor()

        issues: list[str] = []

        if not src_ids:
            return VerifyReport(True, [], 0, 0, 0, 0, 0, 0)

        ph = ",".join("?" * len(src_ids))
        dcur.execute(
            f"SELECT id FROM session WHERE id IN ({ph})", tuple(src_ids)
        )
        dst_ids = {r[0] for r in dcur.fetchall()}
        missing = src_ids - dst_ids
        if missing:
            issues.append(f"{len(missing)} session(s) missing in dst: {sorted(missing)[:5]}")

        src_msg = _count_msg_by_ids(src_conn, src_ids)
        dst_msg = _count_msg_by_ids(dst_conn, src_ids)
        if src_msg != dst_msg:
            issues.append(f"message count mismatch: src={src_msg} dst={dst_msg}")

        src_part = _count_part_by_ids(src_conn, src_ids)
        dst_part = _count_part_by_ids(dst_conn, src_ids)
        if src_part != dst_part:
            issues.append(f"part count mismatch: src={src_part} dst={dst_part}")

        return VerifyReport(
            ok=not issues,
            issues=issues,
            src_session_count=len(src_ids),
            dst_session_count=len(dst_ids),
            src_message_count=src_msg,
            dst_message_count=dst_msg,
            src_part_count=src_part,
            dst_part_count=dst_part,
        )
    finally:
        src_conn.close()
        dst_conn.close()


def delete_from_src(src_db: Path, session_ids: Iterable[str]) -> dict[str, int]:
    """Delete given sessions (and their messages/parts) from src.

    Caller must have already verified dst.
    """
    ids = list(session_ids)
    if not ids:
        return {"sessions": 0, "messages": 0, "parts": 0}

    conn = sqlite3.connect(str(src_db))
    try:
        cur = conn.cursor()
        deleted_parts = 0
        deleted_messages = 0
        deleted_sessions = 0
        for batch in _batched(ids, BATCH_SIZE):
            ph = ",".join("?" * len(batch))
            cur.execute(f"DELETE FROM part WHERE session_id IN ({ph})", tuple(batch))
            deleted_parts += cur.rowcount
            cur.execute(f"DELETE FROM message WHERE session_id IN ({ph})", tuple(batch))
            deleted_messages += cur.rowcount
            cur.execute(f"DELETE FROM session WHERE id IN ({ph})", tuple(batch))
            deleted_sessions += cur.rowcount
            conn.commit()
        return {
            "sessions": deleted_sessions,
            "messages": deleted_messages,
            "parts": deleted_parts,
        }
    finally:
        conn.close()


# helpers

def _count_dst(conn: sqlite3.Connection, table: str) -> int:
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM dst.{table}")
    return cur.fetchone()[0]


def _count_by_session_ids(conn: sqlite3.Connection, table: str, ids: list[str]) -> int:
    if not ids:
        return 0
    total = 0
    for batch in _batched(ids, BATCH_SIZE):
        ph = ",".join("?" * len(batch))
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE session_id IN ({ph})", tuple(batch))
        total += cur.fetchone()[0]
    return total


def _count_msg_by_ids(conn: sqlite3.Connection, ids: set[str]) -> int:
    if not ids:
        return 0
    total = 0
    ids_list = list(ids)
    for batch in _batched(ids_list, BATCH_SIZE):
        ph = ",".join("?" * len(batch))
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM message WHERE session_id IN ({ph})", tuple(batch))
        total += cur.fetchone()[0]
    return total


def _count_part_by_ids(conn: sqlite3.Connection, ids: set[str]) -> int:
    if not ids:
        return 0
    total = 0
    ids_list = list(ids)
    for batch in _batched(ids_list, BATCH_SIZE):
        ph = ",".join("?" * len(batch))
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM part WHERE session_id IN ({ph})", tuple(batch))
        total += cur.fetchone()[0]
    return total


def _batched(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]
