# Working Log: OpenCode Skill

This public working log records project-level changes and validation results without private operational data. Do not add real database paths, session IDs, prompts, message excerpts, token totals, backup filenames, server ports, private endpoints, model names, agent names, generated manifests, rendered prompts, or local workspace paths.

## Changelog

### 2026-05-25

- Broadened the package from data maintenance to a unified OpenCode skill covering HTTP submission, batch submission, QA grouping, and SQLite maintenance.
- Added a requests-based `OpenCodeClient` with CWD `.env` support, Basic auth, session CRUD, message sending, session polling, and provider/model inference.
- Added single-job submission helpers that preserve sessions by default and support prompt text, prompt files, and stdin through the CLI.
- Added batch rendering and submission helpers with spec discovery, slug filtering, smoke slug, variable injection, rate limiting, send timeout, verification hooks, QA grouping, and manifest output.
- Kept existing `stats`, `plan`, `apply`, and `vacuum-main` behavior stable while adding `submit` and `batch` CLI commands.
- Updated public docs, env example, root skill, and ignore rules for generated batch artifacts.
- Reworked the repository scaffold for public GitHub publication.
- Replaced workspace-specific README, AGENTS, PRD, RFC, and test strategy content with public-safe documentation.
- Added `pyproject.toml`, `.env.example`, `LICENSE`, a root skill file, a wrapper script, and type-checker configuration.
- Expanded `.gitignore` to block local configuration, build artifacts, caches, logs, SQLite data files, SQLite sidecars, generated manifests, rendered prompts, and batch output directories.
- Added environment-variable path configuration for the CLI and query defaults.
- Fixed `apply --no-expand` so deletion uses resolved session IDs rather than raw selector parameters.
- Ran offline tests and current-tree privacy scans.

## Validation

### 2026-05-25

- `.venv/bin/python -m pytest -q`: passed locally after the unified submission changes.
- `python -m pytest tests -q`: passed during the initial data-maintenance scaffold validation.
- Current-tree private marker scan: passed during the initial scaffold validation.
- Current-tree secret marker scan: passed during the initial scaffold validation.
- Current-tree excluded artifact scan: passed during the initial scaffold validation.
- Git history privacy scan: failed because prior commits contain private operational details. Do not publish the existing history.

## Lessons Learned

- Public mechanics can live in this repository; private endpoint, model, agent, template, and server policy belongs in `.env` or a private overlay.
- A clean current tree is not enough for public GitHub publication; git history must be scanned and cleaned before pushing.
- Public logs should record validation categories and outcomes, not real local data or operation transcripts.
- Deleting a private file in the current tree does not remove it from previous commits.
