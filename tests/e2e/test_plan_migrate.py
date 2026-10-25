import hashlib
import json
from pathlib import Path

import pytest
from tests.fixtures.profile import build_synthetic_profile, user_data_dir_for

from cursor_portability.cli.main import main
from cursor_portability.errors import AppError, ErrorCode
from cursor_portability.migration import engine
from cursor_portability.migration.engine import ApplyOutcome, apply_plan, rollback_run
from cursor_portability.planning.config_merge import ConflictResolution
from cursor_portability.planning.jsonc import parse_jsonc
from cursor_portability.planning.plan import read_plan
from cursor_portability.platform.env import current_environment
from cursor_portability.services.plan import create_plan


def _bundle_and_plan(tmp_path: Path, use_source_keys: set[str]) -> tuple[Path, Path, Path]:
    bundle = tmp_path / "bundle"
    assert main(["backup", "create", "--output", str(bundle)]) == 0
    dest_home = tmp_path / "dest_home"
    build_synthetic_profile(dest_home, "linux")
    ud = user_data_dir_for(dest_home, "linux")
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
        use_source_keys,
        ConflictResolution.KEEP_DESTINATION,
        source_os="darwin",
        dest_os="linux",
    )
    return bundle, ud, plan_path


def _tree_hash(root: Path) -> str:
    lines = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            lines.append(f"{p.relative_to(root)} {st.st_size} {st.st_mtime_ns}")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def test_plan_create_is_read_only(synthetic_profile, cursor_not_running, tmp_path) -> None:
    bundle = tmp_path / "bundle"
    assert main(["backup", "create", "--output", str(bundle)]) == 0
    dest_home = tmp_path / "dest_home"
    build_synthetic_profile(dest_home, "linux")
    ud = user_data_dir_for(dest_home, "linux")
    (ud / "User" / "settings.json").write_text('{"editor.fontSize": 12}', encoding="utf-8")
    before = _tree_hash(ud)
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
        source_os="darwin",
        dest_os="linux",
    )
    after = _tree_hash(ud)
    assert before == after
    plan = read_plan(plan_path)
    assert plan.plan_id
    assert len(plan.body["operations"]) >= 1


def test_plan_deterministic(synthetic_profile, cursor_not_running, tmp_path) -> None:
    bundle = tmp_path / "bundle"
    assert main(["backup", "create", "--output", str(bundle)]) == 0
    dest_home = tmp_path / "dest_home"
    build_synthetic_profile(dest_home, "linux")
    ud = user_data_dir_for(dest_home, "linux")
    kwargs = {
        "env": current_environment(),
        "bundle_path": bundle,
        "output": tmp_path / "a.json",
        "target_profile": ud,
        "path_maps": [],
        "path_map_file": None,
        "interactive": False,
        "ask_mapping": None,
        "use_source_keys": set(),
        "on_conflict": ConflictResolution.KEEP_DESTINATION,
        "source_os": "darwin",
        "dest_os": "linux",
    }
    create_plan(**kwargs)
    kwargs["output"] = tmp_path / "b.json"
    create_plan(**kwargs)
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()


def test_apply_and_rollback(synthetic_profile, cursor_not_running, tmp_path) -> None:
    bundle = tmp_path / "bundle"
    assert main(["backup", "create", "--output", str(bundle)]) == 0
    dest_home = tmp_path / "dest_home"
    build_synthetic_profile(dest_home, "linux")
    ud = user_data_dir_for(dest_home, "linux")
    (ud / "User" / "settings.json").write_text('{"editor.fontSize": 12}', encoding="utf-8")
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
        source_os="darwin",
        dest_os="linux",
    )
    runs = tmp_path / "runs"
    assert (
        main(
            [
                "migrate",
                "apply",
                str(plan_path),
                "--backup",
                str(bundle),
                "--yes",
                "--runs-dir",
                str(runs),
            ]
        )
        == 0
    )
    settings = json.loads((ud / "User" / "settings.json").read_text())
    assert "hello.key" in settings or settings.get("editor.fontSize") == 14
    run_dirs = [p for p in runs.iterdir() if p.is_dir()]
    assert run_dirs
    run_id = run_dirs[0].name
    assert main(["migrate", "rollback", "--run-id", run_id, "--yes", "--runs-dir", str(runs)]) == 0
    restored = (ud / "User" / "settings.json").read_text(encoding="utf-8")
    assert "editor.fontSize" in restored


def test_apply_honors_use_source(synthetic_profile, cursor_not_running, tmp_path) -> None:
    bundle, ud, plan_path = _bundle_and_plan(tmp_path, {"editor.fontSize"})
    report = apply_plan(plan_path, bundle, tmp_path / "runs", confirm=True)
    assert report.outcome == ApplyOutcome.APPLIED
    text = (ud / "User" / "settings.json").read_text(encoding="utf-8")
    assert parse_jsonc(text).value == {"editor.fontSize": 14, "hello.key": "world"}
    assert "// mine" in text


def _corrupting_writer(target: Path, text: str) -> None:
    target.write_text(text + "\n", encoding="utf-8")


def test_apply_failure_keeps_cause_and_verifies_restore(
    synthetic_profile, cursor_not_running, tmp_path, monkeypatch
) -> None:
    bundle, ud, plan_path = _bundle_and_plan(tmp_path, set())
    settings = ud / "User" / "settings.json"
    before = settings.read_bytes()
    monkeypatch.setattr(engine, "write_text_atomic", _corrupting_writer)
    runs = tmp_path / "runs"
    with pytest.raises(AppError) as info:
        apply_plan(plan_path, bundle, runs, confirm=True)
    assert info.value.code == ErrorCode.APPLY_FAILED_ROLLBACK_SUCCEEDED
    assert info.value.details is not None
    assert info.value.details["cause"] == {
        "code": "TARGET_CHANGED",
        "message": "post-apply hash mismatch",
    }
    assert "TARGET_CHANGED" in info.value.message
    assert settings.read_bytes() == before
    (run_dir,) = runs.iterdir()
    assert not (run_dir / ".lock").exists()


def test_apply_failure_reports_incomplete_rollback(
    synthetic_profile, cursor_not_running, tmp_path, monkeypatch
) -> None:
    bundle, ud, plan_path = _bundle_and_plan(tmp_path, set())
    runs = tmp_path / "runs"

    def corrupt_and_lose_snapshot(target: Path, text: str) -> None:
        _corrupting_writer(target, text)
        for snap in runs.glob("*/snapshot/*"):
            snap.unlink()

    monkeypatch.setattr(engine, "write_text_atomic", corrupt_and_lose_snapshot)
    with pytest.raises(AppError) as info:
        apply_plan(plan_path, bundle, runs, confirm=True)
    assert info.value.code == ErrorCode.APPLY_FAILED_ROLLBACK_INCOMPLETE
    assert info.value.details is not None
    assert info.value.details["not_restored"] == [str(ud / "User" / "settings.json")]
    (run_dir,) = runs.iterdir()
    assert (run_dir / ".lock").exists()


def test_rollback_refuses_when_snapshot_is_missing(
    synthetic_profile, cursor_not_running, tmp_path
) -> None:
    bundle, ud, plan_path = _bundle_and_plan(tmp_path, set())
    runs = tmp_path / "runs"
    report = apply_plan(plan_path, bundle, runs, confirm=True)
    run_dir = Path(report.run_dir)
    for snap in (run_dir / "snapshot").iterdir():
        snap.unlink()
    settings = ud / "User" / "settings.json"
    with pytest.raises(AppError) as info:
        rollback_run(run_dir, ud / "User", confirm=True)
    assert info.value.code == ErrorCode.RUN_INCOMPLETE
    assert settings.is_file()
