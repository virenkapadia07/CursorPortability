from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from cursor_portability.errors import AppError, ErrorCode
from cursor_portability.storage.hashing import sha256_file
from cursor_portability.storage.paths import StagedPath
from cursor_portability.storage.sqlite_conn import open_staged_writable

_SIDECAR_SUFFIXES = ("-wal", "-journal")


@dataclass(frozen=True)
class FileStatSnap:
    size: int
    mtime_ns: int
    inode: int


@dataclass(frozen=True)
class SnapshotResult:
    main_db: Path
    copied_sidecars: tuple[str, ...]
    sha256: str


def _stat_snap(path: Path) -> FileStatSnap | None:
    try:
        st = path.stat()
        return FileStatSnap(st.st_size, st.st_mtime_ns, st.st_ino)
    except OSError:
        return None


def cold_copy_sqlite_set(source_db: Path, dest_dir: Path) -> SnapshotResult:
    """Copy main db and -wal / -journal sidecars (never -shm), then settle the copy.

    `sha256` is the digest of the main file as copied, before settling.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_db = dest_dir / source_db.name
    sources = [source_db]
    for suffix in _SIDECAR_SUFFIXES:
        side = Path(str(source_db) + suffix)
        if side.is_file():
            sources.append(side)

    before = {p: _stat_snap(p) for p in sources}
    if before[source_db] is None:
        raise AppError(ErrorCode.BACKUP_INVALID, f"missing database: {source_db.name}")

    for src in sources:
        dst = dest_dir / src.name
        shutil.copy2(src, dst)

    after = {p: _stat_snap(p) for p in sources}
    for p in sources:
        if before[p] != after[p]:
            for src in sources:
                shutil.copy2(src, dest_dir / src.name)
            after2 = {p: _stat_snap(p) for p in sources}
            for p in sources:
                if before[p] != after2[p]:
                    raise AppError(
                        ErrorCode.SOURCE_CHANGED,
                        "source changed during snapshot",
                        {"file": p.name},
                    )
            break

    sidecars = tuple(s.name for s in sources if s != source_db)
    digest = sha256_file(dest_db)
    if sidecars:
        settle_staged_copy(dest_db)
    return SnapshotResult(dest_db, sidecars, digest)


def settle_staged_copy(main_db: Path) -> None:
    """Fold a copied -wal (or roll back a copied hot -journal) into the staged main file.

    Read-only connections open with immutable=1, which ignores both sidecars; Cursor
    has been observed leaving workspace DBs whose entire content is still in the WAL.
    Only ever call this on a private staged copy.
    """
    with open_staged_writable(StagedPath(main_db)) as conn:
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        mode = conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
    leftovers = [s for s in _SIDECAR_SUFFIXES if Path(str(main_db) + s).exists()]
    if str(mode).lower() != "delete" or leftovers:
        raise AppError(
            ErrorCode.BACKUP_INVALID,
            "could not consolidate staged database",
            {"file": main_db.name, "journal_mode": str(mode), "leftovers": leftovers},
        )
