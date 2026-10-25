from __future__ import annotations

import sqlite3

from cursor_portability.models.enums import DetectionStatus
from cursor_portability.schemas.adapters.cursor_composer_headers import (
    PINNED_LAYOUTS,
    CursorComposerHeadersAdapter,
)
from cursor_portability.schemas.adapters.vscode_style_kv_v1 import VsCodeStyleKvV1Adapter
from cursor_portability.schemas.base import DetectionResult, Inventory, SchemaAdapter
from cursor_portability.schemas.fingerprint import compute_fingerprint

ADAPTERS: tuple[SchemaAdapter, ...] = (
    VsCodeStyleKvV1Adapter(),
    *(CursorComposerHeadersAdapter(v) for v in sorted(PINNED_LAYOUTS)),
)


def select_adapter(conn: sqlite3.Connection) -> tuple[DetectionResult, Inventory | None]:
    fp = compute_fingerprint(conn)
    passes: list[tuple[SchemaAdapter, DetectionResult]] = []
    results: list[DetectionResult] = []
    for adapter in ADAPTERS:
        det = adapter.detect(conn)
        results.append(det)
        if det.status == DetectionStatus.PASS:
            passes.append((adapter, det))
    if len(passes) == 1:
        adapter, det = passes[0]
        inv = adapter.inventory(conn, fp.digest)
        return det, inv
    if len(passes) > 1:
        return DetectionResult(
            DetectionStatus.AMBIGUOUS,
            "registry",
            1,
            "multiple adapters matched",
        ), None
    # fail closed
    message = "; ".join(f"{r.adapter_id}@{r.adapter_version}: {r.message}" for r in results)
    return DetectionResult(DetectionStatus.UNKNOWN, "registry", 1, message or "no adapters"), None
