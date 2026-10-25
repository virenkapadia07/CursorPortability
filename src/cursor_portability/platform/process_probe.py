"""Detect whether Cursor appears to be running."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from cursor_portability.models.enums import ProcessState


def probe_cursor_running(user_data_dir: Path | None) -> ProcessState:
    signals: list[bool] = []

    if user_data_dir and user_data_dir.is_dir():
        signals.append(_sock_present(user_data_dir))
        signals.append(_lock_pid_alive(user_data_dir))
        signals.append(_wal_has_pending_writes(user_data_dir))

    proc = _process_table_has_cursor()
    if proc is ProcessState.RUNNING:
        return ProcessState.RUNNING
    if proc is ProcessState.UNKNOWN:
        return ProcessState.UNKNOWN

    if any(signals):
        return ProcessState.RUNNING
    if signals and not any(signals):
        return ProcessState.NOT_RUNNING
    return ProcessState.UNKNOWN


def _sock_present(user_data_dir: Path) -> bool:
    return any(user_data_dir.glob("*-main.sock"))


def _lock_pid_alive(user_data_dir: Path) -> bool:
    lock = user_data_dir / "code.lock"
    if not lock.is_file():
        return False
    try:
        raw = lock.read_text(encoding="utf-8", errors="replace").strip()
        pid = int(raw)
    except (OSError, ValueError):
        return True  # lock exists, treat as running hint
    return _pid_alive(pid)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return False  # use tasklist path below
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _wal_has_pending_writes(user_data_dir: Path) -> bool:
    # Cursor leaves an empty -wal and a -shm behind after a clean quit; only a
    # non-empty WAL indicates writes that have not been checkpointed.
    wal = user_data_dir / "User" / "globalStorage" / "state.vscdb-wal"
    try:
        return wal.stat().st_size > 0
    except FileNotFoundError:
        return False


def _process_table_has_cursor() -> ProcessState:
    try:
        if sys.platform == "darwin":
            out = subprocess.run(
                ["/bin/ps", "-ax", "-o", "comm="],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if out.returncode != 0:
                return ProcessState.UNKNOWN
            if any(_is_cursor_process_macos(line.strip()) for line in out.stdout.splitlines()):
                return ProcessState.RUNNING
            return ProcessState.NOT_RUNNING
        if sys.platform == "win32":
            system_root = os.environ.get("SystemRoot", r"C:\Windows")
            tasklist = Path(system_root) / "System32" / "tasklist.exe"
            if not tasklist.is_file():
                return ProcessState.UNKNOWN
            out = subprocess.run(
                [str(tasklist), "/FI", "IMAGENAME eq Cursor.exe"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if "Cursor.exe" in out.stdout:
                return ProcessState.RUNNING
            return ProcessState.NOT_RUNNING
        # Linux: /proc
        proc = Path("/proc")
        if not proc.is_dir():
            return ProcessState.UNKNOWN
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            comm = entry / "comm"
            try:
                name = comm.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if "cursor" in name.lower():
                return ProcessState.RUNNING
        return ProcessState.NOT_RUNNING
    except (OSError, subprocess.TimeoutExpired):
        return ProcessState.UNKNOWN


def _is_cursor_process_macos(comm: str) -> bool:
    """Match Cursor's app bundle and helpers, not system services like CursorUIViewService."""
    if "/Cursor.app/" in comm or "cursor-agent" in comm.lower():
        return True
    # Helpers retitle themselves, e.g. "Cursor Helper (Plugin): extension-host ...".
    name = comm.rsplit("/", 1)[-1] if comm.startswith("/") else comm
    return name == "Cursor" or name.startswith("Cursor Helper")


def effective_running(state: ProcessState) -> bool:
    """UNKNOWN is treated as running (fail closed)."""
    return state in (ProcessState.RUNNING, ProcessState.UNKNOWN)
