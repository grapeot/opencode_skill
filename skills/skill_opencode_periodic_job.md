# OpenCode Periodic Job Skill

## When To Use

Use this skill when a user asks to schedule a recurring OpenCode job, automate an OpenCode prompt on a daily/weekly/monthly cadence, test a cron-triggered OpenCode submission, or maintain a prompt file that cron submits through the OpenCode Skill CLI.

For one-shot delayed OpenCode jobs, prefer `process_launcher.md` durable scheduled jobs instead of cron. Still use this skill for recurring cron cadence.

This skill supports macOS and Linux only. It relies on user crontab and does not cover Windows Task Scheduler, systemd timers, launchd plists, Kubernetes cron jobs, or hosted schedulers.

## Goal

Create an auditable periodic job that submits a prompt to the user's OpenCode server through this repository's CLI, with the prompt stored in a file, the environment loaded from a known project directory, the crontab backed up before every change, and a short live test performed before declaring the schedule ready.

## Required Inputs

Before editing crontab, pin down these values in concrete terms:

- Schedule intent: natural language and cron expression, including timezone assumptions.
- Prompt purpose: what OpenCode should do when the job fires.
- Prompt file path: absolute path to a UTF-8 text or Markdown file.
- OpenCode Skill repository path: absolute path containing `pyproject.toml`, `.venv/`, and `.env`.
- Job title: a stable title or prefix that makes the created OpenCode sessions recognizable.
- Output log path: absolute path for stdout/stderr from the cron command.

If the user only gives a natural-language schedule, translate it and repeat the cron expression back before writing it. For example, "every Thursday at 9:30 PM" becomes `30 21 * * 4` in the machine's local timezone.

## Prompt File Contract

Cron should submit a file, not inline prompt text. Store the prompt somewhere stable, preferably a `prompts/` directory near the project or workflow it operates on. The file is part of the job definition: maintain it, test it manually, and only then connect it to cron.

The prompt should be written as a complete instruction to a fresh OpenCode session. Include the working directory, expected output, success criteria, and any notification requirement. When useful, ask the OpenCode job to send a completion email through the user's email skill or another available notification skill. Keep private prompt files and generated logs out of public repositories.

## Environment Contract

Cron runs with a sparse environment. Do not assume shell startup files, aliases, pyenv, conda, nvm, relative paths, or the current terminal's environment will exist.

Use an absolute `cd` into the OpenCode Skill repository and invoke the project virtual environment directly:

```cron
30 21 * * 4 cd /absolute/path/to/opencode_skill && /absolute/path/to/opencode_skill/.venv/bin/python -m opencode_skill submit --prompt-file /absolute/path/to/prompts/weekly_job.md --title "Weekly OpenCode Job" --send-timeout 5 >> /absolute/path/to/logs/weekly_job.log 2>&1
```

The CLI loads `.env` from the current working directory, so the `cd` is intentional. Verify `.env` contains the needed `OPENCODE_BASE_URL`, credentials, model, provider, and agent defaults or pass explicit CLI flags. Do not write secrets into crontab.

## Crontab Safety

Always back up the existing crontab before changing it. Put backups in a stable local backup directory outside public git, and include a timestamp in the filename.

Example backup command:

```bash
mkdir -p /absolute/path/to/cron_backups
crontab -l > /absolute/path/to/cron_backups/crontab_$(date +%Y%m%d_%H%M%S).txt 2>/dev/null || true
```

When modifying crontab, preserve unrelated entries exactly. Add a short marker comment around entries you own so a later cleanup can target only the temporary or periodic OpenCode job:

```cron
# opencode-periodic: weekly-report
30 21 * * 4 cd /absolute/path/to/opencode_skill && /absolute/path/to/opencode_skill/.venv/bin/python -m opencode_skill submit --prompt-file /absolute/path/to/prompts/weekly_report.md --title "Weekly Report" --send-timeout 5 >> /absolute/path/to/logs/weekly_report.log 2>&1
```

## Validation

A scheduled job is ready only when all of these are true:

1. The prompt file exists, is readable, and `submit --dry-run --prompt-file <file>` succeeds with `assistant_replied_ok`.
2. The repository `.venv/bin/python` exists and can import `opencode_skill` from the same directory cron will `cd` into.
3. The `.env` or explicit flags provide working OpenCode submission configuration.
4. The current crontab has been backed up before modification.
5. The installed crontab contains the intended entry and preserves unrelated entries.
6. A two-minute temporary cron test has fired successfully, or the user explicitly chose to skip live testing.
7. Any temporary test entry has been removed after the test, with another crontab backup taken before cleanup.

For a live test, first run the OpenCode CLI dry run:

```bash
cd /absolute/path/to/opencode_skill && /absolute/path/to/opencode_skill/.venv/bin/python -m opencode_skill submit --prompt-file /absolute/path/to/prompts/weekly_job.md --title "Weekly OpenCode Job" --dry-run --json
```

Then schedule at least two minutes in the future. One-minute cron tests are flaky because the edit may land too close to the next minute boundary. Tell the user what will happen, install a temporary entry with a unique marker, wait long enough for cron to fire, then check the log and OpenCode session list or submission output. Remove only the temporary marker entry afterward.

## Two-Minute Test Pattern

Use the same command shape as the real job, changing only the schedule, title, log path, and marker. Generate the minute and hour from the local machine's clock, with at least two full minutes of lead time.

The test succeeds when cron writes expected output to the log and the OpenCode submission creates or reports a session. `status=submitted_timeout` can still be a successful handoff because OpenCode may keep processing after the HTTP message request times out; verify the session ID or title in the client/server before treating it as a cron failure. If the log shows Python import errors, re-check the absolute `.venv/bin/python` path and working directory. If the log shows authentication or server errors, fix `.env` or explicit CLI flags and rerun the two-minute test.

## Known Cron Pitfalls

- Cron uses the machine's local timezone unless configured otherwise. State the timezone in the user's schedule summary.
- `%` has special meaning in crontab commands. Avoid it in inline command text; keep complex content in files.
- Relative paths usually point somewhere unexpected. Use absolute paths for prompt files, logs, backups, and Python.
- Shell init files usually do not run. Avoid aliases and version-manager shims.
- macOS cron may need Full Disk Access or other privacy permissions for paths under protected locations. Put prompts and logs somewhere cron can read and write.
- The OpenCode Skill CLI loads `.env` from the current working directory. A missing `cd` can silently use placeholder defaults.
- Long prompts or slow OpenCode HTTP handlers can hit the default send timeout even after the server has created a visible session. Prefer `--send-timeout 300` for scheduled jobs and verify the OpenCode client before assuming a timeout means no submission happened.

## Output To User

When done, report the repository path, prompt file path, backup path, cron expression, installed command summary, test result, and any remaining manual expectation such as "restart OpenCode" or "confirm the new session appears in your OpenCode client." Do not paste secrets, `.env` contents, full private prompts, or private session IDs unless the user explicitly asks for them.
