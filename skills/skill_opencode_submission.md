# OpenCode Submission Skill

## When To Use

Use this skill when a user asks you to submit one prompt to OpenCode, append a follow-up prompt to an existing OpenCode session, run a template-driven batch of OpenCode sessions, or group QA work by slug.

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
.venv/bin/python -m opencode_skill append --session-id ses_example --prompt-file followup.md --model example/default-model
.venv/bin/python -m opencode_skill append --session-id ses_example --prompt-file followup.md --dry-run --json

.venv/bin/python -m opencode_skill batch submit --template template.md --specs specs/ --output-root tmp/batch_runs --dry-run
.venv/bin/python -m opencode_skill batch submit --template template.md --specs specs/ --output-root tmp/batch_runs --smoke-slug sample
.venv/bin/python -m opencode_skill batch qa --slugs alpha,beta --output-root tmp/batch_runs --group-size 2 --dry-run
```

## Single Submission Workflow

For one-off jobs, prefer `--prompt-file` or `--stdin` over inline prompt text. Run `submit --dry-run` before scheduling or otherwise delaying a real submission; it sends only a built-in OK prompt, verifies the assistant response, and deletes the dry-run session by default.

The default real-submit behavior returns after handoff and preserves the session for auditability. Use `--wait` only when the caller intentionally wants to block until OpenCode reports completion. Use `--delete-session` only when the user explicitly wants an ephemeral session removed after submission or wait completion.

For future-dated OpenCode submissions, keep the prompt file under an ignored stable directory such as `prompts/` in this repository or an equivalent private execution repo. Do not put scheduled prompt files under `tmp/`, because cleanup jobs may remove them before the delayed command runs. Do not put operational prompts in long-term research/report directories unless the prompt itself is part of the user-facing artifact. Process Launcher may own the delayed process lifecycle and logs, but the prompt file should live with the OpenCode submission workflow that consumes it.

## Existing Session Append Workflow

Use `append` when the desired behavior is to bump or continue an existing OpenCode session instead of creating a new one. Prefer `--prompt-file` or `--stdin` for private follow-up prompts:

```bash
.venv/bin/python -m opencode_skill append --session-id ses_example --prompt-file prompts/reminder.md --send-timeout 5 --json
```

Run `append --dry-run` before scheduling a future append. The dry run verifies that the target session is reachable, then creates an ephemeral dry-run session and sends only the built-in `OK` prompt there. It does not append test content to the target session.

If the user asks for the current session and no session ID is exposed in the environment, infer it from the OpenCode SQLite `session` table using read-only metadata. Query sessions for the current working directory, sort by `time_updated DESC`, and use the top candidate only when the directory, recent update time, and title all match the current interaction. If multiple sessions in the same directory are active or the title/time signals disagree, ask for an explicit session ID instead of guessing. After choosing a candidate, verify it with `GET /session/{id}` or `append --dry-run` before a real append. Do not inspect or print message bodies for this inference unless the user explicitly asks.

Example metadata query:

```sql
SELECT id, title, directory, time_created, time_updated
FROM session
WHERE directory = :current_working_directory
ORDER BY time_updated DESC
LIMIT 8;
```

## Same-Session Reminder Workflow

Use this workflow when the user wants the current OpenCode conversation to wake itself up later, such as "in 10 minutes, check this command again". This is different from creating a new OpenCode job. The scheduled action should append a follow-up prompt to the same session.

1. Identify the target session. Prefer an explicit session id. If none is available, infer it from read-only session metadata as described above. Use the inferred session only when directory, title, and recent update time all match the current interaction.
2. Write the reminder prompt to a stable ignored file, such as `prompts/reminder_<slug>.md`. Do not store scheduled reminder prompts under `tmp/`, because cleanup jobs may delete them before the reminder fires.
3. Run `append --dry-run` against the target session. This checks credentials, model routing, agent routing, and session reachability without appending the real reminder text.
4. Schedule the real append through Process Launcher with `delay_seconds` or `run_at`. Prefer non-blocking handoff mode for reminders: use `--send-timeout 5 --json` and do not pass `--wait` unless the user explicitly wants the scheduled process to block until OpenCode completes.
5. After scheduling, inspect `/scheduled` and record the scheduled job id if the caller needs cancellation or later audit. At fire time, verify both layers when needed: Process Launcher should show whether the append command exited successfully, while the OpenCode session timeline is the source of truth for whether the model actually responded.

The default same-session reminder command should look like this:

```bash
curl -X POST http://localhost:7997/run \
  -H 'Content-Type: application/json' \
  -d '{
    "command": ["/absolute/path/to/opencode_skill/.venv/bin/python", "-m", "opencode_skill", "append", "--session-id", "ses_example", "--prompt-file", "/absolute/path/to/prompts/reminder.md", "--send-timeout", "5", "--json"],
    "cwd": "/absolute/path/to/opencode_skill",
    "label": "opencode_append_reminder",
    "delay_seconds": 1800,
    "timeout": 300
  }'
```

Avoid this common failure mode: appending with `--wait --send-timeout 30` can succeed in the OpenCode session but still make the scheduled process exit non-zero if the HTTP request times out while the model is responding. That leaves Process Launcher marked `failed` even though the user sees the reminder response in the session. For reminders, treat message handoff as the durable scheduler's responsibility and OpenCode response verification as a separate check.

## Batch Submission Workflow

For batch jobs, run `--dry-run` first. Inspect the manifest and rendered prompts under the configured output root. Use `--smoke-slug` for one real submission before submitting a larger set. Batch session titles must start with `batch-`, which keeps later archive selectors auditable.

When waiting for concurrent jobs, use the server's aggregate `GET /session/status` map as the source of truth for busy/idle state. `GET /session/{id}` may omit reliable running/status fields and can make a caller wait incorrectly or declare completion too early. Long-running orchestrators also need a per-job watchdog; a multi-hour wave-level timeout alone allows one stuck job to block every completed job.

`batch qa` accepts slugs directly or from a prior manifest. Use `--group-size` to control how much work each QA session receives. Generated manifests and rendered prompts are runtime artifacts and should stay out of git.

## Output Contract

`submit` prints the session ID, status, deletion state, and dry-run state, or a JSON object with the same fields when `--json` is passed. Default handoff statuses include `submitted`, `submitted_timeout`, and `submitted_unconfirmed`; all preserve the session ID for follow-up. For `submit --dry-run`, success means the assistant response was exactly `OK`.

`append` prints the target session ID, status, and dry-run state. For real appends, `session_id` is the target session. For `append --dry-run`, `session_id` is the ephemeral dry-run session and `target_session_id` is the session that was checked.

`batch submit` and `batch qa` write a `batch_manifest.json` and rendered prompt files under the output root. `--dry-run` prints a small JSON summary and avoids network calls.

## Safety Rules

- Prefer `--prompt-file` or `--stdin` for private prompts.
- Use `submit --dry-run` before scheduling a single future OpenCode submission.
- Use `append --dry-run` before scheduling a future append to an existing session.
- Store scheduled prompt files in an ignored stable `prompts/` directory, not `tmp/`.
- Use `--dry-run` and `--smoke-slug` before a large batch submission.
- Poll `GET /session/status` for concurrent wait state; do not infer busy/idle from `GET /session/{id}`.
- Set a bounded per-job watchdog and handle timed-out jobs through targeted retry rather than blocking an entire wave indefinitely.
- Never commit `.env`, logs, generated manifests, rendered prompts, exported sessions, or real operation reports.
- Never print prompt/message body content during a privacy review; report paths and categories instead.
- Never copy private endpoints, model names, agent names, templates, session IDs, or local paths into this public repo.

## Acceptance Criteria

A task using this skill is complete when:

1. Submission dry-runs or manifests were inspected before large network side effects.
2. Real submissions returned a session ID or a batch manifest that can be audited later.
3. Tests or an equivalent verification command passed afterward.
4. No private OpenCode content was written to repository docs or logs.
