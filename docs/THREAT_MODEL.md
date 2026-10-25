# Threat model (Phase 0–1)

See the implementation plan for the full analysis. Summary:

## Assets

Live profile integrity, conversation content, credentials, machine IDs, paths, bundle integrity.

## Mitigations by area

| Area | Key controls |
|------|----------------|
| Local filesystem | No SQLite on live DBs; `fs_safe` sole writer; protected-roots guard; audit-hook tripwire; no-follow walks |
| SQLite | Cold copy db+wal+journal; stat stability; WAL folded into the private staged copy only; hardened read connections; scrub only on staged DBs accepted by a versioned adapter, without triggers/views |
| Archives | Path traversal rejection; bomb limits; extract only to new dirs; hash verification |
| Supply chain | Zero runtime deps; hash-locked dev deps; no network at runtime |
| Secrets | Credential key-name scrub + VACUUM INTO; hard exclusions; redacted reports |
| Rollback (Phase 0–1) | Atomic publish; restore only to new directories; marker-guarded cleanup |

## Out of scope

Same-user malware, OS compromise, forensic recovery of scrubbed free pages.

## Residual risks

Scrub heuristics may under-match; bundles are plaintext; Windows ACL isolation unverified; Cursor may not accept vacuumed DBs (Phase 2+).
