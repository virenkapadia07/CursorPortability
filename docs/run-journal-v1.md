# Migration run journal v1

Each `migrate apply` creates a directory under `~/.cursor-portability/runs/<run_id>/` (override with `--runs-dir`).

| File | Purpose |
|------|---------|
| `plan.json` | Copy of the applied plan |
| `snapshot/` | Pre-apply copies of touched destination files |
| `journal.jsonl` | Append-only, fsynced event log |
| `report.json` | Machine-readable outcome |
| `extensions-to-install.txt` | Extension IDs to install manually |
| `.lock` | Present while a run is in progress |

## Journal events

`run_started`, `snapshot_done`, `op_begin`, `op_done`, `run_completed`, `apply_failed`, `rollback_completed`, `rollback_incomplete`.

`apply_failed` records the original error (`cause`) and the files that did not match their snapshot after the automatic restore (`not_restored`). A restored file counts only if its SHA-256 matches its snapshot, or if it is absent when it did not exist before apply. Symlinked targets are never restored through.

| Outcome | Error code | `.lock` |
|---------|-----------|---------|
| Every written file restored | `APPLY_FAILED_ROLLBACK_SUCCEEDED` | Removed |
| Any file not restored | `APPLY_FAILED_ROLLBACK_INCOMPLETE` | Kept; `snapshot/` holds the originals |

Before creating a run, apply refuses symlinked destination files. It reports `already_applied` without creating a run directory when every target already matches its `post_sha256`.

`--run-id` must be a 12-character lowercase hex run ID.

`migrate verify --run-id ID` checks destination files against the plan's `post_sha256` hashes.

`migrate rollback --run-id ID` checks every target before restoring any of them. It refuses when a destination file no longer matches its post-apply hash, or when the snapshot of a file that existed before apply is missing. Restores are verified the same way as the automatic restore. A failed restore raises `RUN_INCOMPLETE` and journals `rollback_incomplete`.

## Workspace remap runs (Phase 3)

Journal events include `remap_started`, `publish_global_begin`, `publish_global_done`, `workspace_copy_done`, `remap_completed`, `remap_failed`.

`workspaces remap verify` uses semantic checks on a cold copy of the global DB (integrity, no rows left under Ubuntu IDs, `existing_rows + expected_rows` under each Mac ID, `workspace.json` URI, recomputed Mac workspace ID).

If apply fails, only the targets it had started to publish are restored from the snapshot, inside the same write allowlist (global DB set plus the plan's Mac workspace folders).

`workspaces remap rollback` restores the global DB set and workspace folders from the snapshot when the live global DB still matches `global_db_fingerprint_post` in `report.json`.
