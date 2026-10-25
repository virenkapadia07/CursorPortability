"""Read access to bundles. ZIP bundles are untrusted input."""

from __future__ import annotations

import hashlib
import os
import stat
import zipfile
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType

from cursor_portability.bundle.layout import MANIFEST_NAME
from cursor_portability.bundle.manifest import (
    MAX_MANIFEST_BYTES,
    Manifest,
    parse_manifest,
    read_manifest,
)
from cursor_portability.errors import AppError, ErrorCode
from cursor_portability.security.archive_validation import (
    check_bomb_limits,
    validate_archive_member_name,
)

_CHUNK = 1024 * 1024


class ZipBundle:
    """Validates every entry on open; member contents are only ever streamed."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._zf: zipfile.ZipFile | None = None
        self.members: dict[str, zipfile.ZipInfo] = {}

    def __enter__(self) -> ZipBundle:
        try:
            zf = zipfile.ZipFile(self.path, "r")
        except zipfile.BadZipFile as exc:
            raise AppError(ErrorCode.ARCHIVE_REJECTED, f"not a valid zip: {exc}") from exc
        try:
            self.members = _validated_members(zf)
        except BaseException:
            zf.close()
            raise
        self._zf = zf
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._zf is not None:
            self._zf.close()
            self._zf = None

    @property
    def total_uncompressed(self) -> int:
        return sum(info.file_size for info in self.members.values())

    def read_manifest(self) -> Manifest:
        info = self.members.get(MANIFEST_NAME)
        if info is None:
            raise AppError(ErrorCode.BACKUP_INVALID, "zip missing manifest")
        if info.file_size > MAX_MANIFEST_BYTES:
            raise AppError(ErrorCode.BACKUP_INVALID, "manifest too large")
        raw = b"".join(self._iter_member(info))
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AppError(ErrorCode.BACKUP_INVALID, "manifest is not UTF-8") from exc
        return parse_manifest(text)

    def hash_member(self, name: str) -> str:
        digest = hashlib.sha256()
        for chunk in self._iter_member(self.members[name]):
            digest.update(chunk)
        return digest.hexdigest()

    def extract_member(self, name: str, dest: Path) -> str:
        """Stream one member to a new file at dest; returns its SHA-256."""
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        digest = hashlib.sha256()
        fd = os.open(dest, flags, 0o600)
        with os.fdopen(fd, "wb") as out:
            for chunk in self._iter_member(self.members[name]):
                out.write(chunk)
                digest.update(chunk)
        return digest.hexdigest()

    def _iter_member(self, info: zipfile.ZipInfo) -> Iterator[bytes]:
        if self._zf is None:
            raise RuntimeError("ZipBundle is not open")
        read = 0
        try:
            with self._zf.open(info, "r") as src:
                while chunk := src.read(_CHUNK):
                    read += len(chunk)
                    if read > info.file_size:
                        raise AppError(ErrorCode.ARCHIVE_REJECTED, "entry exceeds declared size")
                    yield chunk
        except (zipfile.BadZipFile, EOFError, OSError) as exc:
            raise AppError(
                ErrorCode.ARCHIVE_REJECTED, f"corrupt entry {info.filename}: {exc}"
            ) from exc
        if read != info.file_size:
            raise AppError(ErrorCode.ARCHIVE_REJECTED, f"truncated entry {info.filename}")


def _validated_members(zf: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = zf.infolist()
    members: dict[str, zipfile.ZipInfo] = {}
    folded: set[str] = set()
    total = 0
    for info in infos:
        # zipfile truncates at NUL and rewrites os.sep; validate the raw name.
        validate_archive_member_name(info.orig_filename)
        if info.flag_bits & 0x1:
            raise AppError(ErrorCode.ARCHIVE_REJECTED, "encrypted entries are not supported")
        # Many writers store permission bits only, so a zero file type is allowed.
        ftype = stat.S_IFMT(info.external_attr >> 16)
        if info.is_dir():
            if ftype not in (0, stat.S_IFDIR):
                raise AppError(ErrorCode.ARCHIVE_REJECTED, "special directory entry")
            continue
        if ftype not in (0, stat.S_IFREG):
            raise AppError(ErrorCode.ARCHIVE_REJECTED, f"symlink or special entry: {info.filename}")
        if info.filename in members:
            raise AppError(ErrorCode.ARCHIVE_REJECTED, f"duplicate entry: {info.filename}")
        key = info.filename.casefold()
        if key in folded:
            raise AppError(
                ErrorCode.ARCHIVE_REJECTED, f"case-insensitive name collision: {info.filename}"
            )
        folded.add(key)
        total += info.file_size
        check_bomb_limits(info.file_size, info.compress_size, total, len(infos))
        members[info.filename] = info
    return members


def read_bundle_manifest(path: Path) -> Manifest:
    if path.is_dir():
        manifest_path = path / MANIFEST_NAME
        if not manifest_path.is_file():
            raise AppError(ErrorCode.BACKUP_INVALID, "missing manifest")
        return read_manifest(manifest_path)
    if path.is_file() and path.suffix.lower() == ".zip":
        with ZipBundle(path) as bundle:
            return bundle.read_manifest()
    raise AppError(ErrorCode.BACKUP_INVALID, "unknown bundle type")
