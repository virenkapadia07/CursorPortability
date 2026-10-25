from __future__ import annotations

import sqlite3

from cursor_portability.models.enums import DetectionStatus, EvidenceLevel
from cursor_portability.schemas.base import DetectionResult, Inventory, TableInventory
from cursor_portability.schemas.keyshape import key_shape
from cursor_portability.storage.sqlite_conn import safe_table_name


class VsCodeStyleKvV1Adapter:
    adapter_id = "vscode_style_kv"
    adapter_version = 1
    evidence = EvidenceLevel.UPSTREAM_INFERRED

    def detect(self, conn: sqlite3.Connection) -> DetectionResult:
        views = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view'").fetchone()[0]
        triggers = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'"
        ).fetchone()[0]
        if views or triggers:
            return DetectionResult(
                DetectionStatus.PARTIAL,
                self.adapter_id,
                self.adapter_version,
                "triggers or views present",
            )
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        if not tables:
            return DetectionResult(
                DetectionStatus.UNKNOWN,
                self.adapter_id,
                self.adapter_version,
                "no user tables",
            )
        for table in tables:
            if not safe_table_name(table):
                return DetectionResult(
                    DetectionStatus.UNKNOWN,
                    self.adapter_id,
                    self.adapter_version,
                    "unsafe table name",
                )
            cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            col_names = {c[1] for c in cols}
            if not {"key", "value"}.issubset(col_names):
                return DetectionResult(
                    DetectionStatus.PARTIAL,
                    self.adapter_id,
                    self.adapter_version,
                    f"table {table} is not key/value shaped",
                )
        return DetectionResult(
            DetectionStatus.PASS,
            self.adapter_id,
            self.adapter_version,
            "all tables are key/value without triggers/views",
        )

    def inventory(self, conn: sqlite3.Connection, fingerprint_digest: str) -> Inventory:
        tables_out: list[TableInventory] = []
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        for table in tables:
            if not safe_table_name(table):
                continue
            count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            shapes: set[str] = set()
            cur = conn.execute(f'SELECT key FROM "{table}" LIMIT 500')
            for (key,) in cur:
                if isinstance(key, str):
                    shapes.add(key_shape(key))
            tables_out.append(TableInventory(table, int(count), tuple(sorted(shapes)[:50])))
        return Inventory(tuple(tables_out), fingerprint_digest)
