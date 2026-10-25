# Workspace remap plan v1 (Phase 3)

Remap plans use the same envelope as migration plans (`plan_format: 1`) with `body.remap_plan_format: 2`. Format 1 plans (no folder links) are rejected; create a new plan.

## Body fields

| Field | Purpose |
|-------|---------|
| `path_mapping` | `old` / `new` prefix pair (for example `/home/<user>/Projects` → `/Users/<user>/Projects`) |
| `source_os` / `dest_os` | URI and path rules |
| `target_user_data_dir` | Profile being remapped in place |
| `global_db_fingerprint` | Size, `mtime_ns`, and `-wal` SHA-256 of `User/globalStorage/state.vscdb` at plan time |
| `operations` | Ordered `workspace_copy` and `db_update` steps |
| `warnings` | Skipped projects (collision, missing destination folder) |
| `census_summary` | Link-chats eligibility and mapped count from the internal census |

## Operations

### `workspace_copy`

Copies `workspaceStorage/<ubuntu_id>/` to `workspaceStorage/<mac_id>/`, rewrites `workspace.json` `folder` to the Mac `file://` URI, and settles `state.vscdb` in the copy. The Ubuntu folder is left in place.

When several Ubuntu workspaces map to the same Mac folder (a project re-cloned on Ubuntu gets a new workspace ID), the plan merges them by default: `ubuntu_id` is the one whose state files were modified most recently, `merged_ubuntu_ids` lists the others, and `db_update` moves the chats of all of them to the Mac ID. A `merge <project>` warning records this. `remap plan --skip-duplicates` leaves such projects out with a `skip` warning instead.

Apply, verify, and rollback reject a plan whose workspace IDs are not 32 lowercase hex characters, whose row counts are not non-negative integers, that repeats a Mac or Ubuntu ID, that does not have exactly one `db_update`, or whose folder links are malformed (exact field set, absolute paths, `file://` URIs, one link per Ubuntu ID, every `updates` entry matched by a link with the same Mac ID).

Projects whose Mac folder does not resolve to itself (a symlink in the path) are skipped with a warning, because the workspace ID and folder URI would describe the resolved path while the chats would name the unresolved one.

### `db_update`

Updates `composerHeaders.workspaceId` from each Ubuntu ID to the Mac ID (adapter `cursor_composer_headers@1`–`@3`). Optionally rewrites `history.recentlyOpenedPathsList` in `ItemTable` when the census found the old prefix there.

`folder_links` has one entry per moved Ubuntu workspace (including merged duplicates and `--link-chats` projects): `from_id`, `to_id`, `from_path`, `to_path`, `from_uri` (the Ubuntu `workspace.json` folder string) and `to_uri` (the Mac folder URI written to `workspace.json`). Before the column update, the same staged copy gets these JSON rewrites:

- **Chat headers** (`composerHeaders.value` of rows under a moved Ubuntu ID) and their **chat records** (`cursorDiskKV` `composerData:<composerId>`): `workspaceIdentifier.id` and `workspaceIdentifier.uri` (`path`, `fsPath`, `external`); `agentLocation.environment` and each `agentLocationHistory[].location.environment` whose `id` is the Ubuntu ID; `trackedGitRepos[].repoPath` values that are the Ubuntu folder or inside it.
- **Agents-window state** in `ItemTable`: `glass.localAgentProjects.v1`, `workspaceMetadata.entries`, `repositoryTracker.paths`, and keys shaped `cursor<path>` or `cursor<path><uuid><path>` (credential-like keys are skipped). Any string value equal to a moved Ubuntu ID becomes the Mac ID, and any string value that is a moved Ubuntu folder (path or URI) or inside it is re-rooted to the Mac folder.

Strings that only mention a folder inside longer text, chat content (messages, tool calls, checkpoints, `toolDisplayPath`), `draftTarget`, and per-workspace databases are not changed. Cells keep their storage type (TEXT or BLOB); JSON is re-serialised compactly with non-ASCII characters kept.

Plan creation runs the rewrite as a dry run on a cold copy and records the counts in `link_rewrites` (`chat_headers`, `chat_records`, and `agent_state` rows per key shape); apply fails if its counts differ. The plan refuses to be created if a moved chat's `workspaceIdentifier` names another workspace, a different folder, or a multi-root workspace, or if a chat cell is not JSON. Verify checks that every chat header and record under each Mac ID names that Mac ID (and its folder, where a `uri` is stored), and that no Agents-window value still names a moved Ubuntu workspace.

Each entry in `updates` records `expected_rows` (Ubuntu rows to move; apply fails if the `UPDATE` touches a different count) and `existing_rows` (rows already under the Mac ID at plan time, non-zero with `--link-chats`). Verify expects `existing_rows + expected_rows` under each Mac ID, so run it before opening Cursor; new chats created afterwards change the count.

The staged global DB is published by removing the live `-wal`, `-shm`, and `-journal` sidecars and replacing the main file. All three sidecars are snapshotted and restored on failure or rollback.

## CLI

```bash
cursor-port workspaces census --from OLD --to NEW [--shareable]
cursor-port workspaces remap plan --from OLD --to NEW --output plan.json [--replace NAME] [--link-chats NAME] [--skip-duplicates]
cursor-port workspaces remap show plan.json
cursor-port workspaces remap apply plan.json --yes
cursor-port workspaces remap verify --run-id ID
cursor-port workspaces remap rollback --run-id ID --yes
```

Only `census` and `remap plan` locate a local profile (`--target-profile` overrides). `show` reads only the plan; `apply`, `verify`, and `rollback` use the plan's `target_user_data_dir`.

`remap show --shareable` drops the profile directory, project paths and folder URIs, replaces the prefixes with placeholders, hashes workspace IDs like the shareable census, and reduces warnings to a count per kind (`skip`, `merge`).

## Census output (format 3)

For every table and column where the old prefix appears, `path_hits` gives rows per encoding (`raw`, `file_uri`, `percent_encoded`, `json_escaped`) and, for key-value tables, rows per key shape (`key_patterns`; format 2 counted a row once per matching encoding). `fields` lists, per key shape or column, which JSON fields hold the prefix, from up to 500 matching rows per group:

- Field paths use `.` for objects, `[]` for arrays and `<json>` for a JSON document stored inside a string.
- Each field is tagged `(path)` (value starts with the prefix), `(file_uri)`, `(embedded)` (prefix inside longer text) or `(in key)` (the prefix is in an object key).
- Only identifier-like field names are kept. Anything else is shown as `<key>`, and names with four or more digits as `<id>`.

`workspace_databases.path_hits` sums the same report across all workspace databases, and `storage_json_fields` covers `globalStorage/storage.json`. No values are reported.

`chat_links` shows which fields of each chat header (`composerHeaders.value`) and chat record (`cursorDiskKV` `composerData:<composerId>`) repeat the chat's workspace. Rows are grouped by the header's `workspaceId` column: `mapped_workspaces` (the workspaces the remap moves), `other_workspaces`, `no_workspace_folder`, and, for chat records, `no_header_row`. Each group reports field paths whose value equals the workspace ID, equals the workspace folder URI or path from `workspace.json`, and the field names under `workspaceIdentifier`. It reports names and counts only.

`--shareable` also replaces workspace IDs (Ubuntu and Mac) with SHA-256 prefixes, project names with indexes, and the `--from` / `--to` prefixes with placeholders. Key shapes are generalised further: everything after the first `::` (branch names in PR-link keys) becomes `<text>`, segments with spaces or other punctuation become `<text>`, and digit-heavy random IDs become `<id>`. A name-like segment (letters, digits, `_`, `-`) is kept, so a key that embeds a one-word project name still shows it.

The `file_uri` form is built from the source path as given. Format 1 resolved it against the local filesystem, so on macOS its `file_uri` counts for `/home/...` prefixes were always zero.

## Rehearsal (Ubuntu → Mac profile)

`OLD` and `NEW` are the Ubuntu and Mac project roots, for example `/home/<user>/Projects` and `/Users/<user>/Projects`. `CP` is `.venv/bin/cursor-port` in this repository.

1. **Census** (read-only; if Cursor writes during the cold copy it fails with `SOURCE_CHANGED`, so quit Cursor and rerun):

```bash
$CP --json workspaces census --from OLD --to NEW --shareable > census-shareable.json
```

2. **Rehearsal on an APFS clone** (quit Cursor first; apply refuses while it runs):

```bash
cp -cR ~/Library/Application\ Support/Cursor ~/CursorBackups/rehearsal-ud
$CP workspaces remap plan --from OLD --to NEW \
  --target-profile ~/CursorBackups/rehearsal-ud --output ~/CursorBackups/rehearsal-plan.json
$CP workspaces remap show ~/CursorBackups/rehearsal-plan.json
$CP workspaces remap apply ~/CursorBackups/rehearsal-plan.json --runs-dir ~/CursorBackups/rehearsal-runs
$CP workspaces remap verify --run-id ID --runs-dir ~/CursorBackups/rehearsal-runs
```

Then open a separate Cursor on the clone (`open -na Cursor --args --user-data-dir ~/CursorBackups/rehearsal-ud`), open one moved project from its Mac folder, and check that its Ubuntu chats are listed and open. This is the only check of how Cursor reads the rewritten fields. The clone is disposable; delete it and the rehearsal runs folder afterwards.

Projects already opened on the Mac are skipped; re-plan with `--link-chats NAME` (keep the Mac workspace, attach the Ubuntu chats) or `--replace NAME` (replace the Mac workspace state).

3. **Real profile** (Cursor quit): take a full clone first (`cp -cR ~/Library/Application\ Support/Cursor ~/CursorBackups/pre-remap-ud`), then `plan`, `show`, `apply` (type `yes`), and `verify`. `rollback` works only until Cursor is opened again, because Cursor then changes the global DB and it no longer matches `global_db_fingerprint_post`. After that, restore by moving the remapped profile aside and cloning `pre-remap-ud` back. Delete the run folder when done; its snapshot holds a full copy of the global DB.
