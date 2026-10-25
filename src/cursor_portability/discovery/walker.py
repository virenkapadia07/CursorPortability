from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

DEPTH_LIMIT = "DEPTH_LIMIT"
UNREADABLE = "UNREADABLE"


@dataclass(frozen=True)
class WalkEntry:
    root_token: str
    relative_path: str
    is_dir: bool
    size: int
    is_symlink: bool
    is_junction: bool
    # DEPTH_LIMIT or UNREADABLE when this entry's contents were not accounted for.
    # An empty relative_path means the root itself.
    skip_reason: str | None = None


def _list_dir(path: Path) -> list[os.DirEntry[str]] | None:
    try:
        with os.scandir(path) as it:
            return list(it)
    except OSError:
        return None


def walk_root(root: Path, root_token: str, max_depth: int = 8) -> list[WalkEntry]:
    entries: list[WalkEntry] = []
    if not root.is_dir():
        return entries
    root = root.resolve()

    def _add(
        rel: str,
        *,
        is_dir: bool = False,
        size: int = 0,
        is_symlink: bool = False,
        is_junction: bool = False,
        skip_reason: str | None = None,
    ) -> None:
        entries.append(
            WalkEntry(root_token, rel, is_dir, size, is_symlink, is_junction, skip_reason)
        )

    def _walk(current: Path, rel: str, depth: int, children: list[os.DirEntry[str]]) -> None:
        for dirent in children:
            child_rel = f"{rel}/{dirent.name}" if rel else dirent.name
            is_symlink = dirent.is_symlink()
            is_junction = dirent.is_junction()
            if is_symlink or is_junction:
                _add(
                    child_rel,
                    is_dir=dirent.is_dir(follow_symlinks=False),
                    is_symlink=is_symlink,
                    is_junction=is_junction,
                )
                continue
            try:
                st = dirent.stat(follow_symlinks=False)
            except OSError:
                _add(child_rel, skip_reason=UNREADABLE)
                continue
            if not stat.S_ISDIR(st.st_mode):
                _add(child_rel, size=st.st_size)
                continue
            grandchildren = _list_dir(current / dirent.name)
            if grandchildren is None:
                _add(child_rel, is_dir=True, skip_reason=UNREADABLE)
            elif grandchildren and depth >= max_depth:
                _add(child_rel, is_dir=True, skip_reason=DEPTH_LIMIT)
            else:
                _add(child_rel, is_dir=True)
                _walk(current / dirent.name, child_rel, depth + 1, grandchildren)

    top = _list_dir(root)
    if top is None:
        _add("", is_dir=True, skip_reason=UNREADABLE)
    else:
        _walk(root, "", 0, top)
    return entries
