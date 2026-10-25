import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from tests.fixtures.profile import (
    CANARY,
    LAYOUT_WORKSPACES,
    WAL_ONLY_WORKSPACE,
    build_synthetic_profile,
)

from cursor_portability.cli.main import main
from cursor_portability.platform.env import Environment
from cursor_portability.services.backup import create_backup
from cursor_portability.storage.paths import BundlePath
from cursor_portability.storage.sqlite_conn import open_readonly_db


def _assert_no_canary(root: Path) -> None:
    for f in root.rglob("*"):
        if f.is_file():
            assert CANARY not in f.read_bytes(), f


def test_backup_create_verify_restore(synthetic_profile, cursor_not_running, tmp_path) -> None:
    out = tmp_path / "bundle"
    assert (
        main(["--json", "backup", "create", "--output", str(out), "--confirm-cursor-closed"]) == 0
    )
    assert main(["--json", "backup", "verify", str(out)]) == 0
    restore_to = tmp_path / "restored"
    assert main(["backup", "restore", str(out), "--to", str(restore_to)]) == 0
    manifest = json.loads((restore_to / "manifest.json").read_text())
    assert manifest["bundle_format_major"] == 1
    _assert_no_canary(restore_to)


def _rows(db: Path, sql: str) -> list[tuple[object, ...]]:
    with open_readonly_db(BundlePath(db)) as conn:
        return conn.execute(sql).fetchall()


def test_every_supported_database_is_bundled(
    synthetic_profile, cursor_not_running, tmp_path
) -> None:
    out = tmp_path / "bundle"
    assert main(["backup", "create", "--output", str(out)]) == 0
    user = out / "user-data" / "User"

    global_db = user / "globalStorage" / "state.sqlite"
    assert _rows(global_db, "SELECT composerId FROM composerHeaders ORDER BY 1") == [
        ("c-1",),
        ("c-2",),
    ]
    assert _rows(global_db, "SELECT key FROM cursorDiskKV") == [("synthetic.entry:1",)]

    wal_only_db = user / "workspaceStorage" / WAL_ONLY_WORKSPACE / "state.sqlite"
    assert _rows(wal_only_db, "SELECT key, value FROM ItemTable") == [("wal.only.key", b"kept")]

    for layout, ws_id in LAYOUT_WORKSPACES.items():
        db = user / "workspaceStorage" / ws_id / "state.sqlite"
        assert _rows(db, "SELECT composerId FROM composerHeaders") == [(f"layout-{layout}",)]

    excluded = json.loads((out / "exclusions.json").read_text())["excluded"]
    assert not [e for e in excluded if e["reason"].startswith("SCHEMA_")]

    ws = "user-data/User/workspaceStorage"
    assert json.loads((out / "manifest.json").read_text())["schema_adapters"] == {
        "user-data/User/globalStorage/state.sqlite": "cursor_composer_headers@1",
        f"{ws}/abc123def4567890abc123def4567890/state.sqlite": "vscode_style_kv@1",
        f"{ws}/{WAL_ONLY_WORKSPACE}/state.sqlite": "vscode_style_kv@1",
        f"{ws}/{LAYOUT_WORKSPACES[2]}/state.sqlite": "cursor_composer_headers@2",
        f"{ws}/{LAYOUT_WORKSPACES[3]}/state.sqlite": "cursor_composer_headers@3",
    }


def test_exclusions_record_omitted_paths(synthetic_profile, cursor_not_running, tmp_path) -> None:
    unsupported = synthetic_profile / "User" / "workspaceStorage" / "unsupported" / "state.vscdb"
    unsupported.parent.mkdir()
    conn = sqlite3.connect(unsupported)
    conn.execute("CREATE TABLE other (a TEXT, b TEXT)")
    conn.commit()
    conn.close()

    out = tmp_path / "bundle"
    assert main(["backup", "create", "--output", str(out)]) == 0
    data = json.loads((out / "exclusions.json").read_text())
    assert data["policy_version"] == 1
    by_path = {(e["root"], e["path"]): e for e in data["excluded"]}
    assert by_path[("user-data", "logs/main.log")]["reason"] == "EXCLUDE"
    assert by_path[("user-data", "logs/main.log")]["category"] == "LOGS"
    assert by_path[("cursor-home", "projects/demo/mcp-auth.json")]["category"] == "AUTH_MATERIAL"
    assert not (out / "cursor-home" / "projects").exists()

    rejected = by_path[("user-data", "User/workspaceStorage/unsupported/state.vscdb")]
    assert rejected["reason"] == "SCHEMA_UNKNOWN"
    assert "vscode_style_kv@1: table other is not key/value shaped" in rejected["detail"]
    assert "cursor_composer_headers@1: no composerHeaders table" in rejected["detail"]


def test_paths_past_the_depth_limit_are_recorded(
    synthetic_profile, cursor_not_running, tmp_path, capsys
) -> None:
    boundary = "User/snippets/d1/d2/d3/d4/d5/d6/d7"
    (synthetic_profile / boundary).mkdir(parents=True)
    (synthetic_profile / boundary / "deep.json").write_text("{}")

    out = tmp_path / "bundle"
    assert main(["backup", "create", "--output", str(out)]) == 0

    assert not (out / "user-data" / boundary).exists()
    excluded = json.loads((out / "exclusions.json").read_text())["excluded"]
    assert {
        "root": "user-data",
        "path": boundary,
        "category": "SNIPPETS",
        "reason": "DEPTH_LIMIT",
    } in excluded
    assert "contents of 1 directories at the scan depth limit (9 levels)" in capsys.readouterr().err


def test_zip_create_verify_list_restore(
    synthetic_profile, cursor_not_running, tmp_path, capsys
) -> None:
    out = tmp_path / "bundle"
    assert main(["backup", "create", "--output", str(out), "--zip"]) == 0
    zip_path = tmp_path / "bundle.zip"
    assert zip_path.is_file()
    assert not out.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bundle.zip", "home"]
    assert main(["--json", "backup", "verify", str(zip_path), "--full"]) == 0
    capsys.readouterr()
    assert main(["--json", "backup", "list", str(zip_path)]) == 0
    listed = json.loads(capsys.readouterr().out)
    with zipfile.ZipFile(zip_path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert listed["files"] == len(manifest["files"]) > 0
    restore_to = tmp_path / "restored"
    assert main(["backup", "restore", str(zip_path), "--to", str(restore_to)]) == 0
    assert (restore_to / "user-data" / "User" / "globalStorage" / "state.sqlite").is_file()
    _assert_no_canary(restore_to)


def test_detect_json(synthetic_profile, capsys) -> None:
    assert main(["--json", "detect"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert len(report["profiles"]) == 1
    assert Path(report["profiles"][0]["user_data_dir"]) == synthetic_profile.resolve()


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_each_platform_layout_backs_up(fake_home, cursor_not_running, tmp_path, platform) -> None:
    build_synthetic_profile(fake_home, platform)
    home = fake_home.resolve()
    env = Environment(
        home=home,
        platform=platform,
        os_name="nt" if platform == "win32" else "posix",
        xdg_config_home=home / ".config",
        xdg_data_home=None,
        appdata=home / "AppData" / "Roaming",
        userprofile=home,
        localappdata=home / "AppData" / "Local",
        wsl_distro_name=None,
    )
    report = create_backup(env, tmp_path / "bundle")
    assert report.files > 0
    assert (tmp_path / "bundle" / "user-data" / "User" / "settings.json").is_file()
    assert (tmp_path / "bundle" / "cursor-home" / "extensions" / "extensions.json").is_file()
    _assert_no_canary(tmp_path / "bundle")
