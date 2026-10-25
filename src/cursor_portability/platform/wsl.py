from __future__ import annotations

from pathlib import Path

from cursor_portability.platform.env import Environment


def windows_side_cursor_home(env: Environment) -> Path | None:
    """Experimental: path to Windows user's .cursor under /mnt/c when present."""
    mnt = Path("/mnt/c/Users")
    if not mnt.is_dir():
        return None
    # Cannot know Windows username from env reliably; list is for detect notes only
    for user_dir in sorted(mnt.iterdir()):
        if user_dir.name in ("Public", "Default", "Default User", "All Users"):
            continue
        candidate = user_dir / ".cursor"
        if candidate.is_dir():
            return candidate
    return None
