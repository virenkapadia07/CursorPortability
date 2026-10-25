from __future__ import annotations

from pathlib import Path

from cursor_portability.security.json_strict import loads_strict


def collect_profile_workspaces(user_data_dir: Path) -> list[tuple[str, str]]:
    """(workspace_id, folder_uri) from a live or copied profile user-data root."""
    ws_root = user_data_dir / "User" / "workspaceStorage"
    if not ws_root.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for child in ws_root.iterdir():
        if not child.is_dir():
            continue
        wj = child / "workspace.json"
        if not wj.is_file():
            continue
        try:
            data = loads_strict(wj.read_text(encoding="utf-8"), max_size=65536)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        folder = data.get("folder") or data.get("workspace")
        if isinstance(folder, str):
            out.append((child.name, folder))
    return out
