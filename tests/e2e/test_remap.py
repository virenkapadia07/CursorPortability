import os
import sys
from pathlib import Path

import pytest
from tests.fixtures.remap_profile import add_ubuntu_workspace, build_ubuntu_workspace_profile

from cursor_portability.cli.main import main
from cursor_portability.migration.remap_engine import apply_remap_plan, verify_remap_run
from cursor_portability.planning.plan import read_plan
from cursor_portability.planning.remap import create_remap_plan
from cursor_portability.planning.workspace_id import workspace_id_for_folder


@pytest.mark.skipif(sys.platform != "darwin", reason="workspace id formula is macOS-only")
def test_remap_plan_apply_verify_rollback(fake_home, cursor_not_running, tmp_path) -> None:
    ud = fake_home / "Library" / "Application Support" / "Cursor"
    ubuntu_id = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    mac_root = tmp_path / "Projects"
    mac_proj = mac_root / "demo"
    build_ubuntu_workspace_profile(
        ud,
        ubuntu_id,
        "/home/u/Projects/demo",
        mac_proj,
        composer_rows=3,
    )
    plan_path = tmp_path / "remap-plan.json"
    create_remap_plan(
        ud,
        "/home/u/Projects",
        str(mac_root),
        plan_path,
        set(),
        set(),
    )
    runs = fake_home / ".cursor-portability" / "runs"
    report = apply_remap_plan(plan_path, runs, confirm=True)
    mac_id = workspace_id_for_folder(mac_proj)
    assert (ud / "User" / "workspaceStorage" / mac_id / "workspace.json").is_file()
    verify = verify_remap_run(Path(report.run_dir), ud / "User")
    assert verify["ok"]
    assert (
        main(
            [
                "workspaces",
                "remap",
                "rollback",
                "--run-id",
                report.run_id,
                "--yes",
                "--runs-dir",
                str(runs),
            ]
        )
        == 0
    )


def _profile_with_reclone(fake_home: Path, tmp_path: Path) -> tuple[Path, Path, Path]:
    """Two Ubuntu workspaces for one project; the second (re-cloned) one is newer."""
    ud = fake_home / "Library" / "Application Support" / "Cursor"
    mac_proj = tmp_path / "Projects" / "demo"
    old_id, new_id = "a" * 32, "e" * 32
    build_ubuntu_workspace_profile(ud, old_id, "/home/u/Projects/demo", mac_proj, composer_rows=2)
    newer = add_ubuntu_workspace(ud, new_id, "/home/u/Projects/demo", composer_rows=3)
    (newer / "marker.txt").write_text("newer", encoding="utf-8")
    older_db = ud / "User" / "workspaceStorage" / old_id / "state.vscdb"
    stamp = (newer / "state.vscdb").stat().st_mtime_ns - 10**12
    os.utime(older_db, ns=(stamp, stamp))
    os.utime(older_db.parent / "workspace.json", ns=(stamp, stamp))
    return ud, mac_proj, newer


@pytest.mark.skipif(sys.platform != "darwin", reason="workspace id formula is macOS-only")
def test_duplicate_ubuntu_workspaces_merge_into_one_mac_workspace(
    fake_home, cursor_not_running, tmp_path
) -> None:
    ud, mac_proj, newer = _profile_with_reclone(fake_home, tmp_path)
    plan_path = tmp_path / "remap-plan.json"
    create_remap_plan(ud, "/home/u/Projects", str(mac_proj.parent), plan_path, set(), set())
    body = read_plan(plan_path).body
    ws_op, db_op = body["operations"]
    mac_id = workspace_id_for_folder(mac_proj)
    assert (ws_op["ubuntu_id"], ws_op["merged_ubuntu_ids"]) == (newer.name, ["a" * 32])
    assert sorted((u["from_id"], u["expected_rows"]) for u in db_op["updates"]) == [
        ("a" * 32, 2),
        ("e" * 32, 3),
    ]
    assert any(w.startswith("merge demo: 2 Ubuntu workspaces") for w in body["warnings"])
    report = apply_remap_plan(plan_path, tmp_path / "runs", confirm=True)
    mac_dir = ud / "User" / "workspaceStorage" / mac_id
    assert (mac_dir / "marker.txt").read_text(encoding="utf-8") == "newer"
    assert verify_remap_run(Path(report.run_dir), ud / "User") == {"ok": True, "errors": []}


@pytest.mark.skipif(sys.platform != "darwin", reason="workspace id formula is macOS-only")
def test_skip_duplicates_leaves_the_project_out_of_the_plan(
    fake_home, cursor_not_running, tmp_path
) -> None:
    ud, mac_proj, _ = _profile_with_reclone(fake_home, tmp_path)
    plan_path = tmp_path / "remap-plan.json"
    create_remap_plan(
        ud,
        "/home/u/Projects",
        str(mac_proj.parent),
        plan_path,
        set(),
        set(),
        skip_duplicates=True,
    )
    body = read_plan(plan_path).body
    (db_op,) = body["operations"]
    assert db_op["updates"] == []
    assert any(w.startswith("skip demo: 2 Ubuntu workspaces") for w in body["warnings"])
