# Architecture (Phase 0–3)

Layered design: CLI → services → planning / migration / discovery / schemas / bundle → platform / storage → security.

```
CLI / reporting
      |
services (doctor, detect, inspect, backup, plan, migrate, census, …)
      |
+----------+-----------+----------+-----------+
| planning | migration | discovery| schemas   |
| workspace| remap     |          |           |
+----------+-----------+----------+-----------+
| bundle   |
+----------+
      |
platform adapters + storage (sqlite snapshot, fs_safe, hashing)
      |
security (tripwire, scrub, redaction, archive validation)
```

## Package layout

`src/cursor_portability/` contains `cli/`, `platform/`, `discovery/`, `schemas/`, `storage/`, `bundle/`, `security/`, `services/`, `planning/`, `migration/`, `workspace/`, `remap/`, `reporting/`, `models/`, `errors.py`.

Phase 2: read-only `plan create` and journaled `migrate apply` for settings, keybindings, and snippets.

Phase 3: read-only `workspaces census`, in-profile `workspaces remap` (workspace folder copy + `composerHeaders.workspaceId` updates on a staged global DB, then atomic publish). Live DBs are never opened for write; only cold copies are edited.

## Import rules

- OS/env access only in `platform/`.
- `discovery` does not import `sqlite3`.
- `schemas` receive staged read-only DB handles only.
- Filesystem writes (except scrub SQL on staged copies) go through `storage/fs_safe.py`.

## Schema adapters

Versioned, read-only in Phase 0–1. Registry requires exactly one `PASS` or fails closed (`UNKNOWN` / `AMBIGUOUS`).
