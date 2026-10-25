from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cursor_portability.errors import AppError, ErrorCode
from cursor_portability.planning.pathmap import (
    PathMapping,
    WorkspaceMapStatus,
    build_workspace_mappings,
    resolve_path_for_source,
)
from cursor_portability.planning.uri import parse_workspace_uri
from cursor_portability.planning.workspace_id import workspace_id_for_folder
from cursor_portability.storage.sqlite_snapshot import cold_copy_sqlite_set
from cursor_portability.workspace.census_scan import (
    redact_path_hits,
    scan_sqlite_file,
    scan_storage_json_text,
)
from cursor_portability.workspace.discover import collect_profile_workspaces


@dataclass(frozen=True)
class CensusReport:
    body: dict[str, Any]


def _short_id(workspace_id: str) -> str:
    return hashlib.sha256(workspace_id.encode()).hexdigest()[:12]


def run_workspace_census(
    user_data_dir: Path,
    old_prefix: str,
    new_prefix: str,
    source_os: str = "linux",
    dest_os: str = "darwin",
    shareable: bool = False,
) -> CensusReport:
    if not user_data_dir.is_dir():
        raise AppError(ErrorCode.PROFILE_NOT_FOUND, "user-data directory not found")
    mappings = [PathMapping(old_prefix.rstrip("/"), new_prefix.rstrip("/"))]
    workspaces = collect_profile_workspaces(user_data_dir)
    ws_results = build_workspace_mappings(workspaces, mappings, source_os, dest_os)
    ws_root = user_data_dir / "User" / "workspaceStorage"
    global_db = user_data_dir / "User" / "globalStorage" / "state.vscdb"

    project_names: list[str] = []
    for r in ws_results:
        if r.source_path:
            project_names.append(Path(r.source_path).name)
    name_to_index = {name: i + 1 for i, name in enumerate(sorted(set(project_names)))}

    with tempfile.TemporaryDirectory(prefix="cursor-port-census-") as tmp:
        tmp_path = Path(tmp)
        global_scan = None
        if global_db.is_file():
            staged = cold_copy_sqlite_set(global_db, tmp_path / "global")
            global_scan = scan_sqlite_file(staged.main_db, old_prefix, source_os)

        storage_json_path = user_data_dir / "User" / "globalStorage" / "storage.json"
        storage_json_hits: dict[str, int] | None = None
        if storage_json_path.is_file():
            storage_json_hits = scan_storage_json_text(
                storage_json_path.read_text(encoding="utf-8", errors="replace"),
                old_prefix,
                source_os,
            )

        workspace_rows: list[dict[str, Any]] = []
        workspace_db_patterns: dict[str, list[str]] = {}
        any_workspace_chat_keys = False

        composer_by_ws = global_scan.composer_workspace_counts if global_scan else {}

        for r in ws_results:
            parsed = parse_workspace_uri(r.source_uri)
            mac_id: str | None = None
            mac_id_exists = False
            if r.dest_path and Path(r.dest_path).is_dir():
                try:
                    mac_id = workspace_id_for_folder(Path(r.dest_path))
                    mac_id_exists = (ws_root / mac_id).is_dir()
                except AppError:
                    mac_id = None
            ws_db = ws_root / r.workspace_id / "state.vscdb"
            ws_key_shapes: list[str] = []
            ws_chat = False
            if ws_db.is_file():
                staged_ws = cold_copy_sqlite_set(ws_db, tmp_path / f"ws-{r.workspace_id}")
                ws_scan = scan_sqlite_file(staged_ws.main_db, old_prefix, source_os)
                ws_key_shapes = list(ws_scan.item_key_shapes)
                ws_chat = ws_scan.chat_like_keys_present
                any_workspace_chat_keys = any_workspace_chat_keys or ws_chat
                workspace_db_patterns[_short_id(r.workspace_id)] = ws_key_shapes

            row: dict[str, Any] = {
                "workspace_id": _short_id(r.workspace_id) if shareable else r.workspace_id,
                "uri_kind": parsed.kind.value,
                "status": r.status.value,
                "composer_header_rows": composer_by_ws.get(r.workspace_id, 0),
                "mac_workspace_id": mac_id,
                "mac_workspace_folder_exists": mac_id_exists,
            }
            if shareable:
                if r.source_path:
                    row["project_index"] = name_to_index.get(Path(r.source_path).name)
            else:
                row["source_uri"] = r.source_uri
                row["source_path"] = r.source_path
                row["mapped_dest"] = r.dest_path
                row["dest_exists"] = bool(r.dest_path and Path(r.dest_path).is_dir())
                row["detail"] = r.detail
            workspace_rows.append(row)

        body: dict[str, Any] = {
            "census_format": 1,
            "source_os": source_os,
            "dest_os": dest_os,
            "path_mapping": {"old": mappings[0].old_path, "new": mappings[0].new_path},
            "workspaces": workspace_rows,
            "link_chats_eligible": not any_workspace_chat_keys,
            "workspace_db_key_shapes": workspace_db_patterns if shareable else {},
        }
        if global_scan:
            body["global_database"] = {
                "adapter": global_scan.adapter,
                "composer_workspace_counts": (
                    {_short_id(k): v for k, v in global_scan.composer_workspace_counts.items()}
                    if shareable
                    else global_scan.composer_workspace_counts
                ),
                "path_hits": redact_path_hits(global_scan.path_hits),
                "item_key_shapes": list(global_scan.item_key_shapes),
            }
        if storage_json_hits is not None:
            body["storage_json_hits"] = storage_json_hits

        if not shareable:
            body["workspaces_full_count"] = len(workspace_rows)
            mapped = [r for r in ws_results if r.status == WorkspaceMapStatus.MAPPED]
            body["mapped_count"] = len(mapped)
            body["outside_prefix"] = len(
                [
                    r
                    for r in ws_results
                    if r.status == WorkspaceMapStatus.MISSING
                    and r.source_path
                    and resolve_path_for_source(r.source_path, mappings, source_os, dest_os)[1]
                    == "PATH_MAPPING_MISSING"
                ]
            )

        return CensusReport(body)
