"""Workspace remap under the CLI write guard: every failure point must leave the profile as it was."""

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from tests.fixtures.remap_profile import (
    add_composer_rows,
    build_ubuntu_workspace_profile,
    leave_hot_wal,
)

from cursor_portability.cli.main import main
from cursor_portability.errors import AppError, ErrorCode
from cursor_portability.migration import remap_engine
from cursor_portability.migration.remap_engine import apply_remap_plan, verify_remap_run
from cursor_portability.planning.plan import read_plan, wrap_plan, write_plan
from cursor_portability.planning.remap import create_remap_plan
from cursor_portability.planning.workspace_id import workspace_id_for_folder
from cursor_portability.security.tripwire import get_protected_roots, install_tripwire

darwin_only = pytest.mark.skipif(
    sys.platform != "darwin", reason="workspace id formula is macOS-only"
)

_UBUNTU_ID = "b" * 32
_OLD = "/home/u/Projects"
_REMAP = ["workspaces", "remap"]


def _profile(fake_home: Path, tmp_path: Path, hot_wal: bool = True) -> tuple[Path, Path]:
    ud = fake_home / "Library" / "Application Support" / "Cursor"
    mac_proj = tmp_path / "Projects" / "demo"
    build_ubuntu_workspace_profile(ud, _UBUNTU_ID, f"{_OLD}/demo", mac_proj, composer_rows=3)
    if hot_wal:
        leave_hot_wal(_global_db(ud))
    return ud, mac_proj


def _global_db(ud: Path) -> Path:
    return ud / "User" / "globalStorage" / "state.vscdb"


def _global_set(ud: Path) -> dict[str, bytes | None]:
    out: dict[str, bytes | None] = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = Path(f"{_global_db(ud)}{suffix}")
        out[suffix] = path.read_bytes() if path.is_file() else None
    return out


def _mac_dir(ud: Path, mac_proj: Path) -> Path:
    return ud / "User" / "workspaceStorage" / workspace_id_for_folder(mac_proj)


def _fail_on_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    real_journal = remap_engine.append_journal

    def journal(run_dir: Path, record: dict[str, object]) -> None:
        if record.get("event") == "remap_completed":
            raise OSError("injected failure")
        real_journal(run_dir, record)

    monkeypatch.setattr(remap_engine, "append_journal", journal)


@darwin_only
def test_cli_remap_round_trip_under_the_write_guard(
    fake_home, cursor_not_running, tmp_path
) -> None:
    ud, mac_proj = _profile(fake_home, tmp_path)
    before = _global_set(ud)
    assert before["-wal"] is not None
    plan_path = tmp_path / "remap-plan.json"
    runs = tmp_path / "runs"
    plan_args = ["--from", _OLD, "--to", str(mac_proj.parent), "--output", str(plan_path)]
    assert main([*_REMAP, "plan", *plan_args]) == 0
    assert main([*_REMAP, "apply", str(plan_path), "--yes", "--runs-dir", str(runs)]) == 0
    assert ud.resolve() in get_protected_roots()
    assert (_mac_dir(ud, mac_proj) / "workspace.json").is_file()
    assert not Path(f"{_global_db(ud)}-wal").exists()
    (run_dir,) = runs.iterdir()
    run_args = ["--run-id", run_dir.name, "--runs-dir", str(runs)]
    assert main([*_REMAP, "verify", *run_args]) == 0
    assert main([*_REMAP, "rollback", *run_args, "--yes"]) == 0
    assert _global_set(ud) == before
    assert not _mac_dir(ud, mac_proj).exists()


@darwin_only
@pytest.mark.parametrize("fail_at", ["db-update", "global-publish", "workspace-copy", "completion"])
def test_failure_at_each_point_restores_profile(
    fake_home, cursor_not_running, tmp_path, monkeypatch, fail_at
) -> None:
    ud, mac_proj = _profile(fake_home, tmp_path)
    plan_path = tmp_path / "remap-plan.json"
    create_remap_plan(ud, _OLD, str(mac_proj.parent), plan_path, set(), set())
    before = _global_set(ud)
    install_tripwire((ud,))

    def failing_updates(*_args: object) -> None:
        raise OSError("injected failure")

    def interrupted_publish(live: Path, _staged: Path, _run_dir: Path) -> None:
        for suffix in ("-wal", "-shm"):
            Path(f"{live}{suffix}").unlink()
        raise OSError("injected failure")

    def partial_copy(op: dict[str, str], ws_root: Path, _run_dir: Path) -> None:
        (ws_root / op["mac_id"]).mkdir()
        (ws_root / op["mac_id"] / "workspace.json").write_text("{}", encoding="utf-8")
        raise OSError("injected failure")

    if fail_at == "db-update":
        monkeypatch.setattr(remap_engine, "apply_db_updates", failing_updates)
    elif fail_at == "global-publish":
        monkeypatch.setattr(remap_engine, "_publish_global_db", interrupted_publish)
    elif fail_at == "workspace-copy":
        monkeypatch.setattr(remap_engine, "_publish_workspace_copy", partial_copy)
    else:
        _fail_on_completion(monkeypatch)
    runs = tmp_path / "runs"
    with pytest.raises(AppError) as info:
        apply_remap_plan(plan_path, runs, confirm=True)
    assert info.value.code == ErrorCode.APPLY_FAILED_ROLLBACK_SUCCEEDED
    assert info.value.details is not None
    assert info.value.details["cause"] == {"code": "OSError", "message": "injected failure"}
    assert _global_set(ud) == before
    assert not _mac_dir(ud, mac_proj).exists()
    (run_dir,) = runs.iterdir()
    assert not (run_dir / ".lock").exists()
    assert '"event":"remap_failed"' in (run_dir / "journal.jsonl").read_text(encoding="utf-8")


@darwin_only
def test_failed_replace_restores_the_existing_mac_workspace(
    fake_home, cursor_not_running, tmp_path, monkeypatch
) -> None:
    ud, mac_proj = _profile(fake_home, tmp_path)
    mac_dir = _mac_dir(ud, mac_proj)
    mac_dir.mkdir()
    (mac_dir / "workspace.json").write_text('{"folder": "file:///mine"}', encoding="utf-8")
    plan_path = tmp_path / "remap-plan.json"
    create_remap_plan(ud, _OLD, str(mac_proj.parent), plan_path, {"demo"}, set())
    before = _global_set(ud)
    install_tripwire((ud,))
    _fail_on_completion(monkeypatch)
    with pytest.raises(AppError) as info:
        apply_remap_plan(plan_path, tmp_path / "runs", confirm=True)
    assert info.value.code == ErrorCode.APPLY_FAILED_ROLLBACK_SUCCEEDED
    assert _global_set(ud) == before
    assert (mac_dir / "workspace.json").read_text(encoding="utf-8") == '{"folder": "file:///mine"}'


@darwin_only
def test_link_chats_verify_counts_chats_already_on_the_mac_workspace(
    fake_home, cursor_not_running, tmp_path
) -> None:
    ud, mac_proj = _profile(fake_home, tmp_path, hot_wal=False)
    mac_dir = _mac_dir(ud, mac_proj)
    mac_dir.mkdir()
    (mac_dir / "workspace.json").write_text(
        f'{{"folder": "file://{mac_proj.resolve()}"}}', encoding="utf-8"
    )
    add_composer_rows(_global_db(ud), mac_dir.name, 2, "mac")
    plan_path = tmp_path / "remap-plan.json"
    create_remap_plan(ud, _OLD, str(mac_proj.parent), plan_path, set(), {"demo"})
    (db_op,) = read_plan(plan_path).body["operations"]
    assert db_op["updates"] == [
        {"from_id": _UBUNTU_ID, "to_id": mac_dir.name, "expected_rows": 3, "existing_rows": 2}
    ]
    report = apply_remap_plan(plan_path, tmp_path / "runs", confirm=True)
    assert verify_remap_run(Path(report.run_dir), ud / "User") == {"ok": True, "errors": []}


def _handmade_body(ud: Path) -> dict[str, Any]:
    return {
        "remap_plan_format": 1,
        "target_user_data_dir": str(ud),
        "path_mapping": {"old": _OLD, "new": "/Users/u/Projects"},
        "global_db_fingerprint": {},
        "operations": [
            {
                "op_id": "ws-1",
                "kind": "workspace_copy",
                "ubuntu_id": _UBUNTU_ID,
                "merged_ubuntu_ids": [],
                "mac_id": "c" * 32,
                "dest_folder_uri": "file:///Users/u/Projects/demo",
                "replace_existing": False,
            },
            {
                "op_id": "db-1",
                "kind": "db_update",
                "adapter": "cursor_composer_headers@1",
                "updates": [
                    {
                        "from_id": _UBUNTU_ID,
                        "to_id": "c" * 32,
                        "expected_rows": 3,
                        "existing_rows": 0,
                    }
                ],
                "recent_paths_rewrite": False,
            },
        ],
    }


def _set(path: tuple[object, ...], value: object) -> Callable[[dict[str, Any]], None]:
    def mutate(body: dict[str, Any]) -> None:
        node: Any = body
        for key in path[:-1]:
            node = node[key]
        if value is _DELETE:
            del node[path[-1]]
        else:
            node[path[-1]] = value

    return mutate


_DELETE = object()
_WS = ("operations", 0)
_UPD = ("operations", 1, "updates", 0)


@pytest.mark.parametrize(
    "mutate",
    [
        _set((*_WS, "mac_id"), "../../../../escape"),
        _set((*_WS, "ubuntu_id"), "../" + "a" * 29),
        _set((*_WS, "merged_ubuntu_ids"), ["/etc"]),
        _set((*_WS, "dest_folder_uri"), None),
        _set((*_UPD, "to_id"), "C" * 32),
        _set((*_UPD, "expected_rows"), -1),
        _set((*_UPD, "existing_rows"), _DELETE),
        _set((*_UPD, "expected_rows"), True),
        _set(("operations", 1, "kind"), "sql"),
        _set(("operations",), [{"kind": "db_update", "updates": []}] * 2),
        lambda body: body["operations"].insert(1, dict(body["operations"][0])),
    ],
    ids=[
        "mac-id-traversal",
        "ubuntu-id-traversal",
        "merged-id-path",
        "uri-missing",
        "to-id-not-lowercase-hex",
        "negative-count",
        "existing-rows-missing",
        "bool-count",
        "unknown-kind",
        "two-db-updates",
        "repeated-mac-id",
    ],
)
def test_tampered_remap_plans_are_rejected_before_any_write(
    fake_home, cursor_not_running, tmp_path, mutate
) -> None:
    ud = fake_home / "Library" / "Application Support" / "Cursor"
    plan_path = tmp_path / "remap-plan.json"
    body = _handmade_body(ud)
    write_plan(plan_path, wrap_plan(body))
    with pytest.raises(AppError) as untouched:
        apply_remap_plan(plan_path, tmp_path / "runs", confirm=True)
    assert untouched.value.code == ErrorCode.TARGET_CHANGED
    mutate(body)
    write_plan(plan_path, wrap_plan(body))
    with pytest.raises(AppError) as info:
        apply_remap_plan(plan_path, tmp_path / "runs", confirm=True)
    assert info.value.code == ErrorCode.PLAN_INVALID
    assert not (tmp_path / "runs").exists()


def test_remap_show_needs_no_local_profile(fake_home, tmp_path) -> None:
    plan_path = tmp_path / "remap-plan.json"
    write_plan(plan_path, wrap_plan({"remap_plan_format": 1, "operations": []}))
    assert main([*_REMAP, "show", str(plan_path), "--json"]) == 0


def test_remap_verify_reports_unknown_run(fake_home, tmp_path) -> None:
    runs = tmp_path / "runs"
    assert main([*_REMAP, "verify", "--run-id", "0123456789ab", "--runs-dir", str(runs)]) == 1
