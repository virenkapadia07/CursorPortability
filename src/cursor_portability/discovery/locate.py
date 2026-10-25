from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cursor_portability.platform.types import PlatformPaths


@dataclass(frozen=True)
class ProfileCandidate:
    user_data_dir: Path
    cursor_home: Path | None
    is_backup_tree: bool
    label: str


def locate_profiles(
    platform_paths: PlatformPaths,
    user_data_override: Path | None = None,
    cursor_home_override: Path | None = None,
) -> list[ProfileCandidate]:
    candidates: list[ProfileCandidate] = []
    ud = user_data_override or platform_paths.user_data_dir
    ch = cursor_home_override or platform_paths.cursor_home
    if ud and ud.is_dir():
        is_backup = ud.name.startswith("User.backup")
        if ud.name == "User":
            ud = ud.parent
            is_backup = False
        candidates.append(
            ProfileCandidate(
                user_data_dir=ud.resolve(),
                cursor_home=ch.resolve() if ch and ch.is_dir() else None,
                is_backup_tree=is_backup or _has_user_backup_sibling(ud),
                label="primary",
            )
        )
    return candidates


def _has_user_backup_sibling(user_data: Path) -> bool:
    parent = user_data.parent
    for child in parent.iterdir():
        if child.name.startswith("User.backup"):
            return False  # sibling exists but this is primary user_data root
    return False


def is_non_profile_backup_dir(path: Path) -> bool:
    return path.name.startswith("User.backup")
