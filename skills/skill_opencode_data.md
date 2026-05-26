# OpenCode Data Skill: Agent Reference

## When To Use

Use this skill when a user asks you to inspect, archive, migrate, compact, or query local OpenCode SQLite data. Typical requests include checking database size, planning an archive of old or batch-like sessions, copying sessions to an archive database, or reading token usage across main and archive databases.

Do not use this skill for submitting OpenCode jobs, running batch queues, starting OpenCode servers, or reading private session contents for unrelated purposes.

## Prerequisites

- Working directory: repository root, alongside `pyproject.toml`
- Python environment: project `.venv/` created with `uv`
- Package installed with `uv pip install --python .venv/bin/python -e '.[dev]'`
- Local database paths configured through CLI flags or `.env`
- User has confirmed any operation that mutates a real database

OpenCode databases may contain prompts, messages, tool outputs, project paths, tokens, and other private metadata. Treat database paths and query outputs as sensitive.

## Commands

All commands run from the project root.

```bash
.venv/bin/python -m opencode_skill stats --json

.venv/bin/python -m opencode_skill plan --selector title --prefix batch-
.venv/bin/python -m opencode_skill plan --selector ids --file session_ids.txt
.venv/bin/python -m opencode_skill plan --selector time --before 30d

.venv/bin/python -m opencode_skill apply --selector ids --file session_ids.txt --dest ~/.local/share/opencode/opencode_archive.db --confirm
.venv/bin/python -m opencode_skill apply --selector title --prefix batch- --dest ~/.local/share/opencode/opencode_archive.db --confirm --no-delete

.venv/bin/python -m opencode_skill vacuum-main --confirm
```

Optional global database path flags:

```bash
--main PATH
--archive PATH
--old-archive PATH
```

A convenience wrapper may also be available after installation:

```bash
scripts/opencode-skill stats --json
```

## Workflow

For any destructive maintenance request, use this sequence:

1. Identify the source database and destination archive database.
2. Run `stats` to confirm the configured files exist.
3. Run `plan` with the intended selector.
4. Show the user the session/message/part counts and selector description.
5. Run `apply --confirm` only when the selection is explicit and accepted.
6. Use `--no-delete` first if the user wants a copy-and-verify dry run.
7. Run tests or a small verification query after the operation.

If OpenCode may be writing to the source database, stop and ask the user to close or stop the writer before mutation. `stats` and `plan` can run while the database exists, but `apply` and `vacuum-main` should run only under an intentional maintenance window.

## Selector Semantics

Selectors operate on sessions. Message and part rows are included only through the selected session IDs.

By default, CLI selectors expand descendants through the session `parent_id` chain. This keeps root sessions and subagent child sessions together. Use `--no-expand` only when the user explicitly wants the raw selector result.

## Output Contract

`stats --json` returns a JSON object with a `databases` list. Each entry reports label, path, existence, size, and table counts when available.

`plan` prints the selector description, resolved session count, message count, part count, and sample titles. Treat sample titles as potentially private. Do not paste them into public logs.

`apply` prints copy counts, verification counts, and delete counts. A verification failure must leave the source database unchanged.

## Safety Rules

- Never run `apply` from a vague request such as "clean up old stuff" without first producing a `plan`.
- Never commit `.env`, database files, WAL sidecars, logs, exported sessions, or real operation reports.
- Never print prompt/message body content during a privacy review; report paths and categories instead.
- Never move job submission or batch runner tooling into this repo unless the user explicitly changes the project boundary.

## Acceptance Criteria

A maintenance task using this skill is complete when:

1. The user-visible selection was planned before mutation.
2. The destination database was copied and verified before deletion.
3. Any destructive step required explicit confirmation.
4. Tests or an equivalent verification command passed afterward.
5. No private OpenCode content was written to repository docs or logs.
