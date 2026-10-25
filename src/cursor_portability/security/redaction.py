from __future__ import annotations

import hashlib
from pathlib import Path


def redact_path(path: Path | str, home: Path | None, shareable: bool = False) -> str:
    s = str(path)
    if home:
        home_s = str(home)
        if s.startswith(home_s):
            s = "~" + s[len(home_s) :]
    if not shareable:
        return s
    parts = s.replace("\\", "/").split("/")
    hashed: list[str] = []
    for p in parts:
        if p in ("", "~", "."):
            hashed.append(p)
        else:
            hashed.append(_hash_segment(p))
    return "/".join(hashed)


def _hash_segment(segment: str) -> str:
    return hashlib.sha256(segment.encode()).hexdigest()[:12]
