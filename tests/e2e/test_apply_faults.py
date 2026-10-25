"""Fault injection for migrate apply: every failure point must leave the destination as it was."""

import sys
from pathlib import Path

import pytest
from tests.fixtures.profile import build_synthetic_profile, user_data_dir_for

from cursor_portability.cli.main import main
from cursor_portability.errors import AppError, ErrorCode
from cursor_portability.migration import engine
from cursor_portability.migration.engine import ApplyOutcome, apply_plan
from cursor_portability.planning.config_merge import ConflictResolution
from cursor_portability.planning.plan import read_plan, wrap_plan, write_plan
from cursor_portability.platform.env import current_environment
from cursor_portability.security.tripwire import get_protected_roots, install_tripwire
from cursor_portability.services.plan import create_plan

_TARGETS = ("User/settings.json", "User/keybindings.json", "User/snippets/python.json")


def _use_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))


def _three_op_plan(source_ud: Path, tmp_path: Path) -> tuple[Path, Path, Path]:
    src_user = source_ud / "User"
    (src_user / "keybindings.json").write_text(
        '[{"key": "ctrl+k", "command": "demo.run"}]', encoding="utf-8"
    )
    (src_user / "snippets" / "python.json").write_text(
        '{"p": {"prefix": "p", "body": "print()"}}', encoding="utf-8"
    )
    bundle = tmp_path / "bundle"
    assert main(["backup", "create", "--output", str(bundle)]) == 0
    dest_home = tmp_path / "dest_home"
    build_synthetic_profile(dest_home, sys.platform)
    ud = user_data_dir_for(dest_home, sys.platform)
    (ud / "User" / "settings.json").write_text(
        '{\n  // mine\n  "editor.fontSize": 12\n}', encoding="utf-8"
    )
    plan_path = tmp_path / "plan.json"
    create_plan(
        current_environment(),
        bundle,
        plan_path,
        ud,
        [],
        None,
        False,
        None,
        set(),
        ConflictResolution.KEEP_DESTINATION,
    )
    ops = read_plan(plan_path).body["operations"]
    assert [op["target_rel"] for op in ops] == list(_TARGETS)
    return bundle, ud, plan_path


def _state(ud: Path) -> dict[str, bytes | None]:
    out: dict[str, bytes | None] = {}
    for rel in _TARGETS:
        path = ud / rel
        out[rel] = path.read_bytes() if path.is_file() else None
    return out


@pytest.mark.parametrize("fail_at", ["op-1", "op-2", "op-3", "after-writes"])
def test_failure_at_each_point_restores_destination(
    synthetic_profile, cursor_not_running, tmp_path, monkeypatch, fail_at
) -> None:
    bundle, ud, plan_path = _three_op_plan(synthetic_profile, tmp_path)
    before = _state(ud)
    real_write = engine.write_text_atomic
    writes: list[Path] = []

    def flaky_write(target: Path, text: str) -> None:
        if fail_at == f"op-{len(writes) + 1}":
            raise OSError("injected write failure")
        real_write(target, text)
        writes.append(target)

    def failing_report(_obj: object) -> str:
        raise OSError("injected write failure")

    monkeypatch.setattr(engine, "write_text_atomic", flaky_write)
    if fail_at == "after-writes":
        monkeypatch.setattr(engine, "dumps_canonical", failing_report)
    runs = tmp_path / "runs"
    with pytest.raises(AppError) as info:
        apply_plan(plan_path, bundle, runs, confirm=True)
    assert info.value.code == ErrorCode.APPLY_FAILED_ROLLBACK_SUCCEEDED
    assert info.value.details is not None
    assert info.value.details["cause"] == {"code": "OSError", "message": "injected write failure"}
    assert _state(ud) == before
    assert not list((ud / "User").rglob("*.partial-*"))
    (run_dir,) = runs.iterdir()
    assert not (run_dir / ".lock").exists()
    assert '"event":"apply_failed"' in (run_dir / "journal.jsonl").read_text(encoding="utf-8")


def test_cli_apply_works_under_the_write_guard(
    synthetic_profile, cursor_not_running, tmp_path, monkeypatch
) -> None:
    bundle, ud, plan_path = _three_op_plan(synthetic_profile, tmp_path)
    _use_home(monkeypatch, tmp_path / "dest_home")
    runs = tmp_path / "runs"
    args = ["migrate", "apply", str(plan_path), "--backup", str(bundle), "--yes"]
    assert main([*args, "--runs-dir", str(runs)]) == 0
    assert ud.resolve() in get_protected_roots()
    assert all((ud / rel).is_file() for rel in _TARGETS)


def test_stray_profile_write_during_apply_is_blocked_and_rolled_back(
    synthetic_profile, cursor_not_running, tmp_path, monkeypatch
) -> None:
    bundle, ud, plan_path = _three_op_plan(synthetic_profile, tmp_path)
    install_tripwire(())
    before = _state(ud)
    real_write = engine.write_text_atomic

    def write_with_stray_file(target: Path, text: str) -> None:
        real_write(target, text)
        (ud / "User" / "stray.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(engine, "write_text_atomic", write_with_stray_file)
    with pytest.raises(AppError) as info:
        apply_plan(plan_path, bundle, tmp_path / "runs", confirm=True)
    assert info.value.code == ErrorCode.APPLY_FAILED_ROLLBACK_SUCCEEDED
    assert info.value.details is not None
    assert info.value.details["cause"]["code"] == "RuntimeError"
    assert not (ud / "User" / "stray.json").exists()
    assert _state(ud) == before


def test_second_apply_reports_already_applied(
    synthetic_profile, cursor_not_running, tmp_path
) -> None:
    bundle, ud, plan_path = _three_op_plan(synthetic_profile, tmp_path)
    runs = tmp_path / "runs"
    assert apply_plan(plan_path, bundle, runs, confirm=True).outcome == ApplyOutcome.APPLIED
    after = _state(ud)
    again = apply_plan(plan_path, bundle, runs, confirm=True)
    assert again.outcome == ApplyOutcome.ALREADY_APPLIED
    assert again.run_dir == ""
    assert len(list(runs.iterdir())) == 1
    assert _state(ud) == after


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("target_rel", "User/snippets/../settings.json", ErrorCode.UNSAFE_PATH),
        ("target_rel", "User/snippets/sub/x.json", ErrorCode.UNSAFE_PATH),
        ("target_rel", "User/globalStorage/state.vscdb", ErrorCode.UNSAFE_PATH),
        ("source_member", "user-data/User/settings.json", ErrorCode.PLAN_INVALID),
    ],
)
def test_tampered_plan_paths_are_rejected(
    synthetic_profile, cursor_not_running, tmp_path, field, value, code
) -> None:
    bundle, ud, plan_path = _three_op_plan(synthetic_profile, tmp_path)
    before = _state(ud)
    body = read_plan(plan_path).body
    body["operations"][2][field] = value
    write_plan(plan_path, wrap_plan(body))
    with pytest.raises(AppError) as info:
        apply_plan(plan_path, bundle, tmp_path / "runs", confirm=True)
    assert info.value.code == code
    assert _state(ud) == before


def test_symlinked_destination_file_is_refused(
    synthetic_profile, cursor_not_running, tmp_path
) -> None:
    bundle, ud, plan_path = _three_op_plan(synthetic_profile, tmp_path)
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text("[]", encoding="utf-8")
    (ud / "User" / "keybindings.json").symlink_to(elsewhere)
    with pytest.raises(AppError) as info:
        apply_plan(plan_path, bundle, tmp_path / "runs", confirm=True)
    assert info.value.code == ErrorCode.UNSAFE_PATH
    assert elsewhere.read_text(encoding="utf-8") == "[]"


def test_cli_rejects_run_id_paths(fake_home, tmp_path) -> None:
    runs = tmp_path / "runs"
    assert main(["migrate", "verify", "--run-id", "../escape", "--runs-dir", str(runs)]) == 1
