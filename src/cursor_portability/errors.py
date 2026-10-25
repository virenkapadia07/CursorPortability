"""Stable error codes and application exceptions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ErrorCode(StrEnum):
    PROFILE_NOT_FOUND = "PROFILE_NOT_FOUND"
    CURSOR_RUNNING = "CURSOR_RUNNING"
    UNKNOWN_SCHEMA = "UNKNOWN_SCHEMA"
    UNSUPPORTED_SESSION_TYPE = "UNSUPPORTED_SESSION_TYPE"
    PATH_MAPPING_MISSING = "PATH_MAPPING_MISSING"
    PATH_MAPPING_AMBIGUOUS = "PATH_MAPPING_AMBIGUOUS"
    SOURCE_CHANGED = "SOURCE_CHANGED"
    TARGET_CHANGED = "TARGET_CHANGED"
    INSUFFICIENT_DISK_SPACE = "INSUFFICIENT_DISK_SPACE"
    BACKUP_INVALID = "BACKUP_INVALID"
    SQLITE_INTEGRITY_FAILED = "SQLITE_INTEGRITY_FAILED"
    REFERENCE_INTEGRITY_FAILED = "REFERENCE_INTEGRITY_FAILED"
    DESTINATION_CONFLICT = "DESTINATION_CONFLICT"
    APPLY_FAILED_ROLLBACK_SUCCEEDED = "APPLY_FAILED_ROLLBACK_SUCCEEDED"
    APPLY_FAILED_ROLLBACK_INCOMPLETE = "APPLY_FAILED_ROLLBACK_INCOMPLETE"
    UNSAFE_PATH = "UNSAFE_PATH"
    OUTPUT_EXISTS = "OUTPUT_EXISTS"
    ARCHIVE_REJECTED = "ARCHIVE_REJECTED"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"


class ExitCode:
    PASS = 0
    FAIL = 1
    USAGE = 2
    PARTIAL = 3
    UNKNOWN = 4
    PRECONDITION = 5


# Not frozen: contextlib assigns __traceback__ when re-raising through @contextmanager.
@dataclass(eq=False)
class AppError(Exception):
    code: ErrorCode
    message: str
    details: dict[str, object] | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"
