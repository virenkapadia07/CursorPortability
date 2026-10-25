from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath

from cursor_portability.errors import AppError, ErrorCode

# A single scrubbed globalStorage DB can be several GB on a real profile.
_MAX_ENTRY_BYTES = 16 * 1024 * 1024 * 1024
_MAX_TOTAL_BYTES = 64 * 1024 * 1024 * 1024
_MAX_ENTRIES = 100_000
_MAX_RATIO = 200
# Tiny, highly repetitive files legitimately exceed any useful ratio cap.
_RATIO_MIN_BYTES = 1024 * 1024

_RESERVED_WIN = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_INVALID_RE = re.compile(r"[\x00\\]|:|^[A-Za-z]:|^//")


def validate_archive_member_name(name: str) -> None:
    if not name or name.strip() != name:
        raise AppError(ErrorCode.ARCHIVE_REJECTED, "invalid archive member name")
    if "\x00" in name or "\\" in name:
        raise AppError(ErrorCode.ARCHIVE_REJECTED, "backslash or nul in name")
    if name.startswith("/") or name.startswith("../") or "/../" in name:
        raise AppError(ErrorCode.ARCHIVE_REJECTED, "path traversal")
    if _INVALID_RE.search(name):
        raise AppError(ErrorCode.ARCHIVE_REJECTED, "absolute or drive path")
    parts = PurePosixPath(name).parts
    for part in parts:
        if part in (".", ".."):
            raise AppError(ErrorCode.ARCHIVE_REJECTED, "dot segment")
        base = part.rstrip(". ")
        if base.upper() in _RESERVED_WIN:
            raise AppError(ErrorCode.ARCHIVE_REJECTED, "reserved name")
    nfc = unicodedata.normalize("NFC", name)
    if nfc != name:
        raise AppError(ErrorCode.ARCHIVE_REJECTED, "NFC normalization required")


def check_bomb_limits(
    uncompressed: int,
    compressed: int,
    total_uncompressed: int,
    entry_count: int,
) -> None:
    if entry_count > _MAX_ENTRIES:
        raise AppError(ErrorCode.ARCHIVE_REJECTED, "too many entries")
    if uncompressed > _MAX_ENTRY_BYTES:
        raise AppError(ErrorCode.ARCHIVE_REJECTED, "entry too large")
    if total_uncompressed > _MAX_TOTAL_BYTES:
        raise AppError(ErrorCode.ARCHIVE_REJECTED, "archive too large")
    if uncompressed >= _RATIO_MIN_BYTES and uncompressed > compressed * _MAX_RATIO:
        raise AppError(ErrorCode.ARCHIVE_REJECTED, "compression ratio exceeded")
