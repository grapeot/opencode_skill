# OpenCode Skill: Agent Reference

## When To Use

Use this skill when a user asks you to submit a prompt to OpenCode, run a template-driven batch, group QA work by slug, inspect local OpenCode SQLite data, archive sessions, compact a database, or query token usage across main and archive databases.

This skill does not start or stop OpenCode servers and does not define private prompt policy. Use the user's `.env` or private overlay for endpoint, credential, model, agent, template, and routing defaults.

## Prerequisites

- Working directory: repository root, alongside `pyproject.toml`
- Python environment: project `.venv/` created with `uv`
- Package installed with `uv pip install --python .venv/bin/python -e '.[dev]'`
- HTTP submission configured through `.env` or explicit CLI flags
- Local database paths configured through CLI flags or `.env`
- User has confirmed any operation that mutates a real database

OpenCode prompts, databases, manifests, messages, tool outputs, project paths, tokens, and session IDs can be sensitive. Treat command output and runtime artifacts as private unless the user explicitly says otherwise.

## Commands

All commands run from the project root.

```bash
.venv/bin/python -m opencode_skill submit --prompt-file prompt.md --title "Synthetic Job" --model example/default-model
.venv/bin/python -m opencode_skill submit --prompt-file prompt.md --title "Synthetic Job" --model example/default-model --dry-run
.venv/bin/python -m opencode_skill submit "Synthetic prompt" --no-wait

.venv/bin/python -m opencode_skill batch submit --template template.md --specs specs/ --output-root tmp/batch_runs --dry-run
.venv/bin/python -m opencode_skill batch submit --template template.md --specs specs/ --output-root tmp/batch_runs --smoke-slug sample
.venv/bin/python -m opencode_skill batch qa --slugs alpha,beta --output-root tmp/batch_runs --group-size 2 --dry-run

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

## Submission Workflow

For one-off jobs, prefer `--prompt-file` or `--stdin` over inline prompt text. Run `submit --dry-run` before scheduling or otherwise delaying a real submission; it sends only a built-in OK prompt, verifies the assistant response, and deletes the dry-run session by default. The default real-submit behavior preserves the session for auditability. Use `--delete-session` only when the user explicitly wants an ephemeral session removed after submission or wait completion.

For batch jobs, run `--dry-run` first. Inspect the manifest and rendered prompts under the configured output root. Use `--smoke-slug` for one real submission before submitting a larger set. Batch session titles must start with `batch-`, which keeps later archive selectors auditable.

`batch qa` accepts slugs directly or from a prior manifest. Use `--group-size` to control how much work each QA session receives. Generated manifests and rendered prompts are runtime artifacts and should stay out of git.

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

`submit` prints the session ID, status, deletion state, and dry-run state, or a JSON object with the same fields when `--json` is passed. For `submit --dry-run`, success means the assistant response was exactly `OK`.

`batch submit` and `batch qa` write a `batch_manifest.json` and rendered prompt files under the output root. `--dry-run` prints a small JSON summary and avoids network calls.

`stats --json` returns a JSON object with a `databases` list. Each entry reports label, path, existence, size, and table counts when available.

`plan` prints the selector description, resolved session count, message count, part count, and sample titles. Treat sample titles as potentially private. Do not paste them into public logs.

`apply` prints copy counts, verification counts, and delete counts. A verification failure must leave the source database unchanged.

## Safety Rules

- Never run `apply` from a vague request without first producing a `plan`.
- Never commit `.env`, database files, WAL sidecars, logs, generated manifests, rendered prompts, exported sessions, or real operation reports.
- Never print prompt/message body content during a privacy review; report paths and categories instead.
- Never copy private endpoints, model names, agent names, templates, session IDs, or local paths into this public repo.
- Use `submit --dry-run` before scheduling a single future OpenCode submission.
- Use `--dry-run` and `--smoke-slug` before a large batch submission.

## Acceptance Criteria

A task using this skill is complete when:

1. Submission dry-runs or manifests were inspected before large network side effects.
2. Destructive database selections were planned before mutation.
3. Destination database copies were verified before deletion.
4. Any destructive step required explicit confirmation.
5. Tests or an equivalent verification command passed afterward.
6. No private OpenCode content was written to repository docs or logs.
