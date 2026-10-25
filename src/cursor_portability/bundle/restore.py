from __future__ import annotations

import shutil
from pathlib import Path

from cursor_portability.bundle.reader import ZipBundle
from cursor_portability.bundle.verify import verify_bundle
from cursor_portability.errors import AppError, ErrorCode
from cursor_portability.models.enums import ValidationOutcome
from cursor_portability.storage.diskspace import ensure_space
from cursor_portability.storage.fs_safe import assert_safe_output, new_run_id, publish_path


def restore_bundle(bundle: Path, target: Path) -> Path:
    assert_safe_output(target)
    is_zip = bundle.is_file() and bundle.suffix.lower() == ".zip"
    if not bundle.is_dir() and not is_zip:
        raise AppError(ErrorCode.BACKUP_INVALID, "unsupported bundle format")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.parent / f"{target.name}.partial-{new_run_id()}"
    try:
        if is_zip:
            _extract_zip(bundle, target.parent, partial)
        else:
            partial.mkdir()
            for item in bundle.iterdir():
                if item.is_file():
                    shutil.copy2(item, partial / item.name)
                elif item.is_dir():
                    shutil.copytree(item, partial / item.name)
        report = verify_bundle(partial)
        if report.outcome != ValidationOutcome.PASS:
            raise AppError(
                ErrorCode.BACKUP_INVALID,
                "bundle failed verification before restore",
                {"errors": report.errors},
            )
        publish_path(partial, target)
        return target
    finally:
        if partial.exists():
            shutil.rmtree(partial, ignore_errors=True)


def _extract_zip(bundle: Path, space_root: Path, partial: Path) -> None:
    with ZipBundle(bundle) as zb:
        if not ensure_space(space_root, zb.total_uncompressed):
            raise AppError(ErrorCode.INSUFFICIENT_DISK_SPACE, "not enough space to restore bundle")
        partial.mkdir()
        for name in zb.members:
            dest = partial.joinpath(*name.split("/"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            zb.extract_member(name, dest)
