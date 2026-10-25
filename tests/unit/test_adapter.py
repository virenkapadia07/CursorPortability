import sqlite3
from pathlib import Path

import pytest
from tests.fixtures.db import COMPOSER_HEADERS_SQL, make_composer_headers_db, make_kv_db

from cursor_portability.models.enums import DetectionStatus
from cursor_portability.schemas.adapters.cursor_composer_headers import (
    CursorComposerHeadersAdapter,
)
from cursor_portability.schemas.adapters.vscode_style_kv_v1 import VsCodeStyleKvV1Adapter
from cursor_portability.schemas.registry import select_adapter
from cursor_portability.storage.paths import StagedPath
from cursor_portability.storage.sqlite_conn import open_readonly_db


def test_kv_adapter_pass(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    make_kv_db(db, [("a", b"b")])
    conn = sqlite3.connect(db)
    adapter = VsCodeStyleKvV1Adapter()
    det = adapter.detect(conn)
    assert det.status == DetectionStatus.PASS
    conn.close()


def test_plain_kv_db_selects_kv_adapter(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    make_kv_db(db, [("a", b"b")])
    with open_readonly_db(StagedPath(db)) as conn:
        det, inv = select_adapter(conn)
    assert det.status == DetectionStatus.PASS
    assert det.adapter_id == "vscode_style_kv"
    assert inv is not None


@pytest.mark.parametrize("layout", sorted(COMPOSER_HEADERS_SQL))
def test_each_composer_headers_layout_selects_its_own_adapter(tmp_path: Path, layout: int) -> None:
    db = tmp_path / "g.db"
    make_composer_headers_db(db, [("a", b"b")], [("c", b"d")], ["x", "y"], layout=layout)
    with open_readonly_db(StagedPath(db)) as conn:
        assert VsCodeStyleKvV1Adapter().detect(conn).status == DetectionStatus.PARTIAL
        det, inv = select_adapter(conn)
    assert det.status == DetectionStatus.PASS
    assert (det.adapter_id, det.adapter_version) == ("cursor_composer_headers", layout)
    assert inv is not None
    rows = {t.name: t.row_count for t in inv.tables}
    assert rows == {"ItemTable": 1, "composerHeaders": 2, "cursorDiskKV": 1}


@pytest.mark.parametrize(
    ("db_layout", "adapter_version", "expected"),
    [
        (2, 1, "different column order"),
        (3, 1, "missing (subagentTypeName)"),
        (1, 3, "extra (subagentTypeName)"),
    ],
)
def test_layout_mismatch_names_the_difference(
    tmp_path: Path, db_layout: int, adapter_version: int, expected: str
) -> None:
    db = tmp_path / "g.db"
    make_composer_headers_db(db, [], [], [], layout=db_layout)
    with open_readonly_db(StagedPath(db)) as conn:
        det = CursorComposerHeadersAdapter(adapter_version).detect(conn)
    assert det.status == DetectionStatus.PARTIAL
    assert det.message == f"composerHeaders columns differ from pinned layout: {expected}"


def test_changed_composer_headers_layout_fails_closed(tmp_path: Path) -> None:
    db = tmp_path / "g.db"
    make_composer_headers_db(db, [], [], [])
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE composerHeaders ADD COLUMN unexpected TEXT")
    conn.close()
    with open_readonly_db(StagedPath(db)) as ro:
        det, inv = select_adapter(ro)
    assert det.status == DetectionStatus.UNKNOWN
    assert inv is None
    assert det.message == (
        "vscode_style_kv@1: table composerHeaders is not key/value shaped; "
        "cursor_composer_headers@1: composerHeaders columns differ from pinned layout: "
        "extra (unexpected); "
        "cursor_composer_headers@2: composerHeaders columns differ from pinned layout: "
        "extra (unexpected); "
        "cursor_composer_headers@3: composerHeaders columns differ from pinned layout: "
        "extra (subagentTypeName, unexpected)"
    )


def test_changed_column_type_is_reported(tmp_path: Path) -> None:
    db = tmp_path / "g.db"
    conn = sqlite3.connect(db)
    conn.execute(COMPOSER_HEADERS_SQL[3].replace("value TEXT", "value BLOB"))
    conn.close()
    with open_readonly_db(StagedPath(db)) as ro:
        det = CursorComposerHeadersAdapter(3).detect(ro)
    assert det.message.endswith("changed type/constraints (value)")
