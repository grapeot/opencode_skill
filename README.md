# OpenCode Skill

OpenCode Skill is a local-first CLI and agent skill for submitting work to a user-controlled OpenCode HTTP server and maintaining OpenCode's SQLite data afterward. It supports single prompt submission, template-driven batch submission, batch QA grouping, read-only stats, plan-before-apply archiving, and database compaction.

This repository is designed to be publishable with only fake examples. Runtime data, `.env`, logs, generated manifests, rendered prompts, archive databases, and real operational notes must stay outside git.

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

Then edit `.env` for your local OpenCode server and database paths. The example values are placeholders and contain no secrets.

## Configure

Submission uses `OPENCODE_BASE_URL`, `OPENCODE_USERNAME`, `OPENCODE_PASSWORD`, and optional model/agent variables from `.env` or the process environment. Prefer `--prompt-file` or `--stdin` for private prompts so shell history does not capture sensitive text.

Database maintenance defaults follow OpenCode's common local data layout under `~/.local/share/opencode/`. You can override them with CLI flags or environment variables documented in `.env.example`.

The maintenance safety rule is simple: run `plan` first, inspect the counts, then run `apply --confirm` only when the selected sessions are exactly the ones you intend to archive. Mutation commands should run only when OpenCode is not writing to the source database.

## Commands

Single submission:

```bash
.venv/bin/python -m opencode_skill submit --prompt-file prompt.md --title "Synthetic Job" --model example/default-model
.venv/bin/python -m opencode_skill submit --prompt-file prompt.md --title "Synthetic Job" --model example/default-model --dry-run
.venv/bin/python -m opencode_skill submit "Summarize this synthetic fixture"
.venv/bin/python -m opencode_skill submit --prompt-file prompt.md --wait
```

Use `submit --dry-run` before putting a future submission behind a scheduler. It validates the server, credentials, model, provider, and agent path by sending a built-in harmless prompt that must return exactly `OK`; it does not send the prompt file content and deletes the dry-run session by default.

Real `submit` returns after handoff by default and preserves the session for auditability. Use `--wait` only when the caller intentionally wants to block until OpenCode reports the session is no longer running.

Batch submission and QA:

```bash
.venv/bin/python -m opencode_skill batch submit --template template.md --specs specs/ --output-root tmp/batch_runs --dry-run
.venv/bin/python -m opencode_skill batch submit --template template.md --specs specs/ --output-root tmp/batch_runs --smoke-slug sample
.venv/bin/python -m opencode_skill batch qa --slugs alpha,beta,gamma --output-root tmp/batch_runs --group-size 2 --dry-run
```

SQLite maintenance:

```bash
.venv/bin/python -m opencode_skill stats --json
.venv/bin/python -m opencode_skill plan --selector title --prefix batch-
.venv/bin/python -m opencode_skill apply --selector title --prefix batch- --dest ~/.local/share/opencode/opencode_archive.db --confirm
.venv/bin/python -m opencode_skill vacuum-main --confirm
```

A convenience wrapper is also available after installation:

```bash
scripts/opencode-skill stats --json
```

Prefer explicit `--main`, `--archive`, and `--dest` paths when working outside the default OpenCode layout. Never infer a destructive selector from vague language; use `plan` output to make the selection auditable.

## For AI Agents

When a user asks you to submit, batch, inspect, archive, or query OpenCode data, read `skills/skill_opencode_data.md` first. That file is the agent-facing contract: supported commands, safety boundaries, output expectations, and verification checks.

Use public mechanics from this repo and private defaults from the user's own `.env` or overlay. Do not copy private prompts, endpoints, model names, agent names, session IDs, manifests, database paths, or logs into public files.

## For Developers

The reusable code lives in `src/opencode_skill/`. The CLI is a thin argparse layer over the package. Tests use fake HTTP sessions, fake clients, and synthetic SQLite fixtures. They do not require network access, a real OpenCode server, or a real OpenCode database.

```bash
.venv/bin/python -m pytest -q
```

The main design documents are:

- `docs/prd.md`: behavior, users, scope, and success criteria
- `docs/rfc.md`: architecture, CLI surface, HTTP client, batch manifests, database invariants, and safety model
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
