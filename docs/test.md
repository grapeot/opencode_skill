# Test Strategy: OpenCode Skill

## Principles

Tests must not require a real OpenCode server or inspect a user's real OpenCode database. They should use fake HTTP sessions, fake clients, temporary batch outputs, and synthetic SQLite fixtures that are small enough to commit and review. Any test that mutates a database must mutate only a temporary copy.

## Unit and Integration Coverage

HTTP client tests cover Basic auth header construction, missing password handling, create-session parsing, send-message payloads, typed HTTP errors, provider/model inference, and wait polling with injected sleep.

Single-job tests cover prompt-source validation, create/send/wait workflow, default session preservation, optional deletion, explicit provider handling, dry-run prompt replacement, OK verification, and failure handling when the assistant response differs from `OK`.

Batch tests cover spec discovery, template rendering with `{{VAR}}` and `${VAR}`, unresolved and bare-token validation, slug filters, smoke slug, template directory selection, the required `batch-` title prefix, QA grouping, QA-from-manifest, manifest structure, rate limiting with injected sleep, and send timeout behavior with fake clients.

Selector tests cover explicit IDs, ID files, title prefixes, empty selections, descendant expansion, and multi-level child sessions.

Database tests cover read-only connections, attach/detach behavior, and archive schema initialization from the empty fixture.

Migration tests cover plan counts, copy and verify behavior, project row co-migration, delete order, idempotent re-runs, no-op empty selections, and failure detection when destination data is incomplete.

Query tests cover main-only reads, main plus archive reads, explicit archive exclusion, missing archive databases, time windows, and malformed message JSON.

CLI tests cover stats, plan, confirmation requirements, `--no-delete`, an end-to-end copy/verify/delete flow using temporary databases, `submit` with a prompt file, `submit --dry-run`, and `batch submit --dry-run`.

## Local Verification

From the repository root:

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/python -m pytest -q
```

The tests do not need network access, a real OpenCode server, or a real OpenCode database.

## Manual Validation

Manual validation against a real OpenCode installation should happen outside git and against synthetic prompts or a disposable database copy first. Record only generic lessons in public docs. Do not commit real session IDs, prompt text, message dumps, exact local paths, backup paths, token totals, manifests, rendered prompts, endpoints, model names, agent names, or operational logs.

Manual validation should check:

1. `submit --prompt-file` creates a session and preserves it by default.
2. `submit --dry-run` sends only the built-in OK prompt, verifies the assistant response, and deletes the dry-run session by default.
3. `batch submit --dry-run` writes rendered prompts and a manifest without network calls.
4. `batch submit --smoke-slug` creates one expected batch-prefixed session.
5. `stats` reports the expected configured databases.
6. `plan` selects the intended session set.
7. `apply --no-delete` copies and verifies without changing the source.
8. Full `apply --confirm` deletes only after verification succeeds.
9. `vacuum-main --confirm` runs only when the user has intentionally stopped writers and has enough free disk space.

## Privacy Validation

Before publishing, run current-tree and history scans for private paths, secrets, session IDs, generated manifests, rendered prompts, and real OpenCode artifacts. Treat a clean current tree as necessary but not sufficient; git history can still contain sensitive operational notes.
