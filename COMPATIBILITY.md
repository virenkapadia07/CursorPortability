# Compatibility matrix (Phase 0–2)

## Confirmed (automated tests)

| Capability | Evidence |
|------------|----------|
| macOS-style profile tree discovery | Synthetic fixture tests |
| VS Code-style KV SQLite detection (`key`/`value` tables, no triggers/views), adapter `vscode_style_kv@1` | Unit + backup round-trip |
| Cursor `state.vscdb` with key/value tables plus `composerHeaders`, adapters `cursor_composer_headers@1`–`@3` | Three column layouts pinned from `sqlite_master` of a real macOS profile (run on a manual backup copy): `@1`, `@2` (`subagentTypeName` before `value`), `@3` (no `subagentTypeName`); unit + E2E on fixtures built from those definitions |
| Databases whose content is only in an un-checkpointed `-wal` (main file = header page) | Observed on real workspace DBs; WAL folded into the staged copy; unit + E2E |
| Backup create / verify / restore to new directory | E2E tests |
| Credential key-name scrub + bundle canary scan | E2E tests |
| Archive path traversal rejection | Unit tests |
| Cross-OS `file://` workspace URI parse and path-map (longest-prefix, collision detection) | Unit tests |
| Deterministic content-addressed migration plan (`plan create` / `plan show`) | E2E tests |
| Key-level settings / keybindings / snippets merge with JSONC comment preservation | Unit + E2E tests |
| `migrate apply` with snapshot, journal, verify, and rollback (allowlisted profile files only) | E2E tests on synthetic profiles, including fault injection at every apply step |
| Write guard (audit-hook tripwire) on Cursor profile roots, with a per-apply window for planned files only | Unit + E2E tests |

## Inferred (not verified on live Cursor)

| Item | Tag |
|------|-----|
| Meaning of `composerHeaders` / `cursorDiskKV` rows | UNVERIFIED (copied as-is; not interpreted) |
| `composerHeaders` layout on other Cursor versions / OSes | UNVERIFIED (any column change fails closed) |
| Linux / Windows default paths | COMMUNITY + layout simulation |
| Store stack (`~/.cursor/chats`) | INVENTORY_ONLY in backup |

## Unsupported (Phase 0–2)

- Conversation session migration (SQLite / workspace DB writes)
- Extension install (IDs listed in run folder only)
- Cross-workspace path remapping applied to databases (URI mapping is plan inventory only)
- `conversation-search.db` mutation or inclusion by default
- Encrypted bundles
- Cloud / Background Agent history

## Cursor versions

No version is certified until external testers submit sanitized `inspect --shareable` output and reviewed skeleton fixtures.
