# RFC: OpenCode Data Skill

Status: Draft

This document describes the architecture, CLI surface, database assumptions, and safety model. Product behavior and success criteria live in `docs/prd.md`.

## Architecture

```text
src/opencode_skill/db.py
  SQLite connection helpers, attach/detach helpers, schema bootstrap copy

src/opencode_skill/selector.py
  Session selectors and descendant expansion over session.parent_id

src/opencode_skill/migrate.py
  plan, copy, verify, and delete primitives

src/opencode_skill/query.py
  read-only query helpers for analytics and stats across source + archives

src/opencode_skill/cli.py
  thin argparse entrypoint for humans and AI agents
```

`scripts/` may contain shell wrappers, but reusable behavior belongs in `src/opencode_skill/`.

## Database Model

OpenCode owns the runtime SQLite database. This tool assumes a schema with session, message, part, and project tables. It does not try to abstract away all upstream schema changes; instead, tests and schema checks should fail visibly when OpenCode changes a relevant table.

The archive database should start from an empty OpenCode database template rather than a hand-written subset of tables. A full template avoids column drift and preserves table availability for tools that expect OpenCode's complete schema.

When sessions move, their referenced project rows move with them. Messages and parts are selected by session ID. Selectors never operate directly on message or part rows.

## Selectors

The selector abstraction produces three values:

- `where_clause`: a SQL fragment applied to the `session` table
- `params`: SQLite parameters for the fragment
- `description`: human-readable text for plan output

Supported selector families:

- explicit session ID list
- session ID file
- title prefix
- root sessions older than a cutoff, via the CLI time selector

`expand_with_descendants` resolves a selector to session IDs and adds all recursive children through the `parent_id` chain. This is the default CLI behavior because subagent sessions can be children of a selected root session. `--no-expand` exists for cases where the caller has already selected the exact intended session set.

## Migration Flow

`plan` opens the source database read-only, resolves the selector, and reports selected session, message, and part counts plus sample titles. It does not mutate any database.

`apply` follows this sequence:

1. Require `--confirm`.
2. Initialize the destination database from the bootstrap fixture if needed.
3. Resolve and optionally expand the selector.
4. Copy project, session, message, and part rows with idempotent inserts.
5. Verify destination row counts for selected session IDs.
6. Delete parts, messages, and sessions from the source only after verification passes.

If the copy phase fails, the destination may contain partial extra rows and the source remains unchanged. If verification fails, the source remains unchanged. If deletion fails after verification, re-running with the same selector should be safe because copy operations are idempotent.

## Query Behavior

`iter_assistant_messages` yields assistant messages from the main database and, by default, every existing archive database passed to it. This default preserves complete token accounting. Callers that intentionally want source-only behavior can pass `include_archive=False`.

The query layer parses message JSON in Python rather than relying on SQLite JSON extraction. A single malformed message row should not abort an analytics run.

## CLI Surface

```bash
python -m opencode_skill stats --json
python -m opencode_skill plan --selector title --prefix batch-
python -m opencode_skill plan --selector ids --file session_ids.txt
python -m opencode_skill plan --selector time --before 30d
python -m opencode_skill apply --selector ids --file session_ids.txt --dest ~/.local/share/opencode/opencode_archive.db --confirm
python -m opencode_skill apply --selector title --prefix batch- --dest ~/.local/share/opencode/opencode_archive.db --confirm --no-delete
python -m opencode_skill vacuum-main --confirm
```

Global path options:

```bash
--main PATH
--archive PATH
--old-archive PATH
```

Selectors:

```bash
--selector ids --file PATH
--selector title --prefix PREFIX
--selector time --before 30d|YYYY-MM-DD|YYYY-MM-DDTHH:MM:SS
--no-expand
```

## Safety and Privacy

OpenCode databases can contain private prompts, messages, project paths, tool outputs, and token metadata. Public docs and tests must use synthetic examples only. Runtime database files, WAL sidecars, `.env`, logs, and exported session content stay out of git.

For real maintenance, run against a backup or disposable copy first. Stop OpenCode or otherwise ensure it is not writing to the source database before destructive operations. This project does not manage server lifecycle.

## Test Strategy

The offline test suite uses a schema-only fixture database and synthetic rows. It should cover selector behavior, read-only connections, copy/verify/delete migration semantics, idempotency, descendant expansion, and query aggregation across main plus archive databases.

Manual validation against a real OpenCode installation is intentionally outside CI and must not write real session content into repository files.

## Open Questions

- Whether to add a first-class backup command around SQLite's backup API.
- Whether to make environment variable configuration part of the CLI itself or keep path configuration explicit through CLI flags.
- Whether future OpenCode schema changes should be handled dynamically through `PRAGMA table_info` rather than fixed column lists.
