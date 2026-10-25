# Compatibility matrix

## Migration directions

| From → to | Re-point projects and chats (`workspaces remap`) | Settings, keybindings, snippets (`plan` / `migrate`) |
|-----------|--------------------------------------------------|------------------------------------------------------|
| Linux → macOS | Verified on one real profile copied by hand: two rehearsals on clones and the real run | Automated tests on synthetic profiles |
| macOS → macOS | Untested (`--source-os darwin`) | Automated tests on synthetic profiles |
| Windows → macOS | Not supported: apply rejects folder links that are not POSIX paths | Automated tests on synthetic profiles |
| Any → Linux or Windows | Not supported: the workspace ID formula is only implemented for macOS | Automated tests on synthetic profiles |

## Confirmed (automated tests and real-profile checks)

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
| macOS workspace folder ID `md5(path + birthtime_ms)` | Verified on one Mac; unit test |
| In-profile `workspaces remap` (`workspace_copy` + `composerHeaders.workspaceId`, optional recent-paths key) | E2E on synthetic Ubuntu-layout fixture (macOS); one real Linux → macOS profile |
| Remap link rewrites (chat `workspaceIdentifier` / `agentLocation` / `trackedGitRepos`, Agents-window state) | Field names from one real profile's census; unit + E2E on fixtures built from those names. Checked in Cursor on one real Linux → macOS profile (two rehearsals on clones, then the real run): moved chats are listed under the Mac project, files referenced in them open, and the Agents window shows the Mac path |
| Remap `--replace` for projects already opened on the Mac | E2E; one rehearsal on a real profile clone, then the real run |
| Remap merge of several Ubuntu workspaces that map to one Mac folder | E2E |

## Inferred (not verified on live Cursor)

| Item | Tag |
|------|-----|
| Meaning of `composerHeaders` / `cursorDiskKV` rows | UNVERIFIED (copied as-is; only the remap link fields above are interpreted) |
| `composerHeaders` layout on other Cursor versions / OSes | UNVERIFIED (any column change fails closed) |
| Linux / Windows default paths | COMMUNITY + layout simulation |
| Store stack (`~/.cursor/chats`) | INVENTORY_ONLY in backup |

## Unsupported / partial

- Copying a profile between machines (manual step; see the [user guide](docs/USER_GUIDE.md))
- Rewriting old paths inside chat message bodies, tool output, checkpoints, or attachments
- Per-editor UI state that references old file paths
- `~/.cursor` data (agent transcripts, store chats, MCP config) in a remap
- `--link-chats` on a real profile (E2E only)
- Extension install (IDs listed in run folder only)
- Workspace ID formula on Linux or Windows as migration destination
- Windows paths as remap source
- `conversation-search.db` mutation or inclusion by default
- Encrypted bundles
- Cloud / Background Agent history

## Cursor versions

No version is certified until external testers submit sanitized `inspect --shareable` output and reviewed skeleton fixtures.
