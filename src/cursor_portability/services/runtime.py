from __future__ import annotations

import platform
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SqliteCapabilities:
    version: str
    vacuum_into: bool
    defensive: bool
    json1: bool


def probe_sqlite() -> SqliteCapabilities:
    ver = sqlite3.sqlite_version
    vacuum = False
    defensive = False
    json1 = False
    try:
        conn = sqlite3.connect(":memory:")
        try:
            with tempfile.TemporaryDirectory() as td:
                target = Path(td) / "probe.db"
                conn.execute("VACUUM INTO ?", (str(target),))
            vacuum = True
        except sqlite3.OperationalError:
            vacuum = False
        try:
            conn.execute("SELECT json('{}')")
            json1 = True
        except sqlite3.OperationalError:
            json1 = False
        try:
            conn.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
            defensive = True
        except (AttributeError, sqlite3.OperationalError):
            defensive = False
        conn.close()
    except sqlite3.Error:
        pass
    return SqliteCapabilities(ver, vacuum, defensive, json1)


def runtime_info() -> dict[str, str]:
    return {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "os": platform.system(),
        "arch": platform.machine(),
    }
