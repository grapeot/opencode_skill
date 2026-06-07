from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Sessions whose titles match these patterns are skipped by default. They are
# subagent/system fan-out turns that add noise to a personal session export.
_SUBAGENT_PATTERNS = (
    "@explore subagent",
    "@librarian subagent",
    "@oracle subagent",
    "@general subagent",
)


@dataclass
class MessageTurn:
    role: str  # 'user' or 'assistant'
    content: str
    time_created: int | None = None  # ms epoch of the turn's first message, if known


@dataclass
class SessionExport:
    session_id: str
    title: str
    date: str  # YYYY-MM-DD
    messages: list[MessageTurn] = field(default_factory=list)
    project_directory: str = ""
    models_used: list[str] = field(default_factory=list)

    @property
    def user_turn_count(self) -> int:
        return sum(1 for m in self.messages if m.role == "user")


def _ms_to_date(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000).date().isoformat()


def _ms_to_hhmm(epoch_ms: int) -> str:
    """Local-time HH:MM for a ms epoch (used in per-turn markdown headers)."""
    return datetime.fromtimestamp(epoch_ms / 1000).strftime("%H:%M")


def _sanitize_filename(title: str, max_length: int = 80) -> str:
    text = (title or "").strip()
    text = re.sub(r"[^A-Za-z0-9一-鿿]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        text = "untitled"
    return text[:max_length]


def _yaml_string(value: str) -> str:
    # JSON encoding produces a valid, quoted YAML scalar and escapes embedded
    # quotes/newlines, matching the format the eink_diary parser expects.
    return json.dumps(value, ensure_ascii=False)


def _unique_output_path(output_dir: Path, date_ymd: str, title: str) -> Path:
    prefix = date_ymd.replace("-", "")
    stem = f"{prefix}_{_sanitize_filename(title)}"
    candidate = output_dir / f"{stem}.md"
    counter = 2
    while candidate.exists():
        candidate = output_dir / f"{stem}_{counter}.md"
        counter += 1
    return candidate


def _should_skip_session(title: str | None) -> bool:
    text = (title or "").strip().lower()
    if not text:
        return False
    return any(pattern in text for pattern in _SUBAGENT_PATTERNS)


def load_session_messages(
    conn: sqlite3.Connection, session_id: str
) -> tuple[list[MessageTurn], list[str], int]:
    """Load ordered user/assistant turns for one session.

    Mirrors the OpenCode storage layout: message rows carry role/modelID in
    their JSON ``data``, and the human-visible text lives in ``part`` rows of
    type ``text``. Returns (messages, sorted models, user_turn_count).
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id,
               COALESCE(json_extract(data, '$.role'), ''),
               COALESCE(json_extract(data, '$.modelID'), ''),
               time_created
        FROM message
        WHERE session_id = ?
        ORDER BY time_created ASC
        """,
        (session_id,),
    )

    messages: list[MessageTurn] = []
    models: set[str] = set()
    user_count = 0

    for message_id, role, model_id, time_created in cursor.fetchall():
        if role not in {"user", "assistant"}:
            continue
        cursor.execute(
            """
            SELECT COALESCE(json_extract(data, '$.text'), '')
            FROM part
            WHERE session_id = ?
              AND message_id = ?
              AND json_extract(data, '$.type') = 'text'
            ORDER BY time_created ASC
            """,
            (session_id, message_id),
        )
        content = "".join(row[0] for row in cursor.fetchall()).strip()
        if not content:
            continue
        turn_time = int(time_created) if time_created is not None else None
        messages.append(MessageTurn(role=role, content=content, time_created=turn_time))
        if role == "user":
            user_count += 1
        if role == "assistant" and model_id:
            models.add(model_id)

    return messages, sorted(models), user_count


def render_markdown(session: SessionExport) -> str:
    """Render a session to markdown that eink_diary's ai_sessions source parses.

    Contract: a YAML frontmatter block carrying at least ``source`` and
    ``date``, then ``## User`` / ``## Assistant`` section headers.
    """
    lines: list[str] = [
        "---",
        "source: opencode",
        f"session_id: {_yaml_string(session.session_id)}",
        f"title: {_yaml_string(session.title)}",
        f"date: {_yaml_string(session.date)}",
        f"message_count: {len(session.messages)}",
    ]
    if session.project_directory:
        lines.append(f"project_directory: {_yaml_string(session.project_directory)}")
    if session.models_used:
        lines.append(f"models_used: {json.dumps(session.models_used, ensure_ascii=False)}")
    lines.extend(["---", "", f"# {session.title}", ""])

    for message in session.messages:
        section = "User" if message.role == "user" else "Assistant"
        if message.time_created:
            lines.append(f"## {section} [{_ms_to_hhmm(message.time_created)}]")
        else:
            lines.append(f"## {section}")
        lines.append("")
        lines.append(message.content.rstrip())
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def export_sessions(
    db_path: Path,
    output_dir: Path,
    *,
    since_ms: int | None = None,
    skip_subagent: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Export OpenCode sessions from a SQLite DB to markdown files.

    Read-only on the source DB. Sessions without any user turn (or matching a
    subagent title when ``skip_subagent``) are skipped. Returns a summary dict
    with scanned/exported counts and the written file paths.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"OpenCode database not found: {db_path}")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        sql = "SELECT id, title, directory, time_created FROM session"
        params: tuple[Any, ...] = ()
        if since_ms is not None:
            sql += " WHERE time_created > ?"
            params = (since_ms,)
        sql += " ORDER BY time_created ASC"

        rows = conn.execute(sql, params).fetchall()

        scanned = 0
        exported = 0
        written: list[str] = []
        if not dry_run:
            output_dir.mkdir(parents=True, exist_ok=True)

        for row in rows:
            scanned += 1
            session_id = row["id"]
            title = (row["title"] or "").strip() or "Untitled"
            session_time = int(row["time_created"] or 0)

            if skip_subagent and _should_skip_session(title):
                continue

            messages, models, user_count = load_session_messages(conn, session_id)
            if user_count == 0:
                continue

            record = SessionExport(
                session_id=session_id,
                title=title,
                date=_ms_to_date(session_time),
                messages=messages,
                project_directory=row["directory"] or "",
                models_used=models,
            )
            output_path = _unique_output_path(output_dir, record.date, record.title)
            if not dry_run:
                output_path.write_text(render_markdown(record), encoding="utf-8")
            written.append(str(output_path))
            exported += 1
    finally:
        conn.close()

    return {
        "source": "opencode",
        "scanned": scanned,
        "exported": exported,
        "files": written,
    }
