from __future__ import annotations

from pathlib import Path

from cursor_portability.platform.env import Environment
from cursor_portability.platform.types import PlatformPaths


def _xdg_config(env: Environment) -> Path:
    if env.xdg_config_home and env.xdg_config_home.is_absolute():
        return env.xdg_config_home
    return env.home / ".config"


def resolve_paths(env: Environment) -> PlatformPaths:
    config = _xdg_config(env)
    user_data = config / "Cursor"
    cursor_home = env.home / ".cursor"
    candidates: tuple[Path, ...] = (
        Path("/usr/share/cursor"),
        Path("/opt/Cursor"),
    )
    found = tuple(p for p in candidates if p.exists())
    return PlatformPaths(
        user_data_dir=user_data if user_data.exists() else None,
        cursor_home=cursor_home if cursor_home.exists() else None,
        app_install_candidates=found,
    )
