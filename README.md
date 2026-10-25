# CursorPortability

A local command-line tool for moving your Cursor IDE setup to another computer. It backs up a profile, merges settings into another install, and, after you copy a profile from Linux to macOS, re-points each project and its chats to the project's new folder.

> [!CAUTION]
> **Unofficial. Use at your own risk.** This project is not affiliated with, endorsed by, or supported by Cursor or Anysphere. It changes files that Cursor does not document, and a Cursor update can break it. It comes with no warranty, and the authors are not responsible for lost data or any other damage. Back up your profile and projects first. Read the [disclaimer](DISCLAIMER.md) before use.

Cursor does not publish the format of its local files, so the tool only changes data in layouts it has been tested on and stops on anything it does not recognise.

## What it does

| Task | Commands |
|------|----------|
| Re-point projects and their chats to new folder paths, after copying a profile to a Mac | `workspaces census`, `workspaces remap plan` / `show` / `apply` / `verify` / `rollback` |
| Merge settings, keybindings and snippets into another install | `plan create` / `show`, `migrate apply` / `verify` / `rollback` |
| Portable backups with checksums, credential files left out | `backup create` / `verify` / `list` / `restore` |
| Look at a profile without changing it | `doctor`, `detect`, `inspect [--shareable]`, `compatibility` |

| From → to | Re-point projects and chats | Settings, keybindings, snippets |
|-----------|-----------------------------|---------------------------------|
| Linux → macOS | Verified on one real profile | Automated tests |
| macOS → macOS | Untested | Automated tests |
| Windows → macOS | Not supported | Automated tests |
| Any → Linux or Windows | Not supported | Automated tests |

## Install

Requires Python 3.12 or newer. There are no runtime dependencies.

```bash
git clone <repository-url> CursorPortability
cd CursorPortability
python3.12 -m venv .venv
source .venv/bin/activate
pip install .
cursor-port doctor
```

## Moving to a new computer

Read the [user guide](docs/USER_GUIDE.md) before you start. It covers the manual profile copy, a rehearsal on a copy of the profile, the real run, undoing it, and cleaning up afterwards.

The project route looks like this, with Cursor closed:

```bash
cursor-port --json workspaces census --from /home/<you>/Projects --to /Users/<you>/Projects > census.json
cursor-port workspaces remap plan --from /home/<you>/Projects --to /Users/<you>/Projects --output plan.json
cursor-port --json workspaces remap show plan.json
cursor-port workspaces remap apply plan.json
cursor-port workspaces remap verify --run-id <run-id>
```

## Safety

- A profile is only changed when you apply a plan. Plans are files you can read first, named by the hash of their contents.
- Applying refuses to run while Cursor is open, takes a snapshot first, records a journal, verifies the result, and can be rolled back.
- Writes are limited to the files a plan names; anything else under Cursor's profile folders is blocked.
- Unknown database layouts and unexpected data fail closed, before anything is written.
- No network access and no telemetry.

## Documentation

- [User guide](docs/USER_GUIDE.md)
- [Compatibility](COMPATIBILITY.md): what has been verified, and how
- Formats: [remap plan](docs/remap-plan-v1.md), [migration plan](docs/plan-format-v1.md), [bundle](docs/bundle-format-v1.md), [run journal](docs/run-journal-v1.md)
- [Secrets policy](docs/secrets-policy.md)
- Design: [architecture](docs/ARCHITECTURE.md), [threat model](docs/THREAT_MODEL.md), [test strategy](docs/TEST_STRATEGY.md), [assumptions](docs/ASSUMPTIONS.md)

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check src tests
mypy
```

## License

[MIT](LICENSE), provided without warranty. See also the [disclaimer](DISCLAIMER.md).
