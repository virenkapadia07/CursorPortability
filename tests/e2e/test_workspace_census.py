import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest
from tests.fixtures.remap_profile import build_ubuntu_workspace_profile

from cursor_portability.cli.main import main
from cursor_portability.planning.workspace_id import workspace_id_for_folder
from cursor_portability.services.census import run_workspace_census

_UBUNTU_ID = "b" * 32
_DEMO = "/home/u/Projects/demo"
_COMPOSER_ID = "0b4f1d2e-0000-4000-8000-00000000c0de"
_BRANCH_KEY = "branchMetadata.prUrl.v1::github.com/acme/widgets::feat-hush-launch"


def _tree_hash(root: Path) -> str:
    lines = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            lines.append(f"{p.relative_to(root)} {st.st_size} {st.st_mtime_ns}")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def test_census_is_read_only(fake_home, tmp_path) -> None:
    if sys.platform != "darwin":
        return
    ud = fake_home / "Library" / "Application Support" / "Cursor"
    ubuntu_id = "a" * 32
    mac_proj = tmp_path / "Projects" / "demo"
    build_ubuntu_workspace_profile(
        ud,
        ubuntu_id,
        "/home/u/Projects/demo",
        mac_proj,
    )
    before = _tree_hash(ud)
    assert (
        main(
            [
                "workspaces",
                "census",
                "--from",
                "/home/u/Projects",
                "--to",
                str(tmp_path / "Projects"),
                "--target-profile",
                str(ud),
                "--json",
            ]
        )
        == 0
    )
    assert before == _tree_hash(ud)


def _profile_with_path_fields(fake_home: Path, tmp_path: Path) -> tuple[Path, Path]:
    """Ubuntu paths in the same kinds of places a real census found them."""
    ud = fake_home / "Library" / "Application Support" / "Cursor"
    mac_proj = tmp_path / "Projects" / "demo"
    build_ubuntu_workspace_profile(ud, _UBUNTU_ID, _DEMO, mac_proj, composer_rows=0)
    ident = {
        "id": _UBUNTU_ID,
        "uri": {"$mid": 1, "fsPath": _DEMO, "external": f"file://{_DEMO}"},
    }
    header = {"workspaceIdentifier": ident, "name": f"fix {_DEMO}/app.py"}
    composer = {
        "workspaceIdentifier": ident,
        "context": {"fileSelections": [{"uri": {"fsPath": f"{_DEMO}/app.py"}}]},
        "richText": json.dumps({"root": {"text": f"see {_DEMO}"}}),
        "codeBlockData": {f"{_DEMO}/app.py": {"v": 1}},
    }
    conn = sqlite3.connect(ud / "User" / "globalStorage" / "state.vscdb")
    conn.execute(
        "INSERT INTO composerHeaders (composerId, workspaceId, value) VALUES (?, ?, ?)",
        (_COMPOSER_ID, _UBUNTU_ID, json.dumps(header)),
    )
    conn.executemany(
        "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
        [
            ("glass.localAgentProjects.v1", json.dumps([{"path": _DEMO, "label": "demo"}])),
            (_BRANCH_KEY, "https://github.com/acme/widgets/pull/7"),
        ],
    )
    conn.execute(
        "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
        (f"composerData:{_COMPOSER_ID}", json.dumps(composer)),
    )
    conn.commit()
    conn.close()
    ws_db = sqlite3.connect(ud / "User" / "workspaceStorage" / _UBUNTU_ID / "state.vscdb")
    ws_db.execute(
        "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
        ("composer.composerData", json.dumps({"allComposers": [{"folder": f"file://{_DEMO}"}]})),
    )
    ws_db.commit()
    ws_db.close()
    return ud, mac_proj


def _hits(path_hits: list[dict[str, Any]], table: str) -> dict[str, Any]:
    (row,) = [h for h in path_hits if h["table"] == table and h["column"] == "value"]
    return row


@pytest.mark.skipif(sys.platform != "darwin", reason="workspace id formula is macOS-only")
def test_census_reports_which_fields_hold_the_old_prefix(fake_home, tmp_path) -> None:
    ud, mac_proj = _profile_with_path_fields(fake_home, tmp_path)
    body = run_workspace_census(ud, "/home/u/Projects", str(mac_proj.parent), shareable=True).body
    global_hits = body["global_database"]["path_hits"]

    headers = _hits(global_hits, "composerHeaders")
    assert headers["encoding_hits"]["file_uri"] == 1
    assert headers["fields"]["value"]["fields"] == {
        "name (embedded)": 1,
        "workspaceIdentifier.uri.external (file_uri)": 1,
        "workspaceIdentifier.uri.fsPath (path)": 1,
    }
    items = _hits(global_hits, "ItemTable")
    assert items["key_patterns"] == {"glass.localAgentProjects.v1": 1}
    assert items["fields"]["glass.localAgentProjects.v1"]["fields"] == {"[].path (path)": 1}
    composer = _hits(global_hits, "cursorDiskKV")["fields"]["composerData:<uuid>"]["fields"]
    assert composer == {
        "codeBlockData.<key> (in key)": 1,
        "context.fileSelections[].uri.fsPath (path)": 1,
        "richText<json>.root.text (embedded)": 1,
        "workspaceIdentifier.uri.external (file_uri)": 1,
        "workspaceIdentifier.uri.fsPath (path)": 1,
    }
    assert "branchMetadata.prUrl.v1::<text>" in body["global_database"]["item_key_shapes"]
    ws_items = _hits(body["workspace_databases"]["path_hits"], "ItemTable")
    assert ws_items["databases"] == 1
    assert ws_items["fields"]["composer.composerData"]["fields"] == {
        "allComposers[].folder (file_uri)": 1
    }

    link = {
        "rows": 1,
        "not_json_object": 0,
        "fields_equal_workspace_id": {"workspaceIdentifier.id": 1},
        "fields_equal_folder": {
            "workspaceIdentifier.uri.external (= folder uri)": 1,
            "workspaceIdentifier.uri.fsPath (= folder path)": 1,
        },
        "workspace_identifier_fields": {
            "workspaceIdentifier.id": 1,
            "workspaceIdentifier.uri.$mid": 1,
            "workspaceIdentifier.uri.external": 1,
            "workspaceIdentifier.uri.fsPath": 1,
        },
    }
    assert body["chat_links"] == {
        "composer_headers": {"mapped_workspaces": link},
        "composer_data": {"mapped_workspaces": link},
    }

    shared = json.dumps(body)
    for private in (
        "/home/u",
        str(tmp_path),
        "demo",
        "app.py",
        "acme",
        "widgets",
        "hush",
        _COMPOSER_ID,
        _UBUNTU_ID,
        workspace_id_for_folder(mac_proj),
    ):
        assert private not in shared
