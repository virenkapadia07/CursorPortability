from __future__ import annotations

import hashlib
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path

from cursor_portability.errors import AppError, ErrorCode

StatFn = Callable[[os.PathLike[str] | str], os.stat_result]

_WORKSPACE_ID_RE = re.compile(r"[0-9a-f]{32}")


def is_folder_workspace_id(value: object) -> bool:
    """True for the md5-hex folder names VS Code/Cursor use for folder and multi-root workspaces."""
    return isinstance(value, str) and _WORKSPACE_ID_RE.fullmatch(value) is not None


def workspace_id_for_folder(folder: Path, stat_fn: StatFn | None = None) -> str:
    """VS Code/Cursor workspaceStorage folder name for a local folder path."""
    if sys.platform != "darwin":
        raise AppError(
            ErrorCode.CAPABILITY_UNAVAILABLE,
            "workspace id formula verified on macOS only",
            {"platform": sys.platform},
        )
    resolved = folder.expanduser().resolve()
    if not resolved.is_dir():
        raise AppError(ErrorCode.TARGET_CHANGED, "folder does not exist", {"path": str(resolved)})
    st = (stat_fn or os.stat)(resolved)
    birth_ms = int(st.st_birthtime * 1000)
    payload = f"{resolved}{birth_ms}"
    return hashlib.md5(payload.encode("utf-8")).hexdigest()
