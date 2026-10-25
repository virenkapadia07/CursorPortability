from pathlib import Path

from tests.fixtures.db import make_kv_db, make_wal_only_kv_db

from cursor_portability.storage.paths import StagedPath
from cursor_portability.storage.sqlite_conn import open_readonly_db
from cursor_portability.storage.sqlite_snapshot import cold_copy_sqlite_set


def test_cold_copy(tmp_path: Path) -> None:
    src = tmp_path / "a.db"
    make_kv_db(src, [("k", b"v")])
    dest = tmp_path / "staging"
    result = cold_copy_sqlite_set(src, dest)
    assert result.main_db.is_file()
    assert result.sha256


def test_cold_copy_folds_wal_into_staged_copy(tmp_path: Path) -> None:
    src = tmp_path / "src" / "state.vscdb"
    make_wal_only_kv_db(src, [("k", b"v")])
    source_sizes = {p.name: p.stat().st_size for p in src.parent.iterdir()}

    result = cold_copy_sqlite_set(src, tmp_path / "staging")

    assert result.copied_sidecars == ("state.vscdb-wal",)
    assert not Path(f"{result.main_db}-wal").exists()
    with open_readonly_db(StagedPath(result.main_db)) as conn:
        assert conn.execute("SELECT value FROM ItemTable WHERE key = 'k'").fetchone() == (b"v",)
    assert {p.name: p.stat().st_size for p in src.parent.iterdir()} == source_sizes
