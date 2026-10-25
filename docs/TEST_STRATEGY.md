# Test strategy (Phase 0–2)

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
5. **Architecture** — AST import graph and write-API placement.

## Fixtures

Built in code (`tests/fixtures/`): KV databases, profile trees, malicious zip entries. Real-shape skeletons only after user-reviewed `inspect --emit-skeleton`.

## Coverage targets

≥90% branch coverage on `security/`, `storage/`, `bundle/`; full adapter predicate coverage.

## Markers

`@pytest.mark.slow` for large synthetic DB tests.
