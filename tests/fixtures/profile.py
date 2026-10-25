from __future__ import annotations

from pathlib import Path

from tests.fixtures.db import make_composer_headers_db, make_kv_db, make_wal_only_kv_db

CANARY = b"CANARY_SECRET_VALUE"
WAL_ONLY_WORKSPACE = "fedcba9876543210fedcba9876543210"
LAYOUT_WORKSPACES = {
    2: "22222222222222222222222222222222",
    3: "33333333333333333333333333333333",
}


def user_data_dir_for(home: Path, platform: str) -> Path:
    """Independent oracle for where each platform resolver should look."""
    if platform == "darwin":
        return home / "Library" / "Application Support" / "Cursor"
    if platform == "win32":
        return home / "AppData" / "Roaming" / "Cursor"
    return home / ".config" / "Cursor"


def build_synthetic_profile(home: Path, platform: str) -> Path:
    ud = user_data_dir_for(home, platform)
    user = ud / "User"
    (user / "snippets").mkdir(parents=True)
    (user / "settings.json").write_text(
        '{"editor.fontSize": 14, "hello.key": "world"}', encoding="utf-8"
    )
    make_composer_headers_db(
        user / "globalStorage" / "state.vscdb",
        item_rows=[("hello.key", b"world"), ("auth.token", CANARY)],
        disk_kv_rows=[("synthetic.entry:1", b"{}"), ("synthetic.apiKey", CANARY)],
        header_ids=["c-1", "c-2"],
    )
    ws = user / "workspaceStorage" / "abc123def4567890abc123def4567890"
    ws.mkdir(parents=True)
    (ws / "workspace.json").write_text('{"folder": "file:///tmp/project"}', encoding="utf-8")
    make_kv_db(ws / "state.vscdb", [("ws.key", b"data")])
    make_wal_only_kv_db(
        user / "workspaceStorage" / WAL_ONLY_WORKSPACE / "state.vscdb",
        [("wal.only.key", b"kept")],
    )
    for layout, ws_id in LAYOUT_WORKSPACES.items():
        make_composer_headers_db(
            user / "workspaceStorage" / ws_id / "state.vscdb",
            item_rows=[("ws.key", b"data")],
            disk_kv_rows=[],
            header_ids=[f"layout-{layout}"],
            layout=layout,
        )
    (ud / "logs").mkdir()
    (ud / "logs" / "main.log").write_text("log line\n", encoding="utf-8")

    ch = home / ".cursor"
    (ch / "extensions").mkdir(parents=True)
    (ch / "extensions" / "extensions.json").write_text("[]", encoding="utf-8")
    auth_dir = ch / "projects" / "demo"
    auth_dir.mkdir(parents=True)
    (auth_dir / "mcp-auth.json").write_text('{"token": "CANARY_SECRET_VALUE"}', encoding="utf-8")
    return ud
