from __future__ import annotations

import platform
import shutil
import tempfile
import zipfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cursor_portability.bundle.layout import (
    CHECKSUMS_NAME,
    EXCLUSIONS_NAME,
    MANIFEST_NAME,
    README_NAME,
    SOURCE_REPORT_NAME,
)
from cursor_portability.bundle.manifest import Manifest, ManifestFileEntry, write_manifest
from cursor_portability.bundle.verify import verify_bundle
from cursor_portability.discovery.classify import classify_path
from cursor_portability.discovery.locate import locate_profiles
from cursor_portability.discovery.policy_v1 import POLICY_VERSION
from cursor_portability.discovery.walker import DEPTH_LIMIT, UNREADABLE, walk_root
from cursor_portability.errors import AppError, ErrorCode
from cursor_portability.models.enums import (
    CategoryAction,
    DataCategory,
    DetectionStatus,
    ValidationOutcome,
)
from cursor_portability.platform.base import get_platform_paths
from cursor_portability.platform.env import Environment
from cursor_portability.platform.process_probe import effective_running, probe_cursor_running
from cursor_portability.schemas.fingerprint import compute_fingerprint
from cursor_portability.schemas.registry import select_adapter
from cursor_portability.security.json_strict import dumps_canonical
from cursor_portability.security.scrub import (
    any_credential_keys_remain,
    scrub_staged_kv_database,
    vacuum_into,
)
from cursor_portability.storage.diskspace import ensure_space, estimate_backup_bytes
from cursor_portability.storage.fs_safe import (
    assert_not_protected,
    new_run_id,
    publish_path,
)
from cursor_portability.storage.hashing import sha256_file
from cursor_portability.storage.paths import StagedPath
from cursor_portability.storage.sqlite_conn import open_readonly_db, open_staged_writable
from cursor_portability.storage.sqlite_snapshot import cold_copy_sqlite_set

_WALK_MAX_DEPTH = 8


@dataclass(frozen=True)
class BackupReport:
    bundle_path: str
    outcome: ValidationOutcome
    files: int
    warnings: tuple[str, ...]


def bundle_output_path(output: Path, as_zip: bool) -> Path:
    if as_zip and output.suffix.lower() != ".zip":
        return output.with_name(output.name + ".zip")
    return output


def create_backup(
    env: Environment,
    output: Path,
    as_zip: bool = False,
    overwrite: bool = False,
    confirm_overwrite: Callable[[Path], bool] | None = None,
    confirm_cursor_closed: bool = False,
    include_categories: set[DataCategory] | None = None,
    exclude_categories: set[DataCategory] | None = None,
    opt_in_mcp: bool = False,
) -> BackupReport:
    paths = get_platform_paths(env)
    profiles = locate_profiles(paths)
    if not profiles:
        raise AppError(ErrorCode.PROFILE_NOT_FOUND, "no Cursor profile found")
    ud = profiles[0].user_data_dir
    ch = profiles[0].cursor_home
    state = probe_cursor_running(ud)
    if effective_running(state):
        if state.value == "UNKNOWN" and confirm_cursor_closed:
            pass
        else:
            raise AppError(ErrorCode.CURSOR_RUNNING, "close Cursor before backup")

    final = bundle_output_path(output, as_zip)
    assert_not_protected(final)
    assert_not_protected(final.parent)
    if final.exists() or final.is_symlink():
        if not overwrite:
            raise AppError(
                ErrorCode.OUTPUT_EXISTS, "output exists; use --overwrite", {"path": str(final)}
            )
        if not _is_replaceable_bundle(final):
            raise AppError(
                ErrorCode.OUTPUT_EXISTS,
                "refusing to overwrite a path that is not a cursor-port bundle",
                {"path": str(final)},
            )
        if confirm_overwrite is None or not confirm_overwrite(final):
            raise AppError(ErrorCode.OUTPUT_EXISTS, "overwrite not confirmed", {"path": str(final)})

    final.parent.mkdir(parents=True, exist_ok=True)
    largest_db = _largest_db_bytes(ud, ch)
    if not ensure_space(final.parent, estimate_backup_bytes(largest_db)):
        raise AppError(ErrorCode.INSUFFICIENT_DISK_SPACE, "insufficient disk space")

    run_id = new_run_id()
    staging = final.parent / f"{final.name}.partial-{run_id}"
    zip_artifact = final.parent / f"{final.name}.partial-{run_id}.zip"
    staging.mkdir()
    warnings: list[str] = []
    warnings.append("Bundle may contain sensitive conversation content; store encrypted if needed.")

    manifest = Manifest(
        source_os=platform.system(),
        source_arch=platform.machine(),
    )
    exclusions: list[dict[str, str]] = []
    checksum_lines: list[str] = []

    try:
        _copy_tree_files(
            ud,
            ch,
            staging,
            manifest,
            checksum_lines,
            exclusions,
            include_categories,
            exclude_categories,
            opt_in_mcp,
            warnings,
        )
        write_manifest(staging / MANIFEST_NAME, manifest)
        (staging / CHECKSUMS_NAME).write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
        exclusions.sort(key=lambda e: (e["root"], e["path"]))
        (staging / EXCLUSIONS_NAME).write_text(
            dumps_canonical({"policy_version": POLICY_VERSION, "excluded": exclusions}) + "\n",
            encoding="utf-8",
        )
        (staging / SOURCE_REPORT_NAME).write_text('{"redacted":true}\n', encoding="utf-8")
        (staging / README_NAME).write_text(
            "CursorPortability bundle. Restore with: cursor-port backup restore BUNDLE --to NEW_DIR\n",
            encoding="utf-8",
        )

        artifact = staging
        if as_zip:
            _dir_to_zip(staging, zip_artifact)
            artifact = zip_artifact

        verify = verify_bundle(artifact)
        if verify.outcome != ValidationOutcome.PASS:
            raise AppError(
                ErrorCode.BACKUP_INVALID, "post-create verify failed", {"errors": verify.errors}
            )
        publish_path(artifact, final, replace_existing=overwrite)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if zip_artifact.exists():
            zip_artifact.unlink()
    return BackupReport(str(final), ValidationOutcome.PASS, len(manifest.files), tuple(warnings))


def _is_replaceable_bundle(path: Path) -> bool:
    if path.is_symlink():
        return False
    if path.is_dir():
        return (path / MANIFEST_NAME).is_file()
    if path.is_file() and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            return MANIFEST_NAME in zf.namelist()
    return False


def _largest_db_bytes(ud: Path, ch: Path | None) -> int:
    dbs = [ud / "User" / "globalStorage" / "state.vscdb"]
    ws = ud / "User" / "workspaceStorage"
    if ws.is_dir():
        dbs.extend(child / "state.vscdb" for child in ws.iterdir())
    largest = 0
    for db in dbs:
        if db.is_file():
            wal = Path(f"{db}-wal")
            wal_size = wal.stat().st_size if wal.is_file() else 0
            largest = max(largest, db.stat().st_size + wal_size)
    return largest


def _exclusion_reason(
    category: DataCategory,
    action: CategoryAction,
    include: set[DataCategory] | None,
    exclude: set[DataCategory] | None,
    opt_in_mcp: bool,
) -> str | None:
    if exclude and category in exclude:
        return "EXCLUDED_BY_FLAG"
    if include and category not in include:
        return "NOT_IN_INCLUDE_LIST"
    if action == CategoryAction.OPT_IN and category == DataCategory.MCP_CONFIG and opt_in_mcp:
        return None
    if action == CategoryAction.INCLUDE:
        return None
    return action.value


def _copy_tree_files(
    ud: Path,
    ch: Path | None,
    staging: Path,
    manifest: Manifest,
    checksum_lines: list[str],
    exclusions: list[dict[str, str]],
    include: set[DataCategory] | None,
    exclude: set[DataCategory] | None,
    opt_in_mcp: bool,
    warnings: list[str],
) -> None:
    roots: list[tuple[Path, str]] = [(ud, "user-data")]
    if ch:
        roots.append((ch, "cursor-home"))
    skipped: Counter[str] = Counter()
    for root, token in roots:
        for we in walk_root(root, token, max_depth=_WALK_MAX_DEPTH):
            if we.is_symlink or we.is_junction:
                exclusions.append(
                    {
                        "root": token,
                        "path": we.relative_path,
                        "category": "",
                        "reason": "LINK_NOT_FOLLOWED",
                    }
                )
                continue
            if we.skip_reason:
                skipped[we.skip_reason] += 1
                exclusions.append(
                    {
                        "root": token,
                        "path": we.relative_path,
                        "category": classify_path(token, we.relative_path).category.value,
                        "reason": we.skip_reason,
                    }
                )
                continue
            if we.is_dir:
                continue
            cls = classify_path(token, we.relative_path)
            reason = _exclusion_reason(cls.category, cls.action, include, exclude, opt_in_mcp)
            detail = ""
            if reason is None:
                src = root / we.relative_path
                rel_bundle = f"{token}/{we.relative_path}".replace("\\", "/")
                if rel_bundle.endswith(".vscdb") or rel_bundle.endswith(".db"):
                    rejected = _bundle_db(
                        src, staging, rel_bundle, manifest, checksum_lines, warnings
                    )
                    if rejected:
                        reason, detail = rejected
                else:
                    dest = staging / rel_bundle
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                    digest = sha256_file(dest)
                    manifest.files.append(
                        ManifestFileEntry(rel_bundle, digest, dest.stat().st_size)
                    )
                    checksum_lines.append(f"{digest}  {rel_bundle}")
            if reason is not None:
                entry = {
                    "root": token,
                    "path": we.relative_path,
                    "category": cls.category.value,
                    "reason": reason,
                }
                if detail:
                    entry["detail"] = detail
                exclusions.append(entry)
    if skipped[DEPTH_LIMIT]:
        warnings.append(
            f"contents of {skipped[DEPTH_LIMIT]} directories at the scan depth limit "
            f"({_WALK_MAX_DEPTH + 1} levels) were not scanned; listed in exclusions.json as "
            "DEPTH_LIMIT"
        )
    if skipped[UNREADABLE]:
        warnings.append(
            f"{skipped[UNREADABLE]} paths could not be read; listed in exclusions.json as UNREADABLE"
        )


def _bundle_db(
    src: Path,
    staging: Path,
    rel_bundle: str,
    manifest: Manifest,
    checksum_lines: list[str],
    warnings: list[str],
) -> tuple[str, str] | None:
    """Returns (reason, detail) if the database was left out of the bundle."""
    with tempfile.TemporaryDirectory(prefix="cursor-port-db-") as tmp:
        snap = cold_copy_sqlite_set(src, Path(tmp))
        out_name = rel_bundle.replace(".vscdb", ".sqlite").replace(".db", ".sqlite")
        if not out_name.endswith(".sqlite"):
            out_name = rel_bundle + ".sqlite"
        with open_readonly_db(StagedPath(snap.main_db)) as ro:
            det, _ = select_adapter(ro)
            if det.status != DetectionStatus.PASS:
                warnings.append(f"database {rel_bundle} excluded ({det.status}): {det.message}")
                return f"SCHEMA_{det.status.value}", det.message
            manifest.schema_adapters[out_name] = f"{det.adapter_id}@{det.adapter_version}"
            qc = ro.execute("PRAGMA quick_check").fetchone()
            manifest.sqlite_integrity[out_name] = str(qc[0]) if qc else "unknown"
        out_path = staging / out_name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open_staged_writable(StagedPath(snap.main_db)) as wr:
            report = scrub_staged_kv_database(wr)
            manifest.scrub_reports[out_name] = {
                "pattern_version": report.pattern_version,
                "deleted": report.deleted_by_pattern,
            }
            vacuum_into(wr, out_path)
        with open_readonly_db(StagedPath(out_path)) as verify_conn:
            if any_credential_keys_remain(verify_conn):
                raise AppError(ErrorCode.BACKUP_INVALID, "credential keys remain after scrub")
            manifest.schema_fingerprints[out_name] = compute_fingerprint(verify_conn).digest
        digest = sha256_file(out_path)
        manifest.files.append(ManifestFileEntry(out_name, digest, out_path.stat().st_size))
        checksum_lines.append(f"{digest}  {out_name}")
    return None


def _dir_to_zip(staging: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "x", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(staging).as_posix())
