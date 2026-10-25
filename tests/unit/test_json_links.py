import copy
import json
import sqlite3
from typing import Any

import pytest
from tests.fixtures.db import COMPOSER_HEADERS_SQL, KV_TABLE_SQL
from tests.fixtures.remap_profile import linked_chat_header, workspace_ref

from cursor_portability.remap.json_links import (
    ChatLinkMismatch,
    FolderLink,
    rewrite_agent_state,
    rewrite_chat,
    rewrite_links,
)

_FROM, _TO = "b" * 32, "c" * 32
_OLD, _NEW = "/home/u/Projects/demo", "/Users/u/Projects/demo"
_LINK = FolderLink(_FROM, _TO, _OLD, _NEW, f"file://{_OLD}", f"file://{_NEW}")


@pytest.mark.parametrize(
    ("value", "mapped"),
    [
        (_OLD, _NEW),
        (f"{_OLD}/src/app.py", f"{_NEW}/src/app.py"),
        (f"file://{_OLD}", f"file://{_NEW}"),
        (f"file://{_OLD}/a%20b.txt", f"file://{_NEW}/a%20b.txt"),
        (f"{_OLD}-two", None),
        ("/home/u/Projects", None),
        (f"see {_OLD}", None),
    ],
)
def test_map_location_only_rewrites_the_folder_and_its_contents(
    value: str, mapped: str | None
) -> None:
    assert _LINK.map_location(value) == mapped


def test_rewrite_chat_moves_every_reference_to_the_workspace() -> None:
    doc = linked_chat_header(_FROM, _OLD)
    untouched = {k: copy.deepcopy(doc[k]) for k in ("draftTarget", "subtitle")}
    assert rewrite_chat(doc, _LINK)
    assert doc["workspaceIdentifier"] == workspace_ref(_TO, _NEW)
    assert doc["agentLocation"]["environment"] == workspace_ref(_TO, _NEW)
    assert doc["agentLocationHistory"][0]["location"]["environment"] == workspace_ref(_TO, _NEW)
    assert doc["trackedGitRepos"] == [{"repoPath": _NEW}, {"repoPath": "/home/u/Projects/other"}]
    assert {k: doc[k] for k in untouched} == untouched


def test_rewrite_chat_leaves_a_chat_without_references_alone() -> None:
    doc: dict[str, Any] = {"name": "x", "agentLocation": {"environment": {"id": "f" * 32}}}
    assert not rewrite_chat(doc, _LINK)
    assert doc == {"name": "x", "agentLocation": {"environment": {"id": "f" * 32}}}


def _with(path: tuple[str, ...], value: object) -> dict[str, Any]:
    doc = linked_chat_header(_FROM, _OLD)
    node: Any = doc
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return doc


@pytest.mark.parametrize(
    "doc",
    [
        _with(("workspaceIdentifier", "id"), "c" * 32),
        _with(("workspaceIdentifier", "uri", "path"), "/home/u/Projects/elsewhere"),
        _with(("workspaceIdentifier", "uri", "external"), "file:///elsewhere"),
        _with(("workspaceIdentifier", "configPath"), {"path": "/x.code-workspace"}),
        _with(("agentLocation", "environment", "uri", "fsPath"), "/elsewhere"),
        _with(("workspaceIdentifier",), "not-an-object"),
    ],
    ids=["other-id", "other-folder", "other-uri", "multi-root", "agent-folder", "not-object"],
)
def test_rewrite_chat_refuses_references_it_cannot_explain(doc: dict[str, Any]) -> None:
    with pytest.raises(ChatLinkMismatch):
        rewrite_chat(doc, _LINK)


def test_rewrite_agent_state_maps_ids_and_folder_strings_only() -> None:
    doc = {"a": [{"id": _FROM, "p": f"{_OLD}/x"}], "label": f"about {_OLD}", "n": 3}
    assert rewrite_agent_state(doc, (_LINK,))
    assert doc == {"a": [{"id": _TO, "p": f"{_NEW}/x"}], "label": f"about {_OLD}", "n": 3}
    with pytest.raises(ChatLinkMismatch):
        rewrite_agent_state(_OLD, (_LINK,))


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(KV_TABLE_SQL.format(name="ItemTable"))
    conn.execute(KV_TABLE_SQL.format(name="cursorDiskKV"))
    conn.execute(COMPOSER_HEADERS_SQL[1])
    header = json.dumps(linked_chat_header(_FROM, _OLD), ensure_ascii=False)
    conn.execute(
        "INSERT INTO composerHeaders (composerId, workspaceId, value) VALUES (?, ?, ?)",
        ("c1", _FROM, header),
    )
    conn.execute(
        "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
        ("composerData:c1", header.encode()),
    )
    tabs = json.dumps({"workspaceTabs": [{"props": {"repoPath": _OLD}}]}).encode()
    conn.executemany(
        "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
        [("cursor/workspaceTabs.v1", tabs), ("cursorAuth/token", tabs), ("cursor/apiKey", tabs)],
    )
    return conn


def _snapshot(conn: sqlite3.Connection) -> list[Any]:
    return [
        conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
        for t in ("composerHeaders", "cursorDiskKV", "ItemTable")
    ]


def test_rewrite_links_counts_without_writing_then_writes_keeping_storage_types() -> None:
    conn = _db()
    before = _snapshot(conn)
    expected = {"chat_headers": 1, "chat_records": 1, "agent_state": {"cursor<path>": 1}}
    assert rewrite_links(conn, (_LINK,), write=False).as_dict() == expected
    assert _snapshot(conn) == before
    assert rewrite_links(conn, (_LINK,), write=True).as_dict() == expected
    (header,) = conn.execute("SELECT value FROM composerHeaders").fetchone()
    (record,) = conn.execute("SELECT value FROM cursorDiskKV").fetchone()
    assert isinstance(header, str) and isinstance(record, bytes)
    assert json.loads(header)["workspaceIdentifier"] == workspace_ref(_TO, _NEW)
    assert "café" in header and "\\u" not in header
    assert json.loads(record) == json.loads(header)
    secrets = conn.execute(
        "SELECT value FROM ItemTable WHERE key IN ('cursorAuth/token', 'cursor/apiKey')"
    ).fetchall()
    assert all(_OLD in json.loads(v)["workspaceTabs"][0]["props"]["repoPath"] for (v,) in secrets)
