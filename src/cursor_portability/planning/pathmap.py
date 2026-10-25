from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath

from cursor_portability.errors import AppError, ErrorCode
from cursor_portability.planning.uri import (
    ParsedUri,
    UriKind,
    parse_workspace_uri,
    path_key_from_native,
    path_to_file_uri,
)
from cursor_portability.security.json_strict import loads_strict


class WorkspaceMapStatus(StrEnum):
    MAPPED = "mapped"
    MISSING = "missing"
    DESTINATION_MISSING = "destination_missing"
    UNSUPPORTED = "unsupported"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class PathMapping:
    old_path: str
    new_path: str


@dataclass(frozen=True)
class WorkspaceMappingResult:
    workspace_id: str
    source_uri: str
    source_path: str | None
    dest_path: str | None
    dest_uri: str | None
    status: WorkspaceMapStatus
    detail: str | None = None


def parse_path_map_pairs(pairs: list[str]) -> list[PathMapping]:
    out: list[PathMapping] = []
    for pair in pairs:
        if "=" not in pair:
            raise AppError(ErrorCode.PATH_MAPPING_AMBIGUOUS, f"invalid path-map: {pair}")
        old, new = pair.split("=", 1)
        out.append(PathMapping(old.strip(), new.strip()))
    return out


def load_path_map_file(path: Path) -> list[PathMapping]:
    data = loads_strict(path.read_text(encoding="utf-8"), max_size=512 * 1024)
    if not isinstance(data, dict):
        raise AppError(ErrorCode.PLAN_INVALID, "path-map-file must be a JSON object")
    return [PathMapping(str(k), str(v)) for k, v in data.items()]


def _mapping_keys(m: PathMapping, source_os: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return path_key_from_native(m.old_path, source_os), path_key_from_native(m.new_path, source_os)


def _pure_path(native: str, os_name: str) -> PurePosixPath | PureWindowsPath:
    if os_name == "win32":
        return PureWindowsPath(native)
    return PurePosixPath(native.replace("\\", "/"))


def resolve_path_for_source(
    native_path: str,
    mappings: list[PathMapping],
    source_os: str,
    dest_os: str,
) -> tuple[str | None, str | None]:
    """Return (dest_native_path, error_detail) using longest-prefix match on components."""
    src_key = path_key_from_native(native_path, source_os)
    best_len = -1
    best_map: PathMapping | None = None
    for m in mappings:
        old_k, _ = _mapping_keys(m, source_os)
        if len(old_k) > len(src_key) or src_key[: len(old_k)] != old_k:
            continue
        if len(old_k) > best_len:
            best_len = len(old_k)
            best_map = m
        elif len(old_k) == best_len and best_map is not None and m.new_path != best_map.new_path:
            return None, "PATH_MAPPING_AMBIGUOUS"
    if best_map is None:
        return None, "PATH_MAPPING_MISSING"
    src_p = _pure_path(native_path, source_os)
    old_p = _pure_path(best_map.old_path, source_os)
    try:
        rel = src_p.relative_to(old_p)
    except ValueError:
        return None, "PATH_MAPPING_MISSING"
    dest_base = _pure_path(best_map.new_path, dest_os)
    dest = dest_base / rel if str(rel) != "." else dest_base
    return str(dest), None


def detect_destination_collisions(
    results: list[WorkspaceMappingResult],
) -> list[tuple[str, str]]:
    """Two source workspaces mapping to the same dest path."""
    seen: dict[str, str] = {}
    collisions: list[tuple[str, str]] = []
    for r in results:
        if r.dest_path is None or r.status != WorkspaceMapStatus.MAPPED:
            continue
        key = r.dest_path.casefold() if r.dest_path else ""
        if key in seen:
            collisions.append((seen[key], r.workspace_id))
        else:
            seen[key] = r.workspace_id
    return collisions


def collect_bundle_workspaces(bundle_root: Path) -> list[tuple[str, str]]:
    """(workspace_id, folder_uri) from bundle user-data tree."""
    ws_root = bundle_root / "user-data" / "User" / "workspaceStorage"
    out: list[tuple[str, str]] = []
    if not ws_root.is_dir():
        return out
    for child in ws_root.iterdir():
        if not child.is_dir():
            continue
        wj = child / "workspace.json"
        if not wj.is_file():
            continue
        try:
            data = loads_strict(wj.read_text(encoding="utf-8"), max_size=65536)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        folder = data.get("folder") or data.get("workspace")
        if isinstance(folder, str):
            out.append((child.name, folder))
    return out


def build_workspace_mappings(
    bundle_workspaces: list[tuple[str, str]],
    mappings: list[PathMapping],
    source_os: str,
    dest_os: str,
    skipped_ids: set[str] | None = None,
) -> list[WorkspaceMappingResult]:
    skipped_ids = skipped_ids or set()
    results: list[WorkspaceMappingResult] = []
    for ws_id, uri in bundle_workspaces:
        if ws_id in skipped_ids:
            results.append(
                WorkspaceMappingResult(ws_id, uri, None, None, None, WorkspaceMapStatus.SKIPPED)
            )
            continue
        parsed: ParsedUri = parse_workspace_uri(uri)
        if parsed.kind != UriKind.FILE or not parsed.native_path:
            results.append(
                WorkspaceMappingResult(
                    ws_id,
                    uri,
                    None,
                    None,
                    None,
                    WorkspaceMapStatus.UNSUPPORTED,
                    parsed.message,
                )
            )
            continue
        dest, err = resolve_path_for_source(parsed.native_path, mappings, source_os, dest_os)
        if err == "PATH_MAPPING_MISSING":
            results.append(
                WorkspaceMappingResult(
                    ws_id,
                    uri,
                    parsed.native_path,
                    None,
                    None,
                    WorkspaceMapStatus.MISSING,
                    err,
                )
            )
            continue
        if err == "PATH_MAPPING_AMBIGUOUS":
            results.append(
                WorkspaceMappingResult(
                    ws_id,
                    uri,
                    parsed.native_path,
                    None,
                    None,
                    WorkspaceMapStatus.UNSUPPORTED,
                    err,
                )
            )
            continue
        if dest is None:
            results.append(
                WorkspaceMappingResult(
                    ws_id,
                    uri,
                    parsed.native_path,
                    None,
                    None,
                    WorkspaceMapStatus.MISSING,
                )
            )
            continue
        dest_path_obj = Path(dest)
        status = WorkspaceMapStatus.MAPPED
        detail = None
        if not dest_path_obj.is_dir():
            status = WorkspaceMapStatus.DESTINATION_MISSING
            detail = "destination folder does not exist"
        dest_uri = path_to_file_uri(dest_path_obj, dest_os)
        results.append(
            WorkspaceMappingResult(
                ws_id,
                uri,
                parsed.native_path,
                dest,
                dest_uri,
                status,
                detail,
            )
        )
    return results


def interactive_fill_mappings(
    missing: list[WorkspaceMappingResult],
    ask: Callable[[str], str],
) -> tuple[list[PathMapping], set[str]]:
    """ask(source_path) -> new path string or empty to skip. Returns extra maps and skipped ids."""
    extra: list[PathMapping] = []
    skipped: set[str] = set()
    for r in missing:
        if r.source_path is None:
            skipped.add(r.workspace_id)
            continue
        answer = ask(r.source_path)
        if not answer or not answer.strip():
            skipped.add(r.workspace_id)
            continue
        extra.append(PathMapping(r.source_path, answer.strip()))
    return extra, skipped
