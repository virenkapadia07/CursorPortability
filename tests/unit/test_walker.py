import os
import sys
from pathlib import Path

import pytest

from cursor_portability.discovery.walker import DEPTH_LIMIT, UNREADABLE, WalkEntry, walk_root

needs_posix_permissions = pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="needs POSIX permission bits and a non-root user",
)


def _by_path(root: Path, max_depth: int = 8) -> dict[str, WalkEntry]:
    return {e.relative_path: e for e in walk_root(root, "user-data", max_depth=max_depth)}


def test_depth_limit_is_recorded_instead_of_dropped(tmp_path: Path) -> None:
    (tmp_path / "a" / "b" / "c").mkdir(parents=True)
    (tmp_path / "a" / "b" / "c" / "deep.txt").write_text("x")
    (tmp_path / "a" / "empty").mkdir()
    (tmp_path / "a" / "f.txt").write_text("x")

    entries = _by_path(tmp_path, max_depth=1)

    assert entries["a/b"].skip_reason == DEPTH_LIMIT
    assert "a/b/c" not in entries
    assert entries["a/empty"].skip_reason is None
    assert entries["a/f.txt"].skip_reason is None
    assert entries["a/f.txt"].size == 1


@needs_posix_permissions
def test_unreadable_directory_is_recorded(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "inner.txt").write_text("x")
    locked.chmod(0)
    try:
        entries = _by_path(tmp_path)
    finally:
        locked.chmod(0o700)
    assert entries["locked"].skip_reason == UNREADABLE
    assert "locked/inner.txt" not in entries


@needs_posix_permissions
def test_unreadable_root_is_recorded(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    root.chmod(0)
    try:
        entries = walk_root(root, "user-data")
    finally:
        root.chmod(0o700)
    assert [(e.relative_path, e.skip_reason) for e in entries] == [("", UNREADABLE)]
