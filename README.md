# OpenCode Data Skill

OpenCode Data Skill is a local CLI and agent skill for inspecting and maintaining OpenCode's SQLite data. It helps AI agents archive selected sessions, keep token analytics complete across multiple local databases, and run safe plan-before-apply maintenance workflows.

This project is deliberately narrow. It manages local OpenCode data after sessions exist. It does not submit OpenCode jobs, operate batch runners, start or stop OpenCode servers, or manage remote infrastructure.

This repository is designed to be publishable with only fake examples. Runtime data, `.env`, logs, archive databases, and real operational notes must stay outside git.

## Install

Hand this repository URL to an AI coding agent and ask it to install the skill in your workspace. The agent should start from your workspace `AGENTS.md`, `CLAUDE.md`, or equivalent instructions, then add the public root skill at `skills/skill_opencode_data.md` to your skill discovery chain.

For a direct local install:

```bash
git clone <this-repo-url> opencode_skill
cd opencode_skill
uv venv .venv
uv pip install --python .venv/bin/python -e '.[dev]'
cp .env.example .env
```

Then edit `.env` if your OpenCode database paths differ from the defaults. The example values are placeholders and contain no secrets.

## Configure

The default paths follow OpenCode's common local data layout under `~/.local/share/opencode/`. You can override them with CLI flags or environment variables documented in `.env.example`.

The important safety rule is simple: run `plan` first, inspect the counts, then run `apply --confirm` only when the selected sessions are exactly the ones you intend to archive. Mutation commands should run only when OpenCode is not writing to the source database.

## For AI Agents

When a user asks you to inspect, archive, or query OpenCode local data, read `skills/skill_opencode_data.md` first. That file is the agent-facing contract: supported commands, safety boundaries, output expectations, and verification checks.

Useful commands from the project root:

```bash
.venv/bin/python -m opencode_skill stats --json
.venv/bin/python -m opencode_skill plan --selector title --prefix batch-
.venv/bin/python -m opencode_skill apply --selector title --prefix batch- --dest ~/.local/share/opencode/opencode_archive.db --confirm
.venv/bin/python -m opencode_skill vacuum-main --confirm
```

Prefer explicit `--main`, `--archive`, and `--dest` paths when working outside the default OpenCode layout. Never infer a destructive selector from vague language; use `plan` output to make the selection auditable.

## For Developers

The reusable code lives in `src/opencode_skill/`. The CLI is a thin argparse layer over the package. Tests use synthetic SQLite fixtures and do not require a real OpenCode database.

```bash
.venv/bin/python -m pytest -q
```

The main design documents are:

- `docs/prd.md`: behavior, users, scope, and success criteria
- `docs/rfc.md`: architecture, CLI surface, database invariants, and safety model
- `docs/test.md`: unit, integration, and manual validation strategy

## Local Data

The tool reads and writes SQLite database files that you point it at. A typical local layout is:

```text
~/.local/share/opencode/
├── opencode.db
├── opencode_archive.db
└── archive/
    └── old_sessions.db
```

Those files may contain private prompts, messages, paths, tool outputs, tokens, and project metadata. They must not be committed.
