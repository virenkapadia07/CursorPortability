# Architecture (Phase 0–1)

Layered design: CLI → services → discovery / schemas / bundle → platform / storage → security.

```
CLI / reporting
      |
services (doctor, detect, inspect, backup, verify, list, restore)
      |
+----------+-----------+----------+
| discovery| schemas   | bundle   |
+----------+-----------+----------+
      |
platform adapters + storage (sqlite snapshot, fs_safe, hashing)
      |
security (tripwire, scrub, redaction, archive validation)
```

## Package layout

`src/cursor_portability/` contains `cli/`, `platform/`, `discovery/`, `schemas/`, `storage/`, `bundle/`, `security/`, `services/`, `reporting/`, `models/`, `errors.py`.

`planning/` and `migration/` are reserved for Phase 2+.

## Import rules

- OS/env access only in `platform/`.
- `discovery` does not import `sqlite3`.
- `schemas` receive staged read-only DB handles only.
- Filesystem writes (except scrub SQL on staged copies) go through `storage/fs_safe.py`.

## Schema adapters

Versioned, read-only in Phase 0–1. Registry requires exactly one `PASS` or fails closed (`UNKNOWN` / `AMBIGUOUS`).
