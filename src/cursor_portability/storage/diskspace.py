from __future__ import annotations

import shutil
from pathlib import Path


def available_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def estimate_backup_bytes(largest_db: int, bundle_overhead: int = 50 * 1024 * 1024) -> int:
    """Peak usage: largest staged DB x2 plus bundle margin."""
    return largest_db * 2 + bundle_overhead


def ensure_space(path: Path, required: int) -> bool:
    return available_bytes(path) >= required
