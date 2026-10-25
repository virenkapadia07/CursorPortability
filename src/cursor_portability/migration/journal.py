from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from cursor_portability.errors import AppError, ErrorCode
from cursor_portability.security.json_strict import dumps_canonical


def append_journal(run_dir: Path, record: dict[str, Any]) -> None:
    path = run_dir / "journal.jsonl"
    line = dumps_canonical(record) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    fd = os.open(str(path), flags, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def acquire_lock(run_dir: Path) -> None:
    lock = run_dir / ".lock"
    if lock.exists():
        raise AppError(ErrorCode.RUN_INCOMPLETE, "migration run lock present", {"run": str(run_dir)})
    lock.write_text("locked\n", encoding="utf-8")


def release_lock(run_dir: Path) -> None:
    lock = run_dir / ".lock"
    if lock.is_file():
        lock.unlink()
