from __future__ import annotations

import os
from pathlib import Path

from cursor_portability.errors import AppError, ErrorCode
from cursor_portability.planning.targets import is_allowed_target
from cursor_portability.storage.fs_safe import new_run_id


def assert_allowed_target(rel: str) -> None:
    if not is_allowed_target(rel):
        raise AppError(ErrorCode.UNSAFE_PATH, "target not on migration allowlist", {"path": rel})


def write_text_atomic(dest: Path, text: str) -> None:
    if dest.is_symlink():
        raise AppError(ErrorCode.UNSAFE_PATH, "refusing to write through symlink")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + f".partial-{new_run_id()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(tmp), flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(text.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
