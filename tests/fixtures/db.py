from __future__ import annotations

import sqlite3
from pathlib import Path

KV_TABLE_SQL = "CREATE TABLE {name} (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)"

# Table definitions as observed in a real Cursor profile's global and workspace state.vscdb.
_COMPOSER_HEADERS_PREFIX = (
    "CREATE TABLE composerHeaders (composerId TEXT PRIMARY KEY, workspaceId TEXT, "
    "createdAt INTEGER, lastUpdatedAt INTEGER, isArchived INTEGER, isSubagent INTEGER, "
    "recency INTEGER, checkpointAt INTEGER, "
)
COMPOSER_HEADERS_SQL = {
    1: _COMPOSER_HEADERS_PREFIX + "value TEXT, subagentTypeName TEXT)",
    2: _COMPOSER_HEADERS_PREFIX + "subagentTypeName TEXT, value TEXT)",
    3: _COMPOSER_HEADERS_PREFIX + "value TEXT)",
}
COMPOSER_HEADERS_INDEX_SQL = (
    "CREATE INDEX idx_composerHeaders_0 ON composerHeaders "
    "(workspaceId, isSubagent, isArchived, recency)",
    "CREATE INDEX idx_composerHeaders_1 ON composerHeaders (recency, composerId)",
)


def make_kv_db(path: Path, rows: list[tuple[str, bytes]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.execute(KV_TABLE_SQL.format(name="ItemTable"))
    for key, val in rows:
        conn.execute("INSERT INTO ItemTable (key, value) VALUES (?, ?)", (key, val))
    conn.commit()
    conn.close()


def make_composer_headers_db(
    path: Path,
    item_rows: list[tuple[str, bytes]],
    disk_kv_rows: list[tuple[str, bytes]],
    header_ids: list[str],
    layout: int = 1,
) -> None:
    """Three-table layout of the current Cursor state.vscdb, with synthetic rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(KV_TABLE_SQL.format(name="ItemTable"))
    conn.execute(KV_TABLE_SQL.format(name="cursorDiskKV"))
    conn.execute(COMPOSER_HEADERS_SQL[layout])
    for sql in COMPOSER_HEADERS_INDEX_SQL:
        conn.execute(sql)
    conn.executemany("INSERT INTO ItemTable (key, value) VALUES (?, ?)", item_rows)
    conn.executemany("INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)", disk_kv_rows)
    conn.executemany(
        "INSERT INTO composerHeaders (composerId, workspaceId, value) VALUES (?, ?, ?)",
        [(cid, "ws-1", "{}") for cid in header_ids],
    )
    conn.commit()
    conn.close()


def make_wal_only_kv_db(path: Path, rows: list[tuple[str, bytes]]) -> None:
    """KV db whose content lives only in an un-checkpointed -wal, as seen in real workspaces."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute(KV_TABLE_SQL.format(name="ItemTable"))
    conn.executemany("INSERT INTO ItemTable (key, value) VALUES (?, ?)", rows)
    conn.commit()
    conn.close()
    assert path.stat().st_size == 4096, "main file should hold only the header page"
    assert Path(f"{path}-wal").stat().st_size > 0
