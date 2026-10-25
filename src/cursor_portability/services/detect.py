from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cursor_portability.discovery.locate import locate_profiles
from cursor_portability.platform.base import get_platform_paths
from cursor_portability.platform.env import Environment


@dataclass(frozen=True)
class DetectEntry:
    user_data_dir: str
    cursor_home: str | None
    is_backup_tree: bool


@dataclass(frozen=True)
class DetectReport:
    platform_notes: tuple[str, ...]
    profiles: tuple[DetectEntry, ...]
    wsl_windows_cursor_home: str | None


def run_detect(env: Environment, user_data_override: Path | None = None) -> DetectReport:
    paths = get_platform_paths(env)
    profiles = locate_profiles(paths, user_data_override)
    entries = tuple(
        DetectEntry(
            str(p.user_data_dir),
            str(p.cursor_home) if p.cursor_home else None,
            p.is_backup_tree,
        )
        for p in profiles
    )
    wsl = str(paths.wsl_windows_cursor_home) if paths.wsl_windows_cursor_home else None
    return DetectReport(tuple(paths.notes), entries, wsl)
