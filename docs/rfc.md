# RFC: OpenCode Skill

Status: Draft

This document describes the architecture, CLI surface, HTTP client, batch renderer, database assumptions, and safety model. Product behavior and success criteria live in `docs/prd.md`.

## Architecture

```text
src/opencode_skill/db.py
  SQLite connection helpers, attach/detach helpers, schema bootstrap copy

src/opencode_skill/client.py
  Minimal OpenCode HTTP client using Basic auth and environment configuration

src/opencode_skill/jobs.py
  Single-session submission and append workflows

src/opencode_skill/batch.py
  Batch spec discovery, template rendering, QA grouping, manifests, and rate limiting

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

## Environment Configuration

The CLI reads `.env` from the repository root when present and otherwise respects the process environment. Public examples use fake values.

Submission configuration:

- `OPENCODE_BASE_URL`: OpenCode HTTP server URL
- `OPENCODE_USERNAME`: Basic auth username
- `OPENCODE_PASSWORD`: Basic auth password
- `OPENCODE_MODEL`: optional default model or provider/model pair
- `OPENCODE_PROVIDER`: optional default provider
- `OPENCODE_AGENT`: optional default agent
- `OPENCODE_MESSAGE_TIMEOUT`: HTTP timeout for sending messages

Database maintenance configuration:

- `OPENCODE_MAIN_DB`
- `OPENCODE_ARCHIVE_DB`
- `OPENCODE_OLD_ARCHIVE_DB`
- `OPENCODE_EMPTY_TEMPLATE_DB`

Credentials, ports, preferred model names, and preferred agent names belong in `.env` or a private workspace overlay, not in committed public docs.

## HTTP Client

The public client talks to the OpenCode server with Basic auth and a small endpoint set. It reads process env plus a CWD `.env` file, accepts explicit constructor overrides for tests, and raises typed exceptions without including credentials in error messages:

- `POST /session` to create a session
- `POST /session/{id}/message` to send a prompt or append a follow-up prompt
- `GET /session/{id}` to inspect session metadata
- `GET /session/{id}/message` to inspect messages for smoke verification
- `DELETE /session/{id}` to delete ephemeral sessions
- `GET /provider` to help diagnose model/provider mismatches

The client should raise typed exceptions with HTTP status and response body snippets, while never logging credentials.

## Single Job Submission

`submit` accepts prompt text as an argument, from `--prompt-file`, or from `--stdin`. It creates one session, sends one prompt, returns after handoff by default, and deletes or preserves the session according to explicit flags.

Default behavior should be safe for automation and auditability: preserve sessions unless `--delete-session` is passed, and do not block on long-running OpenCode work. If the HTTP message request times out during handoff, the command should still return the created session ID with `status=submitted_timeout`; this lets schedulers treat session creation as the durable handoff boundary. A user can choose `--wait` for blocking jobs.

Model strings can be passed as `provider/model` or as a model ID with a separate `--provider`. Provider inference is a convenience only; explicit provider wins.

`submit --dry-run` validates the same HTTP submission path while replacing the user's prompt with a built-in harmless prompt. The flow is:

1. Require exactly one prompt source so the command shape matches a real future submission.
2. Create a session titled with a `[dry-run]` prefix.
3. Send the fixed prompt that tells OpenCode to perform no task and reply exactly `OK`.
4. Wait for session completion regardless of the default handoff behavior.
5. Read session messages and require the latest assistant text to equal `OK` after whitespace trimming.
6. Delete the dry-run session by default, unless `--keep-dry-run-session` is passed for debugging.

The dry run intentionally performs a small network side effect because it is a connectivity and agent-routing preflight. It must not send the real prompt or inspect the user's workspace.

## Existing Session Append

`append` accepts prompt text as an argument, from `--prompt-file`, or from `--stdin`, plus a required `--session-id`. It sends the prompt to an existing session via `POST /session/{id}/message`, returns after handoff by default, and can block with `--wait` when the caller needs completion semantics.

`append --dry-run` uses the same prompt-source validation as a real append command, but it does not send the real prompt to the target session. The flow is:

1. Require exactly one prompt source so the command shape matches a real future append.
2. Fetch `GET /session/{target_id}` to verify the target session is reachable.
3. Create an ephemeral session titled with a `[dry-run] append <target_id>` prefix.
4. Send the fixed `OK` prompt to the ephemeral dry-run session with the requested model/provider/agent.
5. Wait for completion, require the latest assistant text to equal `OK`, and delete the dry-run session by default.

This gives schedulers a preflight for credentials, target-session reachability, and model/agent routing without polluting the target session history. If a caller needs a true live append probe, it should use a clearly harmless real prompt and accept that it becomes part of the target session.

## Inferring The Current Session

OpenCode does not always expose the current session ID in the agent process environment. When an agent needs to append back into its current session and no explicit `--session-id` is available, it can use the local SQLite database as a best-effort inference source.

The safe inference pattern is read-only and uses the `session` table, not message bodies:

```sql
SELECT id, title, directory, time_created, time_updated
FROM session
WHERE directory = :current_working_directory
ORDER BY time_updated DESC
LIMIT 8;
```

Use the top candidate only when multiple signals agree: the directory equals the current workspace, `time_updated` is close to the current interaction, and the title matches the user's current task. If several active sessions in the same directory have similar update times, require an explicit session ID. After choosing a candidate, verify it with `GET /session/{id}` before appending. Do not read or print prompt/message bodies unless the user explicitly asks for content inspection.

## Batch Submission

`batch submit` renders one prompt per Markdown spec file. It supports:

- `--template` or `--template-dir`
- `--specs` file or directory
- `--output-root`
- `--dry-run`
- `--smoke-slug`
- `--slugs`
- `--var KEY=VALUE`
- `--rate-limit`
- `--send-timeout`
- `--verify`

Every run writes a manifest and rendered prompts. The manifest is the audit source for retries and follow-up verification, and generated artifacts are ignored by git.

`batch qa` renders one prompt per group from `--slugs` or `--slugs-from-manifest`. It supports group size, custom QA templates, output root, dry-run, wait, and the same model/agent configuration as submit.

Batch title patterns should default to a `batch-` prefix so later archive selectors can target batch sessions. The prefix is a public convention, not a private workspace rule.

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

## Session Export

`export_sessions` reads the main database read-only and emits one markdown file per session. For each session it loads ordered user/assistant turns: message rows supply the role and model id from their JSON `data`, and the human-visible text comes from `part` rows of type `text`, concatenated in time order. Sessions with no user turn are skipped, as are subagent fan-out sessions whose titles match known subagent patterns unless the caller opts in.

The output format is a stable contract for date-indexed downstream consumers rather than an internal dump. Each file opens with a YAML frontmatter block that always includes `source: opencode` and a `date` field (the session creation date), followed by `## User` and `## Assistant` section headers. A consumer can therefore select sessions by date and split turns by header without reading the database. The export module owns its own small markdown renderer and filename helpers so the public repository carries no dependency on any private export pipeline.

## CLI Surface

```bash
python -m opencode_skill submit --prompt-file prompt.md --title "Synthetic Job" --model example/default-model
python -m opencode_skill submit --prompt-file prompt.md --title "Synthetic Job" --model example/default-model --dry-run
python -m opencode_skill submit "Synthetic prompt"
python -m opencode_skill submit --prompt-file prompt.md --wait
python -m opencode_skill append --session-id ses_example --prompt-file followup.md --model example/default-model
python -m opencode_skill append --session-id ses_example --prompt-file followup.md --dry-run --json
python -m opencode_skill batch submit --template template.md --specs specs/ --output-root tmp/batch_runs --dry-run
python -m opencode_skill batch qa --slugs alpha,beta --output-root tmp/batch_runs --group-size 2 --dry-run
python -m opencode_skill stats --json
python -m opencode_skill plan --selector title --prefix batch-
python -m opencode_skill plan --selector ids --file session_ids.txt
python -m opencode_skill plan --selector time --before 30d
python -m opencode_skill apply --selector ids --file session_ids.txt --dest ~/.local/share/opencode/opencode_archive.db --confirm
python -m opencode_skill apply --selector title --prefix batch- --dest ~/.local/share/opencode/opencode_archive.db --confirm --no-delete
python -m opencode_skill vacuum-main --confirm
python -m opencode_skill export --out tmp/sessions --since 30d --dry-run
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

The offline test suite uses a schema-only fixture database, synthetic rows, fake HTTP sessions, and fake OpenCode clients. It should cover client auth and payloads, single-job preserve/delete behavior, batch rendering, manifests, dry-run network avoidance, send timeouts, selector behavior, read-only connections, copy/verify/delete migration semantics, idempotency, descendant expansion, query aggregation across main plus archive databases, and session export rendering and filtering.

Manual validation against a real OpenCode installation is intentionally outside CI and must not write real session content into repository files.

## Open Questions

- Whether to add a first-class backup command around SQLite's backup API.
- Whether to make environment variable configuration part of the CLI itself or keep path configuration explicit through CLI flags.
- Whether future OpenCode schema changes should be handled dynamically through `PRAGMA table_info` rather than fixed column lists.
