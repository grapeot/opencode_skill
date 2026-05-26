# PRD: OpenCode Data Skill

This document defines the behavior and success criteria for a public, local-first OpenCode data maintenance tool. Implementation details live in `docs/rfc.md`.

## Background

OpenCode stores sessions, messages, parts, and project metadata in a local SQLite database. Over time that database can become large, especially when a workspace uses agents for both normal interactive work and high-volume automated jobs.

A useful maintenance tool needs to separate two concerns. First, recent interactive sessions should remain easy for OpenCode and other local tools to read. Second, archived or batch-like sessions should remain queryable for analytics and audit purposes. Moving data out of the primary database must preserve accounting completeness and avoid data loss.

## Users

The primary users are humans and AI agents who maintain their own local OpenCode installation. The tool assumes local filesystem access and treats OpenCode data as private.

## Goals

The tool should let a user or agent:

1. Inspect one or more OpenCode databases without mutation.
2. Select sessions by explicit criteria such as IDs, title prefix, or age.
3. Preview an archive operation before applying it.
4. Copy selected sessions and dependent rows to a destination database.
5. Verify the copy before deleting anything from the source database.
6. Query assistant-message token data across the primary database and archives.
7. Keep export and analytics callers explicit about whether archived sessions are included.

## Non-Goals

This project does not submit jobs to OpenCode, run batch queues, manage OpenCode server processes, sync databases across machines, provide a GUI, or delete exported Markdown session files. Those concerns belong to the user's local workspace or separate tools.

## Expected Behavior

`stats` is always read-only. It reports database existence, file size, and table counts for the configured source and archive databases.

`plan` is always read-only. It resolves a selector, optionally expands descendant sessions, then reports the session, message, and part counts that `apply` would affect.

`apply` copies selected sessions and related rows into the destination database, verifies that the destination contains the selected data, and only then deletes from the source. If verification fails, the source database remains unchanged.

`vacuum-main` is separate from `apply`. SQLite file compaction has different operational risks and should require its own explicit confirmation.

Query helpers should make accounting complete by default: analytics callers include archives unless they explicitly opt out. Export callers can request source-only behavior when they want archived sessions excluded.

## Safety Requirements

Any destructive command must require explicit confirmation. A failed copy or verification step must not remove source data. Selectors must be auditable from command arguments and plan output. Real production use should run against a backup or test copy before touching a user's primary OpenCode database.

## Success Criteria

The project is ready for public use when:

1. Offline tests pass using only synthetic fixtures.
2. The README and root skill explain safe installation, configuration, planning, application, and verification.
3. Public docs contain no real session content, private paths, secrets, or personal operational logs.
4. `.gitignore` blocks runtime data, local configuration, logs, build artifacts, and SQLite sidecars.
5. The package can be installed with `uv pip install -e '.[dev]'` and imported without path hacks.
