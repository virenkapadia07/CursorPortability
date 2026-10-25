# Community tools (scope comparison)

| Tool | Focus | vs CursorPortability |
|------|-------|----------------------|
| [cursor-history](https://github.com/S2thend/cursor-history) | Browse/search/export; Composer backup | We target auditable bundles, scrub, verify, restore to new dirs; no Node runtime dep |
| [cursaves](https://github.com/Callum-Ward/cursaves) | Git/S3 sync snapshots | We are local-first, schema-versioned, fail-closed |
| [deja-vu](https://github.com/vshulcz/deja-vu) | Cross-agent memory | Different problem domain |
| johnlindquist/cursor-history | Clipboard/browse | Not a migration toolkit |

CursorPortability Phase 0–1 does **not** migrate conversations into a live profile; it backs up portable categories and documents unknown schema families.
