# Secrets policy (Phase 1)

## Scrubbed (key names only, never values in reports)

Pattern list v1 in `security/credential_patterns.py`: `secret://` prefix and credential-like substrings.

Applied only when:

- Database adapter detection is `PASS` (`vscode_style_kv@1` or `cursor_composer_headers@1`–`@3`; no triggers/views)
- Processing uses a **staged copy**, then `VACUUM INTO` the bundle file

Only tables with a `key` column are scrubbed. `composerHeaders` has no `key` column, so its rows
(including the `value` JSON) are copied unscrubbed.

## Hard excluded (never read into bundles)

`mcp-auth.json`, Chromium cookie/storage trees, `machineid`, `statsig-cache.json`, logs, sentry/crashpad.

## Opt-in

`mcp.json` requires `--include-mcp` and prints a warning.

## Residual risk

Secrets embedded in conversation blobs or non-matching key names may remain. Treat every bundle as sensitive.
