"""Runtime audit hook to block unsafe operations on protected paths."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

TripwireMode = Literal["off", "normal", "detect_only"]

_state: dict[str, object] = {
    "enabled": False,
    "mode": "off",
    "protected": (),
    "hook": None,
}


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
        path_arg = args[0]
        if isinstance(path_arg, (str, bytes)):
            path_str = path_arg.decode() if isinstance(path_arg, bytes) else path_arg
            if _path_under_protected(path_str, protected):
                if mode == "detect_only":
                    raise RuntimeError("tripwire: open on protected path blocked in detect mode")
                flags = args[1] if len(args) > 1 else 0
                if isinstance(flags, int) and (flags & 0x3) != 0:  # O_RDONLY=0
                    raise RuntimeError("tripwire: write open on protected path blocked")

    if event == "sqlite3.connect" and args:
        path_arg = args[0]
        if isinstance(path_arg, str) and _path_under_protected(path_arg, protected):
            raise RuntimeError("tripwire: sqlite3.connect on protected path blocked")

    if event == "subprocess.Popen" and args:
        # Allow only if executable is in allowlist (checked via first arg in some Python versions)
        pass  # subprocess use is discouraged; tests patch os.environ to disable if needed


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


def uninstall_tripwire() -> None:
    _state["enabled"] = False
    _state["mode"] = "off"
    _state["protected"] = ()


def get_protected_roots() -> tuple[Path, ...]:
    p = _state.get("protected")
    return p if isinstance(p, tuple) else ()
