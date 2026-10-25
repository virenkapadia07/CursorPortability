# Assumptions requiring verification

Evidence tags: **OBSERVED**, **COMMUNITY**, **UPSTREAM**, **UNVERIFIED**.

## Locations

| # | Assumption | Tag |
|---|------------|-----|
| 1 | macOS `Library/Application Support/Cursor/User` layout | OBSERVED |
| 2 | Linux `~/.config/Cursor` + XDG | COMMUNITY / UPSTREAM |
| 3 | Windows `%APPDATA%\Cursor` + `%USERPROFILE%\.cursor` | COMMUNITY |
| 4 | WSL Windows vs Linux stores are separate | COMMUNITY, UNVERIFIED |
| 5 | `~/.cursor` tree names | OBSERVED; semantics UNVERIFIED |
| 6 | Version in app `product.json` | UNVERIFIED |
| 7 | Portable `--user-data-dir` | UPSTREAM, UNVERIFIED |

## Running state

| # | Assumption | Tag |
|---|------------|-----|
| 8 | `code.lock` / `*-main.sock` semantics | OBSERVED presence; UNVERIFIED |
| 9 | Process names per OS | UNVERIFIED |
| 10 | WAL cleared after clean quit | REFUTED: global `-wal` is left empty with a `-shm`; workspace DBs can keep all content in a non-empty `-wal` |

## SQLite / schema

| # | Assumption | Tag |
|---|------------|-----|
| 11 | `state.vscdb` is SQLite WAL | sidecars OBSERVED |
| 12 | `ItemTable(key, value)` KV shape | OBSERVED (one macOS profile) |
| 13 | Extra Cursor tables | OBSERVED: `cursorDiskKV(key, value)` and non-KV `composerHeaders` in three column layouts (one macOS profile) |
| 14 | Non-KV tables → PARTIAL, excluded by default | design choice |
| 15 | `conversation-search.db` purpose | UNVERIFIED → UNKNOWN category |
| 16–22 | workspace IDs, Store DBs, slugs, secrets in KV | mostly UNVERIFIED |

## Engine (test-verified in CI)

| # | Assumption |
|---|------------|
| 23–25 | Cold copy + VACUUM INTO + SQLite capabilities on each platform |

## Phase 2 (config migration)

| # | Assumption | Tag |
|---|------------|-----|
| 26 | `settings.json` / `keybindings.json` accept JSONC (comments, trailing commas) | UPSTREAM_INFERRED |
| 27 | `workspace.json` `folder` URIs use VS Code-style `file://` encoding | UPSTREAM_INFERRED |
| 28 | `extensions.json` list shape matches VS Code marketplace identifiers | UPSTREAM_INFERRED |

## Phase 3 (workspace remap)

| # | Assumption | Tag |
|---|------------|-----|
| 29 | macOS `workspaceStorage` folder name is `md5(absolute_folder_path + birthtime_ms)` | VERIFIED (this Mac) |
| 30 | `composerHeaders.workspaceId` matches `workspaceStorage` folder names | CENSUS_GATED |
| 31 | `history.recentlyOpenedPathsList` in `ItemTable` may hold folder URIs to rewrite | CENSUS_GATED |
| 31a | Linux workspace IDs are salted with the folder inode, so a re-cloned project leaves several Ubuntu workspaces for one path; the most recently modified `state.vscdb` / `-wal` / `workspace.json` marks the one Cursor used last (only meaningful if the profile copy kept mtimes) | UPSTREAM_INFERRED |
| 31b | Folder and multi-root workspace folder names are 32-character lowercase md5 hex; anything else (empty-window or remote) is not remapped | UPSTREAM_INFERRED |

## Phase 3+

| # | Assumption |
|---|------------|
| 32–33 | Cursor accepts scrubbed/vacuumed DBs; re-login after scrub |

The only Cursor-specific names hard-coded are the `composerHeaders` table and its columns in
`cursor_composer_headers@1`–`@3`, taken from a real profile's `sqlite_master`. No Cursor key
names are hard-coded. Any deviation from a pinned layout fails closed.
