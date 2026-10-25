from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from cursor_portability.models.enums import ProcessState
from cursor_portability.security.permissions import clear_protected_roots
from cursor_portability.security.tripwire import uninstall_tripwire


@pytest.fixture(autouse=True)
def _reset_process_guards() -> Iterator[None]:
    yield
    uninstall_tripwire()
    clear_protected_roots()


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    (home / "AppData" / "Roaming").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    (home / "AppData" / "Local").mkdir(parents=True)
    config = home / ".config"
    config.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    return home


@pytest.fixture
def cursor_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    not_running = lambda _ud: ProcessState.NOT_RUNNING  # noqa: E731
    monkeypatch.setattr(
        "cursor_portability.services.backup.probe_cursor_running",
        not_running,
    )
    monkeypatch.setattr(
        "cursor_portability.platform.process_probe.probe_cursor_running",
        not_running,
    )
    monkeypatch.setattr(
        "cursor_portability.migration.engine.probe_cursor_running",
        not_running,
    )


@pytest.fixture
def synthetic_profile(fake_home: Path) -> Path:
    """Synthetic Cursor profile laid out for the OS running the tests."""
    from tests.fixtures.profile import build_synthetic_profile

    return build_synthetic_profile(fake_home, sys.platform)
