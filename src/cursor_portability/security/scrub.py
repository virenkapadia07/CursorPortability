from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from cursor_portability.security.credential_patterns import is_credential_key, pattern_id_for_key
from cursor_portability.storage.sqlite_conn import safe_table_name


@dataclass(frozen=True)
class ScrubReport:
    pattern_version: int
    deleted_by_pattern: dict[str, int]
    tables_processed: tuple[str, ...]


def _has_triggers_or_views(conn: sqlite3.Connection) -> bool:
    v = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('view','trigger')"
    ).fetchone()[0]
    return int(v) > 0


def scrub_staged_kv_database(conn: sqlite3.Connection) -> ScrubReport:
    if _has_triggers_or_views(conn):
        raise ValueError("scrub refused: triggers or views present")
    conn.execute("PRAGMA secure_delete=ON")
    counts: Counter[str] = Counter()
    tables: list[str] = []
    table_rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    for (table,) in table_rows:
        if not safe_table_name(table):
            continue
        cols = {c[1] for c in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
        if "key" not in cols:
            continue
        tables.append(table)
        cur = conn.execute(f'SELECT key FROM "{table}"')
        keys_to_delete = [
            row[0] for row in cur if isinstance(row[0], str) and is_credential_key(row[0])
        ]
        for key in keys_to_delete:
            pid = pattern_id_for_key(key) or "unknown"
            conn.execute(f'DELETE FROM "{table}" WHERE key = ?', (key,))
            counts[pid] += 1
    conn.commit()
    return ScrubReport(1, dict(counts), tuple(tables))


def vacuum_into(conn: sqlite3.Connection, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    conn.execute("VACUUM INTO ?", (str(dest),))


def any_credential_keys_remain(conn: sqlite3.Connection) -> bool:
    for (table,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ):
        if not safe_table_name(table):
            continue
        cols = {c[1] for c in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
        if "key" not in cols:
            continue
        cur = conn.execute(f'SELECT key FROM "{table}"')
        for (key,) in cur:
            if isinstance(key, str) and is_credential_key(key):
                return True
    return False
