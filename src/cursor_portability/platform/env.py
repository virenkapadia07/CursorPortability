"""Injected environment snapshot (only CLI reads os.environ directly)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Environment:
    home: Path
    platform: str
    os_name: str
    xdg_config_home: Path | None
    xdg_data_home: Path | None
    appdata: Path | None
    userprofile: Path | None
    localappdata: Path | None
    wsl_distro_name: str | None


def current_environment() -> Environment:
    home = Path(os.environ.get("HOME") or os.environ.get("USERPROFILE") or ".").expanduser()
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    xdg_data = os.environ.get("XDG_DATA_HOME")
    appdata = os.environ.get("APPDATA")
    userprofile = os.environ.get("USERPROFILE")
    localappdata = os.environ.get("LOCALAPPDATA")
    return Environment(
        home=home.resolve(),
        platform=sys.platform,
        os_name=os.name,
        xdg_config_home=Path(xdg_config).expanduser().resolve() if xdg_config else None,
        xdg_data_home=Path(xdg_data).expanduser().resolve() if xdg_data else None,
        appdata=Path(appdata).expanduser().resolve() if appdata else None,
        userprofile=Path(userprofile).expanduser().resolve() if userprofile else None,
        localappdata=Path(localappdata).expanduser().resolve() if localappdata else None,
        wsl_distro_name=os.environ.get("WSL_DISTRO_NAME"),
    )
