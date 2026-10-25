from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cursor_portability import __version__
from cursor_portability.bundle.layout import BUNDLE_FORMAT_MAJOR
from cursor_portability.errors import AppError, ErrorCode
from cursor_portability.security.archive_validation import validate_archive_member_name
from cursor_portability.security.json_strict import StrictJsonError, dumps_canonical, loads_strict

MAX_MANIFEST_BYTES = 2 * 1024 * 1024


@dataclass
class ManifestFileEntry:
    path: str
    sha256: str
    size: int


@dataclass
class Manifest:
    bundle_format_major: int = BUNDLE_FORMAT_MAJOR
    tool_version: str = __version__
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    source_os: str = ""
    source_arch: str = ""
    files: list[ManifestFileEntry] = field(default_factory=list)
    schema_fingerprints: dict[str, str] = field(default_factory=dict)
    schema_adapters: dict[str, str] = field(default_factory=dict)
    scrub_reports: dict[str, Any] = field(default_factory=dict)
    sqlite_integrity: dict[str, str] = field(default_factory=dict)
    categories: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_format_major": self.bundle_format_major,
            "tool_version": self.tool_version,
            "created_at": self.created_at,
            "source_os": self.source_os,
            "source_arch": self.source_arch,
            "files": [{"path": f.path, "sha256": f.sha256, "size": f.size} for f in self.files],
            "schema_fingerprints": self.schema_fingerprints,
            "schema_adapters": self.schema_adapters,
            "scrub_reports": self.scrub_reports,
            "sqlite_integrity": self.sqlite_integrity,
            "categories": self.categories,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Manifest:
        major = int(data.get("bundle_format_major", 0))
        if major != BUNDLE_FORMAT_MAJOR:
            raise ValueError(f"unsupported bundle major version: {major}")
        m = Manifest(
            bundle_format_major=major,
            tool_version=str(data.get("tool_version", "")),
            created_at=str(data.get("created_at", "")),
            source_os=str(data.get("source_os", "")),
            source_arch=str(data.get("source_arch", "")),
            schema_fingerprints=dict(data.get("schema_fingerprints", {})),
            schema_adapters=dict(data.get("schema_adapters", {})),
            scrub_reports=dict(data.get("scrub_reports", {})),
            sqlite_integrity=dict(data.get("sqlite_integrity", {})),
            categories=dict(data.get("categories", {})),
        )
        seen: set[str] = set()
        for fe in data.get("files", []):
            path = str(fe["path"])
            # Manifest paths are untrusted and later joined onto the bundle root.
            validate_archive_member_name(path)
            if path in seen:
                raise ValueError(f"duplicate manifest path: {path}")
            seen.add(path)
            m.files.append(
                ManifestFileEntry(
                    path=path,
                    sha256=str(fe["sha256"]),
                    size=int(fe["size"]),
                )
            )
        return m


def write_manifest(path: Path, manifest: Manifest) -> None:
    path.write_text(dumps_canonical(manifest.to_dict()) + "\n", encoding="utf-8")


def parse_manifest(text: str) -> Manifest:
    try:
        data = loads_strict(text, max_size=MAX_MANIFEST_BYTES)
        if not isinstance(data, dict):
            raise ValueError("manifest must be object")
        return Manifest.from_dict(data)
    except (StrictJsonError, ValueError, KeyError, TypeError) as exc:
        raise AppError(ErrorCode.BACKUP_INVALID, f"invalid manifest: {exc}") from exc


def read_manifest(path: Path) -> Manifest:
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise AppError(ErrorCode.BACKUP_INVALID, "manifest too large")
    return parse_manifest(path.read_text(encoding="utf-8"))
