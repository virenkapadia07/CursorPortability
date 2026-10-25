"""Sole module for filesystem writes and deletes (except scrub SQL on staged DBs)."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from cursor_portability.errors import AppError, ErrorCode
from cursor_portability.security.permissions import is_under_protected


class RunMarker:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def assert_not_protected(path: Path) -> None:
    if is_under_protected(path):
        raise AppError(ErrorCode.UNSAFE_PATH, "path is inside a protected Cursor profile")


def assert_safe_output(path: Path) -> None:
    assert_not_protected(path)
    if path.exists():
        raise AppError(ErrorCode.OUTPUT_EXISTS, "output already exists", {"path": str(path)})


def create_private_dir(parent: Path, name: str) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / name
    if is_under_protected(target):
        raise AppError(ErrorCode.UNSAFE_PATH, "cannot create dir in protected root")
    target.mkdir(mode=0o700, parents=False, exist_ok=False)
    return target


def write_private_file(path: Path, data: bytes) -> None:
    if is_under_protected(path):
        raise AppError(ErrorCode.UNSAFE_PATH, "cannot write in protected root")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(path), flags, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def copy_file_atomic(src: Path, dest: Path) -> None:
    if is_under_protected(dest):
        raise AppError(ErrorCode.UNSAFE_PATH, "cannot write in protected root")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + f".partial-{new_run_id()}")
    shutil.copy2(src, tmp)
    os.chmod(tmp, 0o600)
    tmp.replace(dest)


def publish_path(artifact: Path, final: Path, replace_existing: bool = False) -> None:
    """Move a verified file or directory into place.

    An existing target is only removed after the new artifact is in place, and is
    restored if the move fails.
    """
    if is_under_protected(final):
        raise AppError(ErrorCode.UNSAFE_PATH, "cannot publish into protected root")
    if not final.exists() and not final.is_symlink():
        artifact.rename(final)
        return
    if not replace_existing:
        raise AppError(ErrorCode.OUTPUT_EXISTS, "publish target exists")
    retired = final.with_name(f"{final.name}.old-{new_run_id()}")
    final.rename(retired)
    try:
        artifact.rename(final)
    except BaseException:
        retired.rename(final)
        raise
    if retired.is_dir() and not retired.is_symlink():
        shutil.rmtree(retired)
    else:
        retired.unlink()


def remove_tree_guarded(path: Path, marker: RunMarker, allowed_root: Path) -> None:
    """Remove only trees we created under allowed_root."""
    try:
        resolved = path.resolve()
        root = allowed_root.resolve()
        resolved.relative_to(root)
    except ValueError:
        raise AppError(ErrorCode.UNSAFE_PATH, "refusing to remove path outside staging root")
    if not str(resolved).endswith(marker.run_id) and marker.run_id not in resolved.name:
        # allow partial-* dirs
        if ".partial-" not in resolved.name:
            raise AppError(ErrorCode.UNSAFE_PATH, "refusing unmarked removal")
    if resolved.is_dir():
        shutil.rmtree(resolved)
    elif resolved.is_file():
        resolved.unlink()
