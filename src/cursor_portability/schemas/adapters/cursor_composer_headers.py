"""Cursor state DB: VS Code-style key/value tables plus a `composerHeaders` table.

Each adapter version pins one `composerHeaders` column layout exactly as observed in a
real macOS Cursor profile (`sqlite_master` only; no keys or values were read). Any
other layout, extra non key/value table, view, or trigger fails detection.
"""

from __future__ import annotations

import sqlite3

from cursor_portability.models.enums import DetectionStatus, EvidenceLevel
from cursor_portability.schemas.base import DetectionResult, Inventory, TableInventory
from cursor_portability.schemas.keyshape import key_shape
from cursor_portability.storage.sqlite_conn import safe_table_name

COMPOSER_HEADERS_TABLE = "composerHeaders"

# PRAGMA table_info rows without cid: (name, type, notnull, dflt_value, pk).
Column = tuple[str, str, int, object, int]

_LEADING: tuple[Column, ...] = (
    ("composerId", "TEXT", 0, None, 1),
    ("workspaceId", "TEXT", 0, None, 0),
    ("createdAt", "INTEGER", 0, None, 0),
    ("lastUpdatedAt", "INTEGER", 0, None, 0),
    ("isArchived", "INTEGER", 0, None, 0),
    ("isSubagent", "INTEGER", 0, None, 0),
    ("recency", "INTEGER", 0, None, 0),
    ("checkpointAt", "INTEGER", 0, None, 0),
)
_VALUE: Column = ("value", "TEXT", 0, None, 0)
_SUBAGENT_TYPE_NAME: Column = ("subagentTypeName", "TEXT", 0, None, 0)

PINNED_LAYOUTS: dict[int, tuple[Column, ...]] = {
    1: (*_LEADING, _VALUE, _SUBAGENT_TYPE_NAME),
    2: (*_LEADING, _SUBAGENT_TYPE_NAME, _VALUE),
    3: (*_LEADING, _VALUE),
}


class CursorComposerHeadersAdapter:
    adapter_id = "cursor_composer_headers"
    evidence = EvidenceLevel.REAL_SKELETON_VERIFIED

    def __init__(self, version: int) -> None:
        self.adapter_version = version
        self._columns = PINNED_LAYOUTS[version]

    def _result(self, status: DetectionStatus, message: str) -> DetectionResult:
        return DetectionResult(status, self.adapter_id, self.adapter_version, message)

    def detect(self, conn: sqlite3.Connection) -> DetectionResult:
        views = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view'").fetchone()[0]
        triggers = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'"
        ).fetchone()[0]
        if views or triggers:
            return self._result(DetectionStatus.PARTIAL, "triggers or views present")
        tables = _user_tables(conn)
        if COMPOSER_HEADERS_TABLE not in tables:
            return self._result(DetectionStatus.UNKNOWN, "no composerHeaders table")
        for table in tables:
            if not safe_table_name(table):
                return self._result(DetectionStatus.UNKNOWN, "unsafe table name")
            info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            if table == COMPOSER_HEADERS_TABLE:
                observed: tuple[Column, ...] = tuple(tuple(c[1:]) for c in info)
                if observed != self._columns:
                    return self._result(
                        DetectionStatus.PARTIAL,
                        "composerHeaders columns differ from pinned layout: "
                        + _column_diff(observed, self._columns),
                    )
            elif not {"key", "value"}.issubset({c[1] for c in info}):
                return self._result(
                    DetectionStatus.PARTIAL, f"table {table} is not key/value shaped"
                )
        return self._result(
            DetectionStatus.PASS,
            f"key/value tables plus composerHeaders layout {self.adapter_version}",
        )

    def inventory(self, conn: sqlite3.Connection, fingerprint_digest: str) -> Inventory:
        tables_out: list[TableInventory] = []
        for table in sorted(_user_tables(conn)):
            if not safe_table_name(table):
                continue
            count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            shapes: set[str] = set()
            if table != COMPOSER_HEADERS_TABLE:
                for (key,) in conn.execute(f'SELECT key FROM "{table}" LIMIT 500'):
                    if isinstance(key, str):
                        shapes.add(key_shape(key))
            tables_out.append(TableInventory(table, int(count), tuple(sorted(shapes)[:50])))
        return Inventory(tuple(tables_out), fingerprint_digest)


def _column_diff(observed: tuple[Column, ...], pinned: tuple[Column, ...]) -> str:
    obs = {c[0]: c for c in observed}
    pin = {c[0]: c for c in pinned}
    parts = []
    missing = [name for name in pin if name not in obs]
    extra = [name for name in obs if name not in pin]
    changed = [name for name in pin if name in obs and obs[name] != pin[name]]
    if missing:
        parts.append(f"missing ({', '.join(missing)})")
    if extra:
        parts.append(f"extra ({', '.join(extra)})")
    if changed:
        parts.append(f"changed type/constraints ({', '.join(changed)})")
    return ", ".join(parts) or "different column order"


def _user_tables(conn: sqlite3.Connection) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
