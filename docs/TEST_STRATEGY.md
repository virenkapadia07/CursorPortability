# Test strategy (Phase 0–1)

## Guards (`tests/conftest.py`)

- Fake HOME / XDG / Windows env vars for fixture profiles.
- Tripwire fails tests on real Cursor roots, `socket.connect`, non-allowlisted subprocesses, and live-root SQLite opens.
- Tree snapshots for no-mutation assertions.

## Layers

1. **Unit** — paths, policy, patterns, redaction, manifest, header parser, archive names.
2. **Property (hypothesis)** — archive validator, key shapes.
3. **SQLite** — WAL copy, journal, SOURCE_CHANGED, integrity, scrub, VACUUM INTO.
4. **E2E** — `main(argv)` for detect/inspect/backup/verify/restore on synthetic macOS/Linux/Windows layouts.
5. **Architecture** — AST import graph and write-API placement.

## Fixtures

Built in code (`tests/fixtures/`): KV databases, profile trees, malicious zip entries. Real-shape skeletons only after user-reviewed `inspect --emit-skeleton`.

## Coverage targets

≥90% branch coverage on `security/`, `storage/`, `bundle/`; full adapter predicate coverage.

## Markers

`@pytest.mark.slow` for large synthetic DB tests.
