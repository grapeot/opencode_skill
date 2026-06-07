# PRD: OpenCode Skill

This document defines the behavior and success criteria for a public, local-first OpenCode skill. Implementation details live in `docs/rfc.md`.

## Background

OpenCode has two common local automation surfaces. The first is the HTTP server used to create sessions and send agent prompts, including follow-up prompts to an existing session. The second is the SQLite data store that keeps sessions, messages, parts, and project metadata. A useful package should cover both surfaces while exposing separate agent skill entry points for submission, data maintenance, and recurring scheduling.

Submission workflows create local sessions. Maintenance workflows inspect, archive, and compact the databases after those sessions exist. Keeping these in one public package makes the workflow easier for AI agents while preserving clear skill boundaries: submit work, record manifests, later archive or analyze the resulting sessions.

## Users

The primary users are humans and AI agents who maintain their own local OpenCode installation. The tool assumes local filesystem and HTTP access to a user-controlled OpenCode server. Prompts, session IDs, database paths, and manifests are private by default.

## Goals

The tool should let a user or agent:

1. Submit one prompt to an OpenCode server with explicit model, provider, agent, title, wait, and keep/delete behavior.
2. Validate single-prompt submission configuration with a harmless dry run before scheduling or other delayed execution.
3. Append one follow-up prompt to an existing session with explicit session ID, model, provider, agent, and wait behavior.
4. Validate append configuration with a harmless dry run that does not pollute the target session.
5. Submit many rendered prompts from spec files with dry-run, smoke-run, rate limiting, short send timeout, and manifest output.
6. Submit QA groups from a slug list or prior manifest.
7. Inspect one or more OpenCode databases without mutation.
8. Select sessions by explicit criteria such as IDs, title prefix, or age.
9. Preview an archive operation before applying it.
10. Copy selected sessions and dependent rows to a destination database.
11. Verify the copy before deleting anything from the source database.
12. Query assistant-message token data across the primary database and archives.
13. Keep export and analytics callers explicit about whether archived sessions are included.

## Non-Goals

This project does not start or stop OpenCode servers, define project-specific prompt content, provide a GUI, sync databases across machines, or delete exported Markdown session files. Private workspace overlays can define preferred models, agents, ports, aliases, and project workflows.

## Expected Behavior

`submit` creates one session, sends one prompt from inline text, stdin, or a prompt file, and returns after handoff by default. It preserves the session unless `--delete-session` is explicitly passed. Callers that need blocking behavior can pass `--wait` to wait until OpenCode reports the session is no longer running. Credentials and server URLs come from environment variables or `.env`; no public file contains real credentials.

`submit --dry-run` validates the same server, credential, model, provider, and agent path without sending the user's real prompt. It creates an ephemeral session, sends a fixed OK-only prompt, waits for completion, verifies that the final assistant response is exactly `OK`, and deletes the dry-run session by default. This is the preferred preflight before putting a future `submit` command behind a scheduler.

`append` sends one prompt to `POST /session/{id}/message` for an existing session. It should preserve the session, return after handoff by default, and report `submitted_timeout` as a possible successful handoff signal because OpenCode may accept the message before the HTTP request returns. `append --dry-run` verifies the target session is reachable, then runs the same harmless `OK` dry-run flow in an ephemeral session instead of writing a test message into the target session.

`batch submit` discovers Markdown spec files, renders a template for each spec, writes rendered prompts and a manifest, and optionally submits them to OpenCode with rate limiting. `--dry-run` must perform all rendering and manifest work without network calls.

`batch qa` groups slugs from a list or previous manifest, renders QA prompts, writes a manifest, and optionally submits one session per group.

`stats` is always read-only. It reports database existence, file size, and table counts for the configured source and archive databases.

`plan` is always read-only. It resolves a selector, optionally expands descendant sessions, then reports the session, message, and part counts that `apply` would affect.

`apply` copies selected sessions and related rows into the destination database, verifies that the destination contains the selected data, and only then deletes from the source. If verification fails, the source database remains unchanged.

`vacuum-main` is separate from `apply`. SQLite file compaction has different operational risks and should require its own explicit confirmation.

`export` is always read-only on the source database. It writes one markdown file per session that has at least one user turn, using a stable frontmatter-plus-section format (`source: opencode`, a `date` field, then `## User` / `## Assistant` sections) so date-indexed downstream tools can match sessions and extract turns. It supports a creation-time cutoff, optional inclusion of subagent fan-out sessions, and a dry run. Exported markdown can contain private prompts and messages and is treated as runtime data that stays out of git.

Query helpers should make accounting complete by default: analytics callers include archives unless they explicitly opt out. Export callers can request source-only behavior when they want archived sessions excluded.

## Safety Requirements

Any destructive command must require explicit confirmation. A failed copy or verification step must not remove source data. Selectors must be auditable from command arguments and plan output. Real production use should run against a backup or test copy before touching a user's primary OpenCode database.

Submission commands must make side effects visible. Batch rendering dry runs must avoid network calls. Single-submit and append dry runs may call the OpenCode server, but must send only the built-in harmless prompt, wait for `OK`, and delete the test session by default. Append dry runs must avoid writing test content into the target session. Batch commands must write manifests so a user can audit which prompts were rendered, which sessions were created, and which submissions need follow-up. Network errors should preserve enough detail for debugging without printing credentials. Public defaults must stay synthetic; private endpoint, model, agent, and template policy belongs in `.env` or a private overlay.

## Success Criteria

The project is ready for public use when:

1. Offline tests pass using only synthetic fixtures.
2. The README and public skill files explain safe installation, configuration, single submission, batch submission, planning, application, and verification.
3. Public docs contain no real session content, private paths, secrets, or personal operational logs.
4. `.gitignore` blocks runtime data, local configuration, logs, build artifacts, and SQLite sidecars.
5. The package can be installed with `uv pip install --python .venv/bin/python -e '.[dev]'` and imported without path hacks.
6. Global workspace skills route OpenCode job submission, batch submission, recurring scheduling, and data maintenance to the appropriate public skill file or a private overlay that references it.
