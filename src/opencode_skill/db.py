from __future__ import annotations

import sqlite3
from pathlib import Path


def open_main_ro(db_path: Path) -> sqlite3.Connection:
    """Open the OpenCode main DB in read-only mode."""
    uri = f"file:{db_path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def open_writable(db_path: Path) -> sqlite3.Connection:
    """Open a DB read-write (creates if missing)."""
    return sqlite3.connect(str(db_path))


def attach(conn: sqlite3.Connection, db_path: Path, alias: str, *, readonly: bool = False) -> None:
    if readonly:
        conn.execute(f"ATTACH DATABASE 'file:{db_path}?mode=ro' AS {alias}")
    else:
        conn.execute(f"ATTACH DATABASE '{db_path}' AS {alias}")


def detach(conn: sqlite3.Connection, alias: str) -> None:
    conn.execute(f"DETACH DATABASE {alias}")


SESSION_COLUMNS = [
    "id", "project_id", "parent_id", "slug", "directory", "title", "version",
    "share_url", "summary_additions", "summary_deletions", "summary_files",
    "summary_diffs", "revert", "permission", "time_created", "time_updated",
    "time_compacting", "time_archived", "workspace_id", "path", "agent", "model",
]

# Verified against real schema 2026-05-09: message and part tables both
# have a time_updated column we missed in the static schema dump.
MESSAGE_COLUMNS = ["id", "session_id", "time_created", "time_updated", "data"]

PART_COLUMNS = ["id", "message_id", "session_id", "time_created", "time_updated", "data"]

# project rows referenced by sessions must be co-migrated (validated 2026-05-09).
# OpenCode server fails to bootstrap if a session's project_id has no row.
PROJECT_COLUMNS = [
    "id", "worktree", "vcs", "name", "icon_url", "icon_color",
    "time_created", "time_updated", "time_initialized", "sandboxes",
    "commands", "icon_url_override",
]


def init_archive_schema_from_template(template_db: Path, target_db: Path) -> None:
    """Copy a freshly-bootstrapped OpenCode DB as the archive starting point.

    OpenCode dev build creates the full 15-table schema on first launch.
    We use that as the source of truth instead of hand-written DDL — avoids
    column drift when OpenCode upstream adds fields.
    """
    import shutil
    shutil.copyfile(template_db, target_db)
