# Migration plan format v1

Plans are canonical JSON files with permissions `0600`.

```json
{"plan_format":1,"plan_id":"<sha256 of body>","body":{...}}
```

`plan_id` must equal `sha256(canonical_json(body))`. The body contains no timestamps so identical inputs produce byte-identical plans.

## Body fields

| Field | Purpose |
|-------|---------|
| `bundle_manifest_sha256` | Fingerprint of the source bundle manifest at plan time |
| `source_os` / `dest_os` | Platform tags used for path and URI rules |
| `target_user_data_dir` | Destination Cursor user-data root (omitted in `--shareable` show output) |
| `target_fingerprint` | Size and SHA-256 of each destination file the plan may touch |
| `path_mappings` | Accepted `old` → `new` path pairs |
| `workspaces` | Per-workspace URI mapping results (inventory; databases are Phase 3) |
| `operations` | Ordered apply steps (`create`, `merge_object`, `append_array`) with `pre_sha256` / `post_sha256` only |
| `conflicts` | Key-level conflicts and resolutions (credential-like key names redacted in display) |
| `extensions_to_install` | Extension IDs present in the bundle but not on the destination |
| `database_inventory` | Bundled databases and adapters (not migrated in Phase 2) |
| `required_acknowledgements` | Non-empty when apply must be refused (for example unresolved conflicts) |

Setting values and merged file contents are not stored in the plan. Apply recomputes them from the bundle and destination with the same merge code as `plan create`, and checks `post_sha256`.

## Operations

| Field | Purpose |
|-------|---------|
| `kind` | `create` (destination file absent), `merge_object` (settings, snippets) or `append_array` (keybindings) |
| `target_rel` | Destination path relative to the user-data root: `User/settings.json`, `User/keybindings.json` or `User/snippets/<name>` (`<name>` a single component ending in `.json` or `.code-snippets`). Each target appears at most once |
| `pre_sha256` / `post_sha256` | Destination text hash before and after the operation (`pre_sha256` is `null` for `create`) |
| `source_member` / `source_sha256` | Bundle member the operation reads; `source_member` must be `user-data/<target_rel>` |
| `use_source_key_digests` | SHA-256 of each conflicting key resolved with `--use-source` (for keybindings, `key\|when`). Digests rather than names keep credential-like key names out of the plan |

Merge rules:

- Keys missing from the destination are added. A key whose destination value is `null` is present, so it conflicts.
- Values are compared as JSON, so `true`, `1` and `1.0` are different values.
- A setting or snippet that wins with `--use-source` is replaced in place. Comments and the other members are left as they were.
- A keybinding that wins with `--use-source` is appended after the destination binding with the same `key` and `when`, because the last matching keybinding takes precedence.
- An unresolved conflict withholds operations only for the file that contains it. Apply is still refused while any conflict is unresolved.
