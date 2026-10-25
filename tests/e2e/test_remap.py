import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest
from tests.fixtures.remap_profile import (
    AGENT_TAB_KEY,
    OTHER_FOLDER,
    add_agent_state,
    add_linked_chats,
    add_ubuntu_workspace,
    build_ubuntu_workspace_profile,
    linked_chat_header,
    linked_chat_record,
    workspace_ref,
)

from cursor_portability.cli.main import main
from cursor_portability.errors import AppError, ErrorCode
from cursor_portability.migration.remap_engine import (
    apply_remap_plan,
    rollback_remap_run,
    verify_remap_run,
)
from cursor_portability.planning.plan import read_plan
from cursor_portability.planning.remap import create_remap_plan, show_remap_plan
from cursor_portability.planning.workspace_id import workspace_id_for_folder

_DEMO = "/home/u/Projects/demo"
_CHATS = ["0b4f1d2e-0000-4000-8000-0000000000c1", "0b4f1d2e-0000-4000-8000-0000000000c2"]


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


def _profile_with_linked_chats(fake_home: Path, tmp_path: Path) -> tuple[Path, Path, str]:
    ud = fake_home / "Library" / "Application Support" / "Cursor"
    mac_proj = tmp_path / "Projects" / "demo"
    ubuntu_id = "b" * 32
    build_ubuntu_workspace_profile(ud, ubuntu_id, _DEMO, mac_proj, composer_rows=0)
    global_db = ud / "User" / "globalStorage" / "state.vscdb"
    add_linked_chats(global_db, ubuntu_id, _DEMO, _CHATS)
    add_agent_state(global_db, ubuntu_id, _DEMO)
    return ud, mac_proj, ubuntu_id


def _json_rows(global_db: Path, sql: str) -> dict[str, Any]:
    conn = sqlite3.connect(global_db)
    try:
        return {key: json.loads(value) for key, value in conn.execute(sql)}
    finally:
        conn.close()


def _global_bytes(ud: Path) -> dict[str, bytes | None]:
    db = ud / "User" / "globalStorage" / "state.vscdb"
    return {
        s: Path(f"{db}{s}").read_bytes() if Path(f"{db}{s}").is_file() else None
        for s in ("", "-wal", "-shm", "-journal")
    }


@pytest.mark.skipif(sys.platform != "darwin", reason="workspace id formula is macOS-only")
def test_remap_points_moved_chats_and_agent_state_at_the_mac_workspace(
    fake_home, cursor_not_running, tmp_path
) -> None:
    ud, mac_proj, _ = _profile_with_linked_chats(fake_home, tmp_path)
    global_db = ud / "User" / "globalStorage" / "state.vscdb"
    mac_id, mac_path = workspace_id_for_folder(mac_proj), str(mac_proj)
    before = _global_bytes(ud)
    plan_path = tmp_path / "remap-plan.json"
    report = create_remap_plan(
        ud, "/home/u/Projects", str(mac_proj.parent), plan_path, set(), set()
    )
    assert report.link_rewrites == {
        "chat_headers": 2,
        "chat_records": 2,
        "agent_state": {
            "cursor<path>": 1,
            "glass.localAgentProjects.v1": 1,
            "repositoryTracker.paths": 1,
            "workspaceMetadata.entries": 1,
        },
    }

    run = apply_remap_plan(plan_path, tmp_path / "runs", confirm=True)
    expected_header = linked_chat_header(mac_id, mac_path)
    expected_header["trackedGitRepos"][1] = {"repoPath": OTHER_FOLDER}
    expected_header["subtitle"] = f"edited {_DEMO}/app.py — café"
    headers = _json_rows(global_db, "SELECT composerId, value FROM composerHeaders")
    assert headers == dict.fromkeys(_CHATS, expected_header)
    expected_record = linked_chat_record(mac_id, mac_path)
    expected_record["fullConversationHeadersOnly"][0]["grouping"]["toolDisplayPath"] = _DEMO
    expected_record["text"] = f"look at {_DEMO}"
    records = _json_rows(global_db, "SELECT substr(key, 14), value FROM cursorDiskKV")
    assert records == dict.fromkeys(_CHATS, expected_record)
    state = _json_rows(global_db, "SELECT key, value FROM ItemTable")
    assert state["glass.localAgentProjects.v1"] == [{"workspace": workspace_ref(mac_id, mac_path)}]
    (entry,) = state["workspaceMetadata.entries"]["entries"]
    assert entry == {
        "folderUri": f"file://{mac_path}",
        "displayPath": mac_path,
        "paths": [{"uri": workspace_ref(mac_id, mac_path)["uri"]}],
        "trackedGitRepos": [{"repoPath": mac_path}],
        "label": f"notes about {_DEMO}",
    }
    assert state["repositoryTracker.paths"] == {"repo-1": {"localPath": f"file://{mac_path}"}}
    assert state[AGENT_TAB_KEY] == {"workspaceTabs": [{"props": {"repoPath": mac_path}}]}
    assert state["terminal.history.entries.dirs"] == {"entries": [{"key": _DEMO}]}
    conn = sqlite3.connect(global_db)
    assert conn.execute("SELECT DISTINCT typeof(value) FROM cursorDiskKV").fetchall() == [("blob",)]
    conn.close()

    assert verify_remap_run(Path(run.run_dir), ud / "User") == {"ok": True, "errors": []}
    rollback_remap_run(Path(run.run_dir), ud / "User", confirm=True)
    assert _global_bytes(ud) == before


@pytest.mark.skipif(sys.platform != "darwin", reason="workspace id formula is macOS-only")
def test_verify_flags_a_moved_chat_that_still_names_the_ubuntu_workspace(
    fake_home, cursor_not_running, tmp_path
) -> None:
    ud, mac_proj, ubuntu_id = _profile_with_linked_chats(fake_home, tmp_path)
    plan_path = tmp_path / "remap-plan.json"
    create_remap_plan(ud, "/home/u/Projects", str(mac_proj.parent), plan_path, set(), set())
    run = apply_remap_plan(plan_path, tmp_path / "runs", confirm=True)
    mac_id = workspace_id_for_folder(mac_proj)
    conn = sqlite3.connect(ud / "User" / "globalStorage" / "state.vscdb")
    conn.execute(
        "UPDATE composerHeaders SET value = ? WHERE composerId = ?",
        (json.dumps(linked_chat_header(ubuntu_id, _DEMO)), _CHATS[0]),
    )
    conn.commit()
    conn.close()
    assert verify_remap_run(Path(run.run_dir), ud / "User") == {
        "ok": False,
        "errors": [f"mac id {mac_id}: 1 chat rows name another workspace"],
    }


@pytest.mark.skipif(sys.platform != "darwin", reason="workspace id formula is macOS-only")
def test_plan_refuses_a_chat_whose_stored_workspace_differs_from_its_column(
    fake_home, tmp_path
) -> None:
    ud, mac_proj, ubuntu_id = _profile_with_linked_chats(fake_home, tmp_path)
    conn = sqlite3.connect(ud / "User" / "globalStorage" / "state.vscdb")
    conn.execute(
        "UPDATE composerHeaders SET value = ? WHERE composerId = ?",
        (json.dumps(linked_chat_header("c" * 32, _DEMO)), _CHATS[0]),
    )
    conn.commit()
    conn.close()
    with pytest.raises(AppError) as info:
        create_remap_plan(
            ud, "/home/u/Projects", str(mac_proj.parent), tmp_path / "p.json", set(), set()
        )
    assert info.value.code == ErrorCode.PLAN_INVALID
    assert info.value.message == (
        "cannot re-point chats: workspaceIdentifier.id of a chat of workspace "
        f"{ubuntu_id} is another workspace"
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="workspace id formula is macOS-only")
def test_shareable_show_has_no_paths_or_raw_ids(fake_home, tmp_path) -> None:
    ud, mac_proj, ubuntu_id = _profile_with_linked_chats(fake_home, tmp_path)
    plan_path = tmp_path / "remap-plan.json"
    create_remap_plan(ud, "/home/u/Projects", str(mac_proj.parent), plan_path, set(), set())
    shared = json.dumps(show_remap_plan(plan_path, shareable=True))
    for private in ("/home/u", str(tmp_path), "demo", ubuntu_id, workspace_id_for_folder(mac_proj)):
        assert private not in shared


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
