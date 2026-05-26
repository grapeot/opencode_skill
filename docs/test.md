# Test Strategy: OpenCode Data Skill

## Principles

Tests must not require or inspect a user's real OpenCode database. They should use synthetic SQLite fixtures that are small enough to commit and review. Any test that mutates a database must mutate only a temporary copy.

## Unit and Integration Coverage

Selector tests cover explicit IDs, ID files, title prefixes, empty selections, descendant expansion, and multi-level child sessions.

Database tests cover read-only connections, attach/detach behavior, and archive schema initialization from the empty fixture.

Migration tests cover plan counts, copy and verify behavior, project row co-migration, delete order, idempotent re-runs, no-op empty selections, and failure detection when destination data is incomplete.

Query tests cover main-only reads, main plus archive reads, explicit archive exclusion, missing archive databases, time windows, and malformed message JSON.

CLI tests cover stats, plan, confirmation requirements, `--no-delete`, and an end-to-end copy/verify/delete flow using temporary databases.

## Local Verification

From the repository root:

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/python -m pytest -q
```

The tests do not need network access and do not need a real OpenCode installation.

## Manual Validation

Manual validation against a real OpenCode database should happen outside git and against a backup or disposable copy first. Record only generic lessons in public docs. Do not commit real session IDs, prompt text, message dumps, exact local paths, backup paths, token totals, or operational logs.

Manual validation should check:

1. `stats` reports the expected configured databases.
2. `plan` selects the intended session set.
3. `apply --no-delete` copies and verifies without changing the source.
4. Full `apply --confirm` deletes only after verification succeeds.
5. `vacuum-main --confirm` runs only when the user has intentionally stopped writers and has enough free disk space.

## Privacy Validation

Before publishing, run current-tree and history scans for private paths, secrets, and real OpenCode artifacts. Treat a clean current tree as necessary but not sufficient; git history can still contain sensitive operational notes.
