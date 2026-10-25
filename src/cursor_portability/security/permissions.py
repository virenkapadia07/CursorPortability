from __future__ import annotations

from pathlib import Path

_protected: list[Path] = []


def register_protected_roots(roots: tuple[Path, ...]) -> None:
    global _protected
    _protected = [p.resolve() for p in roots if p.exists()]


def clear_protected_roots() -> None:
    global _protected
    _protected = []


def is_under_protected(path: Path) -> bool:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return False
    for root in _protected:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def get_protected_roots() -> tuple[Path, ...]:
    return tuple(_protected)
