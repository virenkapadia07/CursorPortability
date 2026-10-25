from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PlatformPaths:
    user_data_dir: Path | None
    cursor_home: Path | None
    app_install_candidates: tuple[Path, ...]
    wsl_windows_cursor_home: Path | None = None
    notes: tuple[str, ...] = ()
