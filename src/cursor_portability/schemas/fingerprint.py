from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from cursor_portability.models.common import to_canonical_json
from cursor_portability.storage.hashing import sha256_bytes
from cursor_portability.storage.sqlite_conn import safe_table_name


@dataclass(frozen=True)
class SchemaFingerprint:
    digest: str
    structure: dict[str, object]


def compute_fingerprint(conn: sqlite3.Connection) -> SchemaFingerprint:
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    table_structures: dict[str, object] = {}
    for table in tables:
        if not safe_table_name(table):
            continue
        cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        table_structures[table] = {
            "columns": [{"name": c[1], "type": c[2]} for c in cols],
        }
    structure: dict[str, object] = {"tables": table_structures}
    views = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name"
    ).fetchall()
    triggers = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name"
    ).fetchall()
    structure["views"] = [v[0] for v in views]
    structure["triggers"] = [t[0] for t in triggers]
    canonical = to_canonical_json(structure)
    return SchemaFingerprint(sha256_bytes(canonical.encode()), structure)
