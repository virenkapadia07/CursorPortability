# Architecture (Phase 0–2)

Layered design: CLI → services → planning / migration / discovery / schemas / bundle → platform / storage → security.

```
CLI / reporting
      |
services (doctor, detect, inspect, backup, plan, migrate, …)
      |
+----------+-----------+----------+-----------+
| planning | migration | discovery| schemas   |
+----------+-----------+----------+-----------+
| bundle   |
+----------+
      |
platform adapters + storage (sqlite snapshot, fs_safe, hashing)
      |
security (tripwire, scrub, redaction, archive validation)
```

## Package layout

`src/cursor_portability/` contains `cli/`, `platform/`, `discovery/`, `schemas/`, `storage/`, `bundle/`, `security/`, `services/`, `planning/`, `migration/`, `reporting/`, `models/`, `errors.py`.

Phase 2 adds read-only `plan create` and journaled `migrate apply` for settings, keybindings, and snippets only. SQLite profile data is not written until Phase 3.

## Import rules

- OS/env access only in `platform/`.
- `discovery` does not import `sqlite3`.
- `schemas` receive staged read-only DB handles only.
- Filesystem writes (except scrub SQL on staged copies) go through `storage/fs_safe.py`.

## Schema adapters

Versioned, read-only in Phase 0–1. Registry requires exactly one `PASS` or fails closed (`UNKNOWN` / `AMBIGUOUS`).
