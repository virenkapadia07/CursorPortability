# Compatibility matrix (Phase 0–1)

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

## Inferred (not verified on live Cursor)

| Item | Tag |
|------|-----|
| Meaning of `composerHeaders` / `cursorDiskKV` rows | UNVERIFIED (copied as-is; not interpreted) |
| `composerHeaders` layout on other Cursor versions / OSes | UNVERIFIED (any column change fails closed) |
| Linux / Windows default paths | COMMUNITY + layout simulation |
| Store stack (`~/.cursor/chats`) | INVENTORY_ONLY in backup |

## Unsupported (Phase 0–1)

- Live profile restore / migration apply
- Conversation session migration
- Cross-workspace path remapping
- `conversation-search.db` mutation or inclusion by default
- Encrypted bundles
- Cloud / Background Agent history

## Cursor versions

No version is certified until external testers submit sanitized `inspect --shareable` output and reviewed skeleton fixtures.
