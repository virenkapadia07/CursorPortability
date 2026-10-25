import os
import sys
from pathlib import Path

import pytest

from cursor_portability.errors import AppError, ErrorCode
from cursor_portability.planning.workspace_id import workspace_id_for_folder


def test_workspace_id_macos_formula(tmp_path: Path) -> None:
    if sys.platform != "darwin":
        pytest.skip("macOS-only formula")
    folder = tmp_path / "proj"
    folder.mkdir()
    st = folder.stat()

    def fake_stat(_path: object) -> os.stat_result:
        return st

    a = workspace_id_for_folder(folder, stat_fn=fake_stat)
    b = workspace_id_for_folder(folder, stat_fn=fake_stat)
    assert a == b
    assert len(a) == 32


def test_workspace_id_unavailable_off_macos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(AppError) as info:
        workspace_id_for_folder(tmp_path)
    assert info.value.code == ErrorCode.CAPABILITY_UNAVAILABLE
