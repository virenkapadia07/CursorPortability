# Test strategy (Phase 0–3)

## Guards (`tests/conftest.py`)

- Fake HOME / XDG / Windows env vars for fixture profiles.
- Tripwire fails tests on writes under Cursor profile roots, `socket.connect`, and live-root SQLite opens (`tests/unit/test_tripwire.py`). Subprocesses are not yet restricted.
- Tree snapshots for no-mutation assertions.

## Layers

1. **Unit** — paths, policy, patterns, redaction, manifest, header parser, archive names.
2. **Property (hypothesis)** — archive validator, key shapes.
3. **SQLite** — WAL copy, journal, SOURCE_CHANGED, integrity, scrub, VACUUM INTO.
4. **E2E** — `main(argv)` for detect/inspect/backup/verify/restore on synthetic macOS/Linux/Windows layouts.
   - Phase 2 adds plan create, apply, verify and rollback, plus fault injection: a failure at each apply operation and after all writes must leave the destination byte-identical.
   - It also covers apply through the CLI with the write guard active, a stray profile write blocked and rolled back, tampered plan paths, and symlinked targets.
   - Phase 3 (`tests/e2e/test_remap_faults.py`, macOS only) runs remap plan, apply, verify and rollback through the CLI with the write guard active and a live `-wal` beside the global DB. It injects failures at the staged DB update, mid-publish of the global DB, mid workspace copy, and after the report. Each must leave the global DB set byte-identical and the Mac workspace folder absent or restored. Tampered remap plans (path-like or non-hex IDs, bad counts, repeated IDs, wrong operation set) must be rejected before any write, on every OS. `tests/e2e/test_remap.py` covers merging and skipping duplicate Ubuntu workspaces, the chat and Agents-window link rewrites (every rewritten and untouched field, storage type, byte-identical rollback), verify catching a chat left on the Ubuntu workspace, plan refusal for a mismatched chat, and a shareable `show` without paths or raw IDs; `tests/unit/test_json_links.py` covers folder-boundary matching, each refusal case, the dry run, and skipped credential-like keys; `tests/unit/test_tripwire.py` covers `sqlite3.connect` by path and by `file:` URI.
5. **Architecture** — AST import graph and write-API placement.

## Fixtures

Built in code (`tests/fixtures/`): KV databases, profile trees, malicious zip entries. Real-shape skeletons only after user-reviewed `inspect --emit-skeleton`.

## Coverage targets

≥90% branch coverage on `security/`, `storage/`, `bundle/`; full adapter predicate coverage.

## Markers

`@pytest.mark.slow` for large synthetic DB tests.
