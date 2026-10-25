from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from cursor_portability.storage.paths import BundlePath, StagedPath

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@contextmanager
def open_readonly_db(handle: StagedPath | BundlePath) -> Iterator[sqlite3.Connection]:
    path = handle.path
    uri = f"file:{path.as_posix()}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    try:
        _harden_connection(conn)
        yield conn
    finally:
        conn.close()


def _harden_connection(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA trusted_schema=OFF")
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA mmap_size=0")
    conn.execute("PRAGMA cell_size_check=ON")
    try:
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 64 * 1024 * 1024)
        conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 1_000_000)
    except AttributeError:
        pass
    try:
        conn.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
    except (AttributeError, sqlite3.OperationalError):
        pass


@contextmanager
def open_staged_writable(handle: StagedPath) -> Iterator[sqlite3.Connection]:
    """Writable connection for scrub on staged copy only."""
    conn = sqlite3.connect(str(handle.path))
    try:
        conn.execute("PRAGMA trusted_schema=OFF")
        yield conn
    finally:
        conn.close()


def safe_table_name(name: str) -> bool:
    return bool(_IDENT_RE.match(name))


def table_info(conn: sqlite3.Connection, table: str) -> list[tuple[str, str]]:
    if not safe_table_name(table):
        raise ValueError("invalid table name")
    cur = conn.execute(f'PRAGMA table_info("{table}")')
    return [(row[1], row[2]) for row in cur.fetchall()]
