# CursorPortability

Local-first CLI for discovering, inspecting, backing up, and verifying Cursor IDE profile data.

**Unofficial community project** — not affiliated with Cursor or Anysphere.

## Requirements

- Python 3.12 or newer
- No runtime dependencies (stdlib only)

## Install (development)

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Commands (Phase 0–1)

```bash
cursor-port doctor
cursor-port detect [--json]
cursor-port inspect [--profile PATH] [--json] [--shareable]
cursor-port compatibility
cursor-port backup create --output PATH
cursor-port backup verify PATH
cursor-port backup list PATH
cursor-port backup restore BUNDLE --to NEW_DIR
```

Close Cursor before `inspect` or `backup` on a live profile.

## Documentation

- [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/TEST_STRATEGY.md](docs/TEST_STRATEGY.md)
- [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md)
- [COMPATIBILITY.md](COMPATIBILITY.md)

## License

MIT (see LICENSE when published).
