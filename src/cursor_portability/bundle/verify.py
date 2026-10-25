from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from cursor_portability.bundle.layout import (
    CHECKSUMS_NAME,
    EXCLUSIONS_NAME,
    MANIFEST_NAME,
    README_NAME,
    SOURCE_REPORT_NAME,
)
from cursor_portability.bundle.manifest import Manifest, read_manifest
from cursor_portability.bundle.reader import ZipBundle
from cursor_portability.errors import AppError, ErrorCode
from cursor_portability.models.enums import ValidationOutcome
from cursor_portability.schemas.fingerprint import compute_fingerprint
from cursor_portability.security.scrub import any_credential_keys_remain
from cursor_portability.storage.diskspace import ensure_space
from cursor_portability.storage.hashing import sha256_file
from cursor_portability.storage.paths import BundlePath
from cursor_portability.storage.sqlite_conn import open_readonly_db

METADATA_FILES = frozenset(
    {MANIFEST_NAME, CHECKSUMS_NAME, README_NAME, EXCLUSIONS_NAME, SOURCE_REPORT_NAME}
)
_DB_SUFFIXES = (".sqlite", ".vscdb", ".db")


@dataclass(frozen=True)
class VerifyReport:
    outcome: ValidationOutcome
    errors: tuple[str, ...]
    files_checked: int


def verify_bundle(path: Path, full_integrity: bool = False) -> VerifyReport:
    errors: list[str] = []
    try:
        if path.is_dir():
            checked = _verify_dir(path, errors, full_integrity)
        elif path.is_file() and path.suffix.lower() == ".zip":
            checked = _verify_zip(path, errors, full_integrity)
        else:
            return VerifyReport(ValidationOutcome.FAIL, ("unknown bundle type",), 0)
    except AppError as exc:
        return VerifyReport(ValidationOutcome.FAIL, (*errors, str(exc)), 0)

    if errors:
        return VerifyReport(ValidationOutcome.FAIL, tuple(errors), checked)
    return VerifyReport(ValidationOutcome.PASS, (), checked)


def is_db_path(rel: str) -> bool:
    return rel.endswith(_DB_SUFFIXES)


def _check_file_set(manifest: Manifest, present: set[str], errors: list[str]) -> None:
    manifest_paths = {f.path for f in manifest.files}
    missing = manifest_paths - present
    extra = present - manifest_paths - METADATA_FILES
    for rel in sorted(missing):
        errors.append(f"missing file: {rel}")
    if extra:
        errors.append(f"unmanifested files: {len(extra)}")


def _verify_dir(path: Path, errors: list[str], full: bool) -> int:
    manifest_path = path / MANIFEST_NAME
    if not manifest_path.is_file():
        errors.append("missing manifest")
        return 0
    manifest = read_manifest(manifest_path)
    present = {child.relative_to(path).as_posix() for child in path.rglob("*") if child.is_file()}
    _check_file_set(manifest, present, errors)
    checked = 0
    for fe in manifest.files:
        if fe.path not in present:
            continue
        fp = path / fe.path
        if fp.stat().st_size != fe.size:
            errors.append(f"size mismatch: {fe.path}")
        if sha256_file(fp) != fe.sha256:
            errors.append(f"hash mismatch: {fe.path}")
        checked += 1
        if is_db_path(fe.path):
            _verify_db(fp, fe.path, manifest, errors, full)
    return checked


def _verify_zip(path: Path, errors: list[str], full: bool) -> int:
    checked = 0
    with ZipBundle(path) as bundle:
        manifest = bundle.read_manifest()
        _check_file_set(manifest, set(bundle.members), errors)
        db_entries = [
            fe for fe in manifest.files if fe.path in bundle.members and is_db_path(fe.path)
        ]
        largest_db = max((bundle.members[fe.path].file_size for fe in db_entries), default=0)
        with tempfile.TemporaryDirectory(prefix="cursor-port-verify-") as tmp:
            if largest_db and not ensure_space(Path(tmp), largest_db):
                raise AppError(
                    ErrorCode.INSUFFICIENT_DISK_SPACE,
                    "not enough temp space to verify zip databases",
                )
            for fe in manifest.files:
                info = bundle.members.get(fe.path)
                if info is None:
                    continue
                if info.file_size != fe.size:
                    errors.append(f"size mismatch: {fe.path}")
                    continue
                if is_db_path(fe.path):
                    extracted = Path(tmp) / f"entry-{checked}.sqlite"
                    digest = bundle.extract_member(fe.path, extracted)
                    if digest != fe.sha256:
                        errors.append(f"hash mismatch: {fe.path}")
                    else:
                        _verify_db(extracted, fe.path, manifest, errors, full)
                    extracted.unlink()
                elif bundle.hash_member(fe.path) != fe.sha256:
                    errors.append(f"hash mismatch: {fe.path}")
                checked += 1
    return checked


def _verify_db(
    fp: Path,
    rel: str,
    manifest: Manifest,
    errors: list[str],
    full: bool,
) -> None:
    try:
        with open_readonly_db(BundlePath(fp)) as conn:
            check = "integrity_check" if full else "quick_check"
            row = conn.execute(f"PRAGMA {check}").fetchone()
            if row and row[0] != "ok":
                errors.append(f"sqlite {check} failed: {rel}")
            if any_credential_keys_remain(conn):
                errors.append(f"credential keys remain: {rel}")
            fp_digest = compute_fingerprint(conn).digest
            expected = manifest.schema_fingerprints.get(rel)
            if expected and expected != fp_digest:
                errors.append(f"fingerprint mismatch: {rel}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"db verify error {rel}: {exc}")
