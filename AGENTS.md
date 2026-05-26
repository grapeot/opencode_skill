# OpenCode Data Skill

## What This Repo Is

This repository contains a Python package, CLI, tests, and one root AI skill for maintaining local OpenCode SQLite data. It supports read-only stats and query helpers plus explicit archive workflows that copy, verify, and then delete selected sessions from a source database.

This project handles local data management after sessions already exist. Job submission, batch runners, OpenCode server orchestration, and other write-side tooling are outside this repo.

## Working Environment

Use a project-local `.venv` managed by `uv`.

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/python -m pytest -q
```

Do not create or commit `.env`, local database files, logs, cache directories, or exported session content. Use `.env.example` for fake configuration examples.

## Code Boundaries

`src/opencode_skill/` is the reusable logic layer. The CLI should stay thin: argument parsing, path resolution, library calls, and output formatting.

Mutation workflows must preserve the safety model:

1. Select sessions only through an explicit selector.
2. Run a read-only `plan` before `apply`.
3. Copy selected rows to the destination database.
4. Verify destination row counts before deleting from the source.
5. Keep destructive operations behind explicit confirmation.

## Public Repo Privacy

This repository is intended to be public. Keep examples synthetic and generic. Do not add real OpenCode session IDs, prompt/message dumps, personal filesystem paths, backup paths, token totals, operational logs, API keys, passwords, or private server commands.

The public root skill is `skills/skill_opencode_data.md`. If a private workspace needs additional defaults or routing, keep that overlay in the private workspace's own skill/config directory rather than in this repo.

## Testing and Docs

After changes to library or CLI behavior, run the offline pytest suite. Update `docs/prd.md`, `docs/rfc.md`, or `docs/test.md` when behavior, architecture, or verification requirements change.
