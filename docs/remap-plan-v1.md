# Workspace remap plan v1 (Phase 3)

Remap plans use the same envelope as migration plans (`plan_format: 1`) with `body.remap_plan_format: 1`.

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

Apply, verify, and rollback reject a plan whose workspace IDs are not 32 lowercase hex characters, whose row counts are not non-negative integers, that repeats a Mac or Ubuntu ID, or that does not have exactly one `db_update`.

### `db_update`

Updates `composerHeaders.workspaceId` from each Ubuntu ID to the Mac ID (adapter `cursor_composer_headers@1`–`@3`). Optionally rewrites `history.recentlyOpenedPathsList` in `ItemTable` when the census found the old prefix there.

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
$CP workspaces remap rollback --run-id ID --runs-dir ~/CursorBackups/rehearsal-runs
```

Projects already opened on the Mac are skipped; re-plan with `--link-chats NAME` (keep the Mac workspace, attach the Ubuntu chats) or `--replace NAME` (replace the Mac workspace state).

3. **Real profile** (Cursor quit): take a full clone first (`cp -cR ~/Library/Application\ Support/Cursor ~/CursorBackups/pre-remap-ud`), then `plan`, `show`, `apply` (type `yes`), and `verify`. `rollback` works only until Cursor is opened again, because Cursor then changes the global DB and it no longer matches `global_db_fingerprint_post`. After that, restore by moving the remapped profile aside and cloning `pre-remap-ud` back. Delete the run folder when done; its snapshot holds a full copy of the global DB.
