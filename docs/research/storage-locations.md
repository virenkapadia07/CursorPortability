# Storage locations (research)

Evidence tags: OBSERVED, COMMUNITY, UPSTREAM, UNVERIFIED.

## Composer stack (user-data)

| OS | Path | Tag |
|----|------|-----|
| macOS | `~/Library/Application Support/Cursor/User/` | OBSERVED |
| Linux | `$XDG_CONFIG_HOME/Cursor/User/` or `~/.config/Cursor/User/` | COMMUNITY |
| Windows | `%APPDATA%\Cursor\User\` | COMMUNITY |

Key artifacts: `globalStorage/state.vscdb`, `workspaceStorage/<id>/state.vscdb`, `workspace.json`, `settings.json`.

## Store stack (cursor-home)

| OS | Path | Tag |
|----|------|-----|
| All | `~/.cursor/` (or `%USERPROFILE%\.cursor`) | OBSERVED names |

Notable entries: `chats/**/store.db`, `projects/**/agent-transcripts`, `mcp.json`, `extensions/extensions.json`.

## Excluded by policy v1

Chromium state under user-data root, `machineid`, logs, `User/History`, hot-exit `Backups/`, `mcp-auth.json`.
