# Compatibility matrix (Phase 0–3)

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
| `workspaces census` on cold copies (workspace mapping, path-hit census, composer row counts) | E2E read-only test |
| macOS workspace folder ID `md5(path + birthtime_ms)` | Verified on this Mac; unit test |
| In-profile `workspaces remap` (`workspace_copy` + `composerHeaders.workspaceId`, optional recent-paths key) | E2E on synthetic Ubuntu-layout fixture (macOS) |
| Remap link rewrites (chat `workspaceIdentifier` / `agentLocation` / `trackedGitRepos`, Agents-window state) | Field names from one real profile's census; unit + E2E on fixtures built from those names. Whether Cursor then lists the chats under the Mac project is UNVERIFIED until a rehearsal on a clone |

## Inferred (not verified on live Cursor)

| Item | Tag |
|------|-----|
| Meaning of `composerHeaders` / `cursorDiskKV` rows | UNVERIFIED (copied as-is; only the remap link fields above are interpreted) |
| `composerHeaders` layout on other Cursor versions / OSes | UNVERIFIED (any column change fails closed) |
| Linux / Windows default paths | COMMUNITY + layout simulation |
| Store stack (`~/.cursor/chats`) | INVENTORY_ONLY in backup |

## Unsupported / partial (Phase 3)

- Rewriting Ubuntu paths inside chat message bodies or attachments
- Per-editor UI state that references old file paths
- Extension install (IDs listed in run folder only)
- Workspace ID formula on Linux or Windows as migration destination
- `conversation-search.db` mutation or inclusion by default
- Encrypted bundles
- Cloud / Background Agent history

## Cursor versions

No version is certified until external testers submit sanitized `inspect --shareable` output and reviewed skeleton fixtures.
