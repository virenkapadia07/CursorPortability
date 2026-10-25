from pathlib import Path

import pytest

from cursor_portability.models.enums import ProcessState
from cursor_portability.platform import process_probe
from cursor_portability.platform.process_probe import _is_cursor_process_macos

# Shaped like `ps -ax -o comm=` output on macOS.
CURSOR_PROCESSES = [
    "/Applications/Cursor.app/Contents/MacOS/Cursor",
    "/Applications/Cursor.app/Contents/Frameworks/Cursor Helper.app/Contents/MacOS/Cursor Helper",
    "/Applications/Cursor.app/Contents/Frameworks/Electron Framework.framework/Helpers/chrome_crashpad_handler",
    "Cursor Helper (Plugin): extension-host Agents Window [1-1]",
    "Cursor Helper: shared-process",
    "Cursor Helper: fileWatcher [1:empty-window]",
    "/Users/me/Applications/Cursor.app/Contents/MacOS/Cursor",
    "/Users/me/.local/share/cursor-agent/versions/1.0/node",
]

NOT_CURSOR_PROCESSES = [
    "/System/Library/PrivateFrameworks/TextInputUIMacHelper.framework/Versions/A/XPCServices/CursorUIViewService.xpc/Contents/MacOS/CursorUIViewService",
    "/Library/Frameworks/Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python",
    "/Users/me/src/project/.venv/bin/python",
    "/Applications/Cursorcerer.app/Contents/MacOS/Cursorcerer",
    "-zsh",
]


@pytest.mark.parametrize("comm", CURSOR_PROCESSES)
def test_matches_cursor_processes(comm: str) -> None:
    assert _is_cursor_process_macos(comm)


@pytest.mark.parametrize("comm", NOT_CURSOR_PROCESSES)
def test_ignores_unrelated_processes(comm: str) -> None:
    assert not _is_cursor_process_macos(comm)


@pytest.fixture
def quit_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """User-data dir as Cursor leaves it after a clean quit."""
    gs = tmp_path / "Cursor" / "User" / "globalStorage"
    gs.mkdir(parents=True)
    (gs / "state.vscdb").write_bytes(b"db")
    (gs / "state.vscdb-wal").write_bytes(b"")
    (gs / "state.vscdb-shm").write_bytes(b"\0" * 32768)
    monkeypatch.setattr(
        process_probe, "_process_table_has_cursor", lambda: ProcessState.NOT_RUNNING
    )
    return tmp_path / "Cursor"


def test_leftover_empty_wal_and_shm_mean_not_running(quit_profile: Path) -> None:
    assert process_probe.probe_cursor_running(quit_profile) is ProcessState.NOT_RUNNING


def test_nonempty_wal_means_running(quit_profile: Path) -> None:
    (quit_profile / "User" / "globalStorage" / "state.vscdb-wal").write_bytes(b"\1" * 4096)
    assert process_probe.probe_cursor_running(quit_profile) is ProcessState.RUNNING


def test_main_socket_means_running(quit_profile: Path) -> None:
    (quit_profile / "3.27-main.sock").write_bytes(b"")
    assert process_probe.probe_cursor_running(quit_profile) is ProcessState.RUNNING
