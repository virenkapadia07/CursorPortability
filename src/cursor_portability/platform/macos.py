from __future__ import annotations

from pathlib import Path

from cursor_portability.platform.env import Environment
from cursor_portability.platform.types import PlatformPaths


def resolve_paths(env: Environment) -> PlatformPaths:
    user_data = env.home / "Library" / "Application Support" / "Cursor"
    cursor_home = env.home / ".cursor"
    apps = (
        Path("/Applications/Cursor.app"),
        env.home / "Applications" / "Cursor.app",
    )
    candidates = tuple(p for p in apps if p.exists())
    return PlatformPaths(
        user_data_dir=user_data if user_data.exists() else None,
        cursor_home=cursor_home if cursor_home.exists() else None,
        app_install_candidates=candidates,
    )
