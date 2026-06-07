# OpenCode Data Skill

## When To Use

Use this skill when a user asks you to inspect local OpenCode SQLite data, archive sessions, compact a database, or query token/session data across main and archive databases.

For submitting OpenCode prompts or batches, use `skill_opencode_submission.md`. For recurring cron jobs that submit OpenCode prompts, use `skill_opencode_periodic_job.md`.

## Prerequisites

- Working directory: repository root, alongside `pyproject.toml`
- Python environment: project `.venv/` created with `uv`
- Package installed with `uv pip install --python .venv/bin/python -e '.[dev]'`
- Local database paths configured through CLI flags or `.env`
- User has confirmed any operation that mutates a real database

OpenCode databases can contain private prompts, messages, tool outputs, project paths, tokens, and session IDs. Treat command output and runtime artifacts as private unless the user explicitly says otherwise.

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

.venv/bin/python -m opencode_skill export --out OUTPUT_DIR --dry-run
.venv/bin/python -m opencode_skill export --out OUTPUT_DIR --since 30d
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

## Maintenance Workflow

For any destructive maintenance request, use this sequence:

1. Identify the source database and destination archive database.
2. Run `stats` to confirm the configured files exist.
3. Run `plan` with the intended selector.
4. Show the user the session/message/part counts and selector description.
5. Run `apply --confirm` only when the selection is explicit and accepted.
6. Use `--no-delete` first if the user wants a copy-and-verify dry run.
7. Use tests or a small verification query after the operation.

If OpenCode may be writing to the source database, stop and ask the user to close or stop the writer before mutation. `stats` and `plan` can run while the database exists, but `apply` and `vacuum-main` should run only under an intentional maintenance window.

## Selector Semantics

Selectors operate on sessions. Message and part rows are included only through the selected session IDs.

By default, CLI selectors expand descendants through the session `parent_id` chain. This keeps root sessions and subagent child sessions together. Use `--no-expand` only when the user explicitly wants the raw selector result.

## Output Contract

`stats --json` returns a JSON object with a `databases` list. Each entry reports label, path, existence, size, and table counts when available.

`plan` prints the selector description, resolved session count, message count, part count, and sample titles. Treat sample titles as potentially private. Do not paste them into public logs.

`apply` prints copy counts, verification counts, and delete counts. A verification failure must leave the source database unchanged.

`export` reads the source database read-only and writes one markdown file per session that has at least one user turn. It returns scanned and exported counts (and the written file paths under `--json`). Each file begins with a YAML frontmatter block carrying `source: opencode` and a `date` field, followed by `## User` and `## Assistant` sections. Subagent fan-out sessions are skipped unless `--include-subagent` is set; `--since` accepts a relative window (e.g. `30d`) or an ISO date. Treat exported markdown as private: it contains prompt and message bodies and must never be committed.

## Safety Rules

- Never run `apply` from a vague request without first producing a `plan`.
- Never run `vacuum-main` while OpenCode is writing to the database.
- Never commit `.env`, database files, WAL sidecars, logs, generated manifests, rendered prompts, exported sessions, or real operation reports.
- Never print prompt/message body content during a privacy review; report paths and categories instead.
- Never copy private endpoints, model names, agent names, session IDs, local paths, or database contents into this public repo.

## Acceptance Criteria

A task using this skill is complete when:

1. Destructive database selections were planned before mutation.
2. Destination database copies were verified before deletion.
3. Any destructive step required explicit confirmation.
4. Tests or an equivalent verification command passed afterward.
5. No private OpenCode content was written to repository docs or logs.
