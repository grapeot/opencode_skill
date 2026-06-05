# OpenCode Submission Skill

## When To Use

Use this skill when a user asks you to submit one prompt to OpenCode, run a template-driven batch of OpenCode sessions, or group QA work by slug.

This skill does not start or stop OpenCode servers and does not define private prompt policy. Use the user's `.env` or private overlay for endpoint, credential, model, agent, template, and routing defaults.

For recurring cron jobs, use `skill_opencode_periodic_job.md`. For SQLite stats, archive, compaction, or local session data maintenance, use `skill_opencode_data.md`.

## Prerequisites

- Working directory: repository root, alongside `pyproject.toml`
- Python environment: project `.venv/` created with `uv`
- Package installed with `uv pip install --python .venv/bin/python -e '.[dev]'`
- HTTP submission configured through `.env` or explicit CLI flags

OpenCode prompts, manifests, messages, tool outputs, project paths, and session IDs can be sensitive. Treat command output and runtime artifacts as private unless the user explicitly says otherwise.

## Commands

All commands run from the project root.

```bash
.venv/bin/python -m opencode_skill submit --prompt-file prompt.md --title "Synthetic Job" --model example/default-model
.venv/bin/python -m opencode_skill submit --prompt-file prompt.md --title "Synthetic Job" --model example/default-model --dry-run
.venv/bin/python -m opencode_skill submit "Synthetic prompt"
.venv/bin/python -m opencode_skill submit --prompt-file prompt.md --wait

.venv/bin/python -m opencode_skill batch submit --template template.md --specs specs/ --output-root tmp/batch_runs --dry-run
.venv/bin/python -m opencode_skill batch submit --template template.md --specs specs/ --output-root tmp/batch_runs --smoke-slug sample
.venv/bin/python -m opencode_skill batch qa --slugs alpha,beta --output-root tmp/batch_runs --group-size 2 --dry-run
```

## Single Submission Workflow

For one-off jobs, prefer `--prompt-file` or `--stdin` over inline prompt text. Run `submit --dry-run` before scheduling or otherwise delaying a real submission; it sends only a built-in OK prompt, verifies the assistant response, and deletes the dry-run session by default.

The default real-submit behavior returns after handoff and preserves the session for auditability. Use `--wait` only when the caller intentionally wants to block until OpenCode reports completion. Use `--delete-session` only when the user explicitly wants an ephemeral session removed after submission or wait completion.

## Batch Submission Workflow

For batch jobs, run `--dry-run` first. Inspect the manifest and rendered prompts under the configured output root. Use `--smoke-slug` for one real submission before submitting a larger set. Batch session titles must start with `batch-`, which keeps later archive selectors auditable.

`batch qa` accepts slugs directly or from a prior manifest. Use `--group-size` to control how much work each QA session receives. Generated manifests and rendered prompts are runtime artifacts and should stay out of git.

## Output Contract

`submit` prints the session ID, status, deletion state, and dry-run state, or a JSON object with the same fields when `--json` is passed. Default handoff statuses include `submitted`, `submitted_timeout`, and `submitted_unconfirmed`; all preserve the session ID for follow-up. For `submit --dry-run`, success means the assistant response was exactly `OK`.

`batch submit` and `batch qa` write a `batch_manifest.json` and rendered prompt files under the output root. `--dry-run` prints a small JSON summary and avoids network calls.

## Safety Rules

- Prefer `--prompt-file` or `--stdin` for private prompts.
- Use `submit --dry-run` before scheduling a single future OpenCode submission.
- Use `--dry-run` and `--smoke-slug` before a large batch submission.
- Never commit `.env`, logs, generated manifests, rendered prompts, exported sessions, or real operation reports.
- Never print prompt/message body content during a privacy review; report paths and categories instead.
- Never copy private endpoints, model names, agent names, templates, session IDs, or local paths into this public repo.

## Acceptance Criteria

A task using this skill is complete when:

1. Submission dry-runs or manifests were inspected before large network side effects.
2. Real submissions returned a session ID or a batch manifest that can be audited later.
3. Tests or an equivalent verification command passed afterward.
4. No private OpenCode content was written to repository docs or logs.
