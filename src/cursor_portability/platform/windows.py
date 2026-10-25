from __future__ import annotations

from pathlib import Path

from cursor_portability.platform.env import Environment
from cursor_portability.platform.types import PlatformPaths


def resolve_paths(env: Environment) -> PlatformPaths:
    if not env.appdata or not env.userprofile:
        return PlatformPaths(None, None, (), notes=("APPDATA/USERPROFILE not set",))
    user_data = env.appdata / "Cursor"
    cursor_home = env.userprofile / ".cursor"
    local = env.localappdata or (env.userprofile / "AppData" / "Local")
    candidates = tuple(
        p
        for p in (
            local / "Programs" / "cursor" / "Cursor.exe",
            Path("C:/Program Files/Cursor/Cursor.exe"),
        )
        if p.exists()
    )
    return PlatformPaths(
        user_data_dir=user_data if user_data.exists() else None,
        cursor_home=cursor_home if cursor_home.exists() else None,
        app_install_candidates=candidates,
    )
