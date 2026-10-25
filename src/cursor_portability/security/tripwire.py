"""Runtime audit hook to block unsafe operations on protected paths."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import unquote

TripwireMode = Literal["off", "normal", "detect_only"]

_state: dict[str, object] = {
    "enabled": False,
    "mode": "off",
    "protected": (),
    "hook": None,
    "write_allowed": frozenset(),
}

_WRITE_OPEN_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND

# Audit event -> (indexes of path arguments that get modified, indexes of dir_fd arguments).
# Argument order follows CPython's PySys_Audit calls for each event.
_WRITE_EVENTS: dict[str, tuple[tuple[int, ...], tuple[int, ...]]] = {
    "os.rename": ((0, 1), (2, 3)),
    "os.remove": ((0,), (1,)),
    "os.rmdir": ((0,), (1,)),
    "os.mkdir": ((0,), (2,)),
    "os.chmod": ((0,), (2,)),
    "os.chown": ((0,), (3,)),
    "os.utime": ((0,), (3,)),
    "os.symlink": ((1,), (2,)),
    "os.link": ((1,), (2, 3)),
    "os.truncate": ((0,), ()),
    "os.chflags": ((0,), ()),
    "os.lchflags": ((0,), ()),
    "os.setxattr": ((0,), ()),
    "os.removexattr": ((0,), ()),
    "shutil.copyfile": ((1,), ()),
    "shutil.copytree": ((1,), ()),
    "shutil.move": ((0, 1), ()),
    "shutil.rmtree": ((0,), ()),
}


def _as_path_str(arg: object) -> str | None:
    if isinstance(arg, os.PathLike):
        arg = os.fspath(arg)
    if isinstance(arg, bytes):
        return os.fsdecode(arg)
    if isinstance(arg, str):
        return arg
    return None


def _path_under_protected(path_str: str, protected: tuple[Path, ...]) -> bool:
    try:
        resolved = Path(path_str).expanduser().resolve()
    except (OSError, ValueError):
        return False
    for root in protected:
        try:
            resolved.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def _entry_path(path: Path) -> Path:
    """Resolve the parent only, so a symlink at the final component is not followed."""
    path = path.expanduser()
    return path.parent.resolve() / path.name


def _write_allowed(path_str: str, event: str) -> bool:
    allowed = _state["write_allowed"]
    if not isinstance(allowed, frozenset) or not allowed:
        return False
    try:
        entry = _entry_path(Path(path_str))
    except (OSError, ValueError):
        return False
    for target in allowed:
        if entry == target:
            return True
        try:
            entry.relative_to(target)
            return True
        except ValueError:
            pass
        if entry.parent == target.parent and entry.name.startswith(f"{target.name}.partial-"):
            return True
        if event == "os.mkdir" and entry in target.parents:
            return True
    return False


def _sqlite_candidate_paths(database: str) -> list[str]:
    """Paths ``sqlite3.connect`` may open; the audit event does not say whether ``uri=True``."""
    if database in ("", ":memory:"):
        return []
    paths = [database]
    if database.startswith("file:"):
        rest = database[len("file:") :].split("#", 1)[0].split("?", 1)[0]
        if rest.startswith("//"):
            rest = "/" + rest[2:].partition("/")[2]
        decoded = unquote(rest)
        if decoded and decoded != ":memory:":
            paths.append(decoded)
    return paths


def _check_write(path_str: str, event: str, protected: tuple[Path, ...]) -> None:
    if _path_under_protected(path_str, protected) and not _write_allowed(path_str, event):
        raise RuntimeError(f"tripwire: {event} on protected path blocked")


def _audit_hook(event: str, args: tuple[object, ...]) -> None:
    if not _state["enabled"]:
        return
    mode = _state["mode"]
    protected = _state["protected"]
    if not isinstance(protected, tuple):
        return

    if event == "socket.connect":
        raise RuntimeError("tripwire: network connect blocked")

    if event == "open" and args:
        path_str = _as_path_str(args[0])
        if path_str is None or not _path_under_protected(path_str, protected):
            return
        if mode == "detect_only":
            raise RuntimeError("tripwire: open on protected path blocked in detect mode")
        flags = args[2] if len(args) > 2 else 0
        if isinstance(flags, int) and flags & _WRITE_OPEN_FLAGS:
            _check_write(path_str, "write open", protected)
        return

    if event in _WRITE_EVENTS:
        path_indexes, dir_fd_indexes = _WRITE_EVENTS[event]
        # Paths relative to a directory fd (shutil.rmtree internals) cannot be resolved here;
        # the top-level shutil.rmtree event is checked instead.
        if any(i < len(args) and args[i] not in (None, -1) for i in dir_fd_indexes):
            return
        for i in path_indexes:
            path_str = _as_path_str(args[i]) if i < len(args) else None
            if path_str is not None:
                _check_write(path_str, event, protected)
        return

    if event == "sqlite3.connect" and args:
        path_str = _as_path_str(args[0])
        if path_str is not None and any(
            _path_under_protected(p, protected) for p in _sqlite_candidate_paths(path_str)
        ):
            raise RuntimeError("tripwire: sqlite3.connect on protected path blocked")


def install_tripwire(
    protected_roots: tuple[Path, ...],
    mode: TripwireMode = "normal",
) -> None:
    if mode == "off":
        uninstall_tripwire()
        return
    old = _state.get("hook")
    if old is None:
        sys.addaudithook(_audit_hook)
        _state["hook"] = _audit_hook
    _state["enabled"] = True
    _state["mode"] = mode
    _state["protected"] = tuple(p.resolve() for p in protected_roots if p.exists())
    _state["write_allowed"] = frozenset()


def uninstall_tripwire() -> None:
    _state["enabled"] = False
    _state["mode"] = "off"
    _state["protected"] = ()
    _state["write_allowed"] = frozenset()


def get_protected_roots() -> tuple[Path, ...]:
    p = _state.get("protected")
    return p if isinstance(p, tuple) else ()


@contextmanager
def allow_profile_writes(profile_root: Path, targets: Iterable[Path]) -> Iterator[None]:
    """Apply mode: protect ``profile_root`` and permit writes only to ``targets``.

    Allowed per target: the file itself, its ``<name>.partial-*`` temp siblings, and creating
    its missing parent directories. Does nothing when the tripwire is not enabled.
    """
    if not _state["enabled"]:
        yield
        return
    previous_protected = _state["protected"]
    previous_allowed = _state["write_allowed"]
    protected = previous_protected if isinstance(previous_protected, tuple) else ()
    root = profile_root.resolve()
    if root.exists() and root not in protected:
        protected = (*protected, root)
    _state["protected"] = protected
    _state["write_allowed"] = frozenset(_entry_path(t) for t in targets)
    try:
        yield
    finally:
        _state["protected"] = previous_protected
        _state["write_allowed"] = previous_allowed
