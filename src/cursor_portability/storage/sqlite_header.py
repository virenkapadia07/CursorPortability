from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SqliteHeader:
    page_size: int
    page_count: int
    encoding: int
    user_version: int
    wal_mode_hint: bool


def read_sqlite_header(path: Path) -> SqliteHeader:
    with path.open("rb") as f:
        header = f.read(100)
    if len(header) < 100 or header[0:16] != b"SQLite format 3\x00":
        raise ValueError("not a SQLite database")
    page_size = struct.unpack(">H", header[16:18])[0]
    if page_size == 1:
        page_size = 65536
    page_count = struct.unpack(">I", header[28:32])[0]
    encoding = struct.unpack(">I", header[56:60])[0]
    user_version = struct.unpack(">I", header[60:64])[0]
    wal_path = Path(str(path) + "-wal")
    wal_mode_hint = wal_path.exists()
    return SqliteHeader(page_size, page_count, encoding, user_version, wal_mode_hint)
