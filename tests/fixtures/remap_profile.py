from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from tests.fixtures.db import COMPOSER_HEADERS_INDEX_SQL, COMPOSER_HEADERS_SQL, KV_TABLE_SQL


def build_ubuntu_workspace_profile(
    user_data: Path,
    ubuntu_ws_id: str,
    ubuntu_project_path: str,
    mac_project_path: Path,
    composer_rows: int = 2,
) -> None:
    """Profile with one Ubuntu-pathed workspace and a matching Mac project folder."""
    mac_project_path.mkdir(parents=True, exist_ok=True)
    ws = user_data / "User" / "workspaceStorage" / ubuntu_ws_id
    ws.mkdir(parents=True)
    uri = f"file://{ubuntu_project_path}"
    (ws / "workspace.json").write_text(
        f'{{"folder": "{uri}"}}',
        encoding="utf-8",
    )
    make_global_with_composer(
        user_data / "User" / "globalStorage" / "state.vscdb",
        ubuntu_ws_id,
        composer_rows,
    )
    make_kv_db(ws / "state.vscdb", [("ws.only", b"1")])


def add_ubuntu_workspace(
    user_data: Path, ubuntu_ws_id: str, ubuntu_project_path: str, composer_rows: int
) -> Path:
    """Another Ubuntu workspace folder (for example after a re-clone) with its own chats."""
    ws = user_data / "User" / "workspaceStorage" / ubuntu_ws_id
    ws.mkdir(parents=True)
    (ws / "workspace.json").write_text(
        f'{{"folder": "file://{ubuntu_project_path}"}}', encoding="utf-8"
    )
    make_kv_db(ws / "state.vscdb", [("ws.only", b"1")])
    add_composer_rows(
        user_data / "User" / "globalStorage" / "state.vscdb",
        ubuntu_ws_id,
        composer_rows,
        ubuntu_ws_id[:4],
    )
    return ws


def make_kv_db(path: Path, rows: list[tuple[str, bytes]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(KV_TABLE_SQL.format(name="ItemTable"))
    for key, val in rows:
        conn.execute("INSERT INTO ItemTable (key, value) VALUES (?, ?)", (key, val))
    conn.commit()
    conn.close()


def make_global_with_composer(path: Path, workspace_id: str, row_count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(KV_TABLE_SQL.format(name="ItemTable"))
    conn.execute(KV_TABLE_SQL.format(name="cursorDiskKV"))
    conn.execute(COMPOSER_HEADERS_SQL[1])
    for sql in COMPOSER_HEADERS_INDEX_SQL:
        conn.execute(sql)
    for i in range(row_count):
        conn.execute(
            "INSERT INTO composerHeaders (composerId, workspaceId, value) VALUES (?, ?, ?)",
            (f"c-{i}", workspace_id, "{}"),
        )
    conn.commit()
    conn.close()


def add_composer_rows(path: Path, workspace_id: str, row_count: int, id_prefix: str) -> None:
    conn = sqlite3.connect(path)
    for i in range(row_count):
        conn.execute(
            "INSERT INTO composerHeaders (composerId, workspaceId, value) VALUES (?, ?, ?)",
            (f"{id_prefix}-{i}", workspace_id, "{}"),
        )
    conn.commit()
    conn.close()


def leave_hot_wal(path: Path) -> None:
    """Rewrite ``path`` so its last commit lives only in ``-wal``, as after Cursor quits uncleanly."""
    work = path.parent / "wal-work"
    work.mkdir()
    shutil.copy2(path, work / path.name)
    conn = sqlite3.connect(work / path.name)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("INSERT INTO ItemTable (key, value) VALUES (?, ?)", ("wal.only", b"1"))
    conn.commit()
    for suffix in ("", "-wal", "-shm"):
        shutil.copy2(f"{work / path.name}{suffix}", f"{path}{suffix}")
    conn.close()
    shutil.rmtree(work)
