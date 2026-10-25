# Threat model (Phase 0–2)

See the implementation plan for the full analysis. Summary:

## Assets

Live profile integrity, conversation content, credentials, machine IDs, paths, bundle integrity.

## Mitigations by area

| Area | Key controls |
|------|----------------|
| Local filesystem | No SQLite on live DBs; `fs_safe` sole writer; protected-roots guard; no-follow walks; audit-hook tripwire that blocks, under the Cursor profile roots, write-mode opens, renames and replaces, deletes, `mkdir`, `rmtree`, symlink and hard-link creation, and permission, timestamp or extended-attribute changes |
| SQLite | Cold copy db+wal+journal; stat stability; WAL folded into the private staged copy only; hardened read connections; scrub only on staged DBs accepted by a versioned adapter, without triggers/views |
| Archives | Path traversal rejection; bomb limits; extract only to new dirs; hash verification |
| Supply chain | Zero runtime deps; hash-locked dev deps; no network at runtime |
| Secrets | Credential key-name scrub + VACUUM INTO; hard exclusions; redacted reports |
| Rollback (Phase 0–1) | Atomic publish; restore only to new directories; marker-guarded cleanup |
| Profile writes (Phase 2) | Allowlist: `User/settings.json`, `User/keybindings.json`, `User/snippets/<name>.json` or `.code-snippets` only (single path component; `..`, separators, `:` and NUL rejected); each operation's bundle source must be `user-data/<target>`; duplicate targets rejected; symlinked targets refused; apply window lifts the tripwire only for the planned files, their `.partial-*` temp files and missing parent directories; atomic replace with temp-file cleanup; pre-apply snapshot; journaled ops; restores verified against the snapshot; stale plan / fingerprint checks; typed confirmation |

## Out of scope

Same-user malware, OS compromise, forensic recovery of scrubbed free pages.

## Residual risks

Scrub heuristics may under-match; bundles are plaintext; Windows ACL isolation unverified; Cursor may not accept vacuumed DBs (Phase 2+).
