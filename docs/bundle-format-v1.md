# Bundle format v1

Directory or ZIP archive containing:

| File | Purpose |
|------|---------|
| `manifest.json` | Canonical JSON metadata, file inventory, fingerprints |
| `checksums.sha256` | `sha256  path` lines |
| `exclusions.json` | Excluded paths and reasons |
| `source-report.json` | Redacted source summary |
| `README-RESTORE.txt` | Human restore hint |
| `user-data/...` | Composer stack files |
| `cursor-home/...` | Store-side portable files |

SQLite databases are stored scrubbed as `*.sqlite` (vacuumed single-file, DELETE journal mode).

`bundle_format_major` must be `1`.

`manifest.json` field `schema_adapters` maps each bundled `*.sqlite` path to the adapter that
accepted it (`<adapter_id>@<adapter_version>`, e.g. `cursor_composer_headers@2`). Readers treat a
missing field as empty.

## `exclusions.json`

```json
{"excluded":[{"category":"LOGS","path":"logs/main.log","reason":"EXCLUDE","root":"user-data"}],"policy_version":1}
```

`path` is relative to `root` (`user-data` or `cursor-home`). `reason` is one of:

- a policy action (`EXCLUDE`, `OPT_IN`, `INVENTORY_ONLY`)
- `EXCLUDED_BY_FLAG` / `NOT_IN_INCLUDE_LIST` (CLI `--exclude` / `--include`)
- `SCHEMA_<status>` (database whose schema no adapter accepted); these entries also carry a
  `detail` string with each adapter's rejection reason
- `LINK_NOT_FOLLOWED` (symlink or junction; `category` is empty)
- `DEPTH_LIMIT` (non-empty directory at the scan depth limit; its contents were not scanned)
- `UNREADABLE` (file or directory the scan could not read; an empty `path` means the root itself)

## ZIP bundles

ZIP bundles are treated as untrusted input by `verify`, `list`, and `restore`. Before any
member is read, every entry is checked; the archive is rejected on:

- unsafe names (traversal, absolute/drive/UNC, backslash, NUL, Windows reserved names, non-NFC)
- duplicate names or case-insensitive collisions
- symlink, special, or encrypted entries
- more than 100,000 entries, any entry over 16 GiB, more than 64 GiB total, or a compression
  ratio above 200:1 for entries of 1 MiB or more

Members are streamed with a byte counter that must match the declared size. `restore` checks free
space for the declared total before extracting, and `verify` extracts each database to a private
temp directory to run the same SQLite, credential-key, and fingerprint checks used for directory
bundles.
