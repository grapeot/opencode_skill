# Working Log: OpenCode Data Skill

This public working log records project-level changes and validation results without private operational data. Do not add real database paths, session IDs, prompts, message excerpts, token totals, backup filenames, server ports, or local workspace paths.

## Changelog

### 2026-05-25

- Reworked the repository scaffold for public GitHub publication.
- Replaced workspace-specific README, AGENTS, PRD, RFC, and test strategy content with public-safe documentation.
- Added `pyproject.toml`, `.env.example`, `LICENSE`, a root skill file, a wrapper script, and type-checker configuration.
- Expanded `.gitignore` to block local configuration, build artifacts, caches, logs, SQLite data files, and SQLite sidecars.
- Added environment-variable path configuration for the CLI and query defaults.
- Fixed `apply --no-expand` so deletion uses resolved session IDs rather than raw selector parameters.
- Ran offline tests and current-tree privacy scans.

## Validation

### 2026-05-25

- `python -m pytest tests -q`: passed.
- Current-tree private marker scan: passed.
- Current-tree secret marker scan: passed.
- Current-tree excluded artifact scan: passed.
- Git history privacy scan: failed because prior commits contain private operational details. Do not publish the existing history.

## Lessons Learned

- A clean current tree is not enough for public GitHub publication; git history must be scanned and cleaned before pushing.
- Public logs should record validation categories and outcomes, not real local data or operation transcripts.
- Deleting a private file in the current tree does not remove it from previous commits.
