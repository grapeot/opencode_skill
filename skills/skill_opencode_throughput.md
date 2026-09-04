# OpenCode Throughput Skill

## When To Use

Use this skill when a user asks you to measure how fast their local OpenCode setup actually generates tokens — per provider/model — from the SQLite data OpenCode already records. It answers questions like "which model is fastest for me?", "is my local model keeping up with the cloud one?", or "what's my real tokens/second, not the vendor's claimed number?".

It is read-only. It never mutates the databases. For inspecting/archiving/compacting data use `skill_opencode_data.md`; for submitting prompts use `skill_opencode_submission.md`.

## What It Measures

For every assistant message it computes an **effective output throughput**:

```
throughput (tokens/s) = (output tokens + reasoning tokens) / generation duration
```

- **Generation duration** is `time.completed - time.created`. When a message ended with `finish == "tool-calls"`, the duration is truncated at the earliest tool `state.time.start`, because the time the model spent waiting for a tool to run is not inference time.
- **Prefill is included.** The window starts at message creation, so these are effective end-to-end rates (prompt processing + decode), not pure decode speed. That is the number that matters for "how long do I wait".
- Samples with fewer than `--min-output` output+reasoning tokens are dropped (tiny replies make the ratio noisy), as are samples whose duration falls outside a sane `[0.2s, 3600s]` band.

Results are aggregated per `provider/model` into P10 / P25 / P50 / P75 / P90 plus min/max and total output tokens.

## Prerequisites

- Working directory: repository root, alongside `pyproject.toml`
- Python environment: project `.venv/` created with `uv`
- Package installed: `uv pip install --python .venv/bin/python -e '.[dev,chart]'`
  - The `chart` extra pulls in `matplotlib`. If it is missing the CLI still runs but skips the chart and prints a hint to stderr.

## Commands

All commands run from the project root. The main database is required; archive databases are optional and only read if they exist.

```bash
# Print a Markdown table to stdout and write the default report (tmp/throughput_report.md)
.venv/bin/python -m opencode_skill throughput --since 30d --top 15

# Filter to one provider or one model
.venv/bin/python -m opencode_skill throughput --provider example --since 30d
.venv/bin/python -m opencode_skill throughput --model default-model

# Write the report to a specific path (default is tmp/throughput_report.md)
.venv/bin/python -m opencode_skill throughput --since 30d --top 15 --out tmp/throughput_report.md

# Machine-readable output (prints JSON, does not write a report file)
.venv/bin/python -m opencode_skill throughput --since 30d --json

# Only the main DB (ignore archives), skip the chart
.venv/bin/python -m opencode_skill throughput --main-only --no-chart
```

Flags:

| Flag | Meaning |
|---|---|
| `--provider` | Filter to one provider id |
| `--model` | Filter to one model id |
| `--since` | Only messages created after this cutoff (`30d`, `7d`, or an ISO date like `2026-04-09`) |
| `--min-output` | Min output+reasoning tokens for a sample to count (default `50`) |
| `--top` | Limit all output (JSON, report, chart) to the top N models by sample count |
| `--main-only` | Ignore archive databases |
| `--out PATH` | Write a Markdown report (and a `throughput_chart.png` next to it) to `PATH` (default `tmp/throughput_report.md`) |
| `--no-chart` | Skip the matplotlib chart when using `--out` |
| `--json` | Print the results as a JSON array instead of the Markdown table |

Global database path flags (`--main`, `--archive`, `--old-archive`) work the same as in the data skill.

## Output Contract

- **Stdout (default):** a Markdown table with columns `Provider / Model | Samples | P10 | P25 | P50 | P75 | P90 | Total generated tokens`, sorted by sample count descending (ties broken by provider, then model). A report is also written to `--out` (default `tmp/throughput_report.md`) unless `--json` is used.
- **`--json`:** a JSON array; each object has `provider`, `model`, `n`, `total_output_tokens`, `p10`, `p25`, `p50`, `p75`, `p90`, `min`, `max`.
- **`--out PATH`:** writes a Markdown report to `PATH` and a `throughput_chart.png` alongside it (a box plot per model, box = P25–P75, whiskers = P10–P90). The report embeds the chart by relative filename.

## Reading The Numbers

- Compare **P50**, not the max, for "typical" speed. The P10 shows the slow tail (long prompts, cold starts, queueing); the P90 shows the fast tail (short replies, cached prefixes).
- A local model beating a cloud model on P50 is a real, measurable win for your hardware — vendor "claimed tok/s" numbers are marketing, this is what you actually got.
- Very low sample counts (`n` in the tens) are directional only; the percentiles are unstable.
- Because prefill is included, models serving long-context prompts will look slower than the same model on short prompts. If you want a cleaner decode comparison, narrow the window or filter to a specific workload.

## Privacy

The report contains provider/model names, sample counts, and total token counts drawn from your local databases. Treat the report and chart as **private, local artifacts**. Do not commit `tmp/throughput_report.md`, `tmp/throughput_chart.png`, or any `--json` output to this public repository.

## Acceptance Criteria

A task using this skill is complete when:

1. The command ran read-only (no database was mutated).
2. The window and any provider/model filters match what the user asked for.
3. The numbers shown are the effective throughput (output+reasoning tokens / effective generation duration, with tool execution excluded).
4. No report, chart, or JSON output was written into the public repo.