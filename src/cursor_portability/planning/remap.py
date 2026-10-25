from __future__ import annotations

import hashlib
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from cursor_portability.errors import AppError, ErrorCode
from cursor_portability.models.enums import DetectionStatus
from cursor_portability.planning.pathmap import (
    PathMapping,
    WorkspaceMapStatus,
    build_workspace_mappings,
)
from cursor_portability.planning.plan import read_plan, wrap_plan, write_plan
from cursor_portability.planning.uri import parse_workspace_uri, path_to_file_uri
from cursor_portability.planning.workspace_id import (
    is_folder_workspace_id,
    workspace_id_for_folder,
)
from cursor_portability.remap.json_links import ChatLinkMismatch, FolderLink, rewrite_links
from cursor_portability.remap.sites import (
    RECENT_PATHS_KEY,
    plan_db_updates,
    recent_paths_key_present,
)
from cursor_portability.schemas.registry import select_adapter
from cursor_portability.services.census import run_workspace_census
from cursor_portability.storage.hashing import sha256_file
from cursor_portability.storage.paths import StagedPath
from cursor_portability.storage.sqlite_conn import open_readonly_db
from cursor_portability.storage.sqlite_snapshot import cold_copy_sqlite_set
from cursor_portability.workspace.census_scan import scan_sqlite_file
from cursor_portability.workspace.discover import collect_profile_workspaces

REMAP_PLAN_FORMAT = 2


class CollisionPolicy(StrEnum):
    SKIP = "skip"
    REPLACE = "replace"
    LINK_CHATS = "link_chats"


@dataclass(frozen=True)
class RemapPlanCreateReport:
    plan_path: str
    plan_id: str
    workspace_operations: int
    db_updates: int
    link_rewrites: dict[str, Any]


def _global_db_fingerprint(global_db: Path) -> dict[str, Any]:
    wal = Path(str(global_db) + "-wal")
    shm = Path(str(global_db) + "-shm")
    main_fp = {
        "exists": global_db.is_file(),
        "size": global_db.stat().st_size if global_db.is_file() else 0,
        "mtime_ns": global_db.stat().st_mtime_ns if global_db.is_file() else 0,
    }
    wal_fp: dict[str, Any] = {"exists": wal.is_file()}
    if wal.is_file():
        wal_fp["sha256"] = sha256_file(wal)
    wal_fp["shm_exists"] = shm.is_file()
    return {"main": main_fp, "wal": wal_fp}


def _workspace_recency(ws_dir: Path) -> int:
    """Latest mtime among a workspace folder's state files; 0 when none can be read."""
    times: list[int] = []
    for name in ("state.vscdb", "state.vscdb-wal", "workspace.json"):
        try:
            times.append((ws_dir / name).stat().st_mtime_ns)
        except OSError:
            continue
    return max(times, default=0)


def _policy_for_project(
    project_name: str,
    mac_exists: bool,
    replace: set[str],
    link_chats: set[str],
) -> CollisionPolicy:
    if project_name in replace:
        return CollisionPolicy.REPLACE
    if project_name in link_chats:
        return CollisionPolicy.LINK_CHATS
    if mac_exists:
        return CollisionPolicy.SKIP
    return CollisionPolicy.REPLACE


def create_remap_plan(
    user_data_dir: Path,
    old_prefix: str,
    new_prefix: str,
    output: Path,
    replace_projects: set[str],
    link_chats_projects: set[str],
    source_os: str = "linux",
    dest_os: str = "darwin",
    skip_duplicates: bool = False,
) -> RemapPlanCreateReport:
    if link_chats_projects & replace_projects:
        raise AppError(ErrorCode.PLAN_INVALID, "project cannot be both --replace and --link-chats")
    census = run_workspace_census(
        user_data_dir, old_prefix, new_prefix, source_os, dest_os, field_report=False
    )
    if not census.body.get("link_chats_eligible") and link_chats_projects:
        raise AppError(
            ErrorCode.PLAN_INVALID,
            "workspace databases contain chat-like keys; --link-chats not offered",
        )
    mappings = [PathMapping(old_prefix.rstrip("/"), new_prefix.rstrip("/"))]
    workspaces = collect_profile_workspaces(user_data_dir)
    ws_results = build_workspace_mappings(workspaces, mappings, source_os, dest_os)
    ws_root = user_data_dir / "User" / "workspaceStorage"
    global_db = user_data_dir / "User" / "globalStorage" / "state.vscdb"
    if not global_db.is_file():
        raise AppError(ErrorCode.PROFILE_NOT_FOUND, "global state.vscdb missing")

    workspace_ops: list[dict[str, Any]] = []
    id_pairs: list[tuple[str, str]] = []
    folder_links: list[FolderLink] = []
    warnings: list[str] = []

    groups: dict[str, list[tuple[str, str, str, str]]] = {}
    dest_uris: dict[str, str] = {}
    for r in ws_results:
        if r.status not in (WorkspaceMapStatus.MAPPED, WorkspaceMapStatus.DESTINATION_MISSING):
            continue
        if not r.source_path or not r.dest_path:
            continue
        project = Path(r.source_path).name
        if not is_folder_workspace_id(r.workspace_id):
            warnings.append(f"skip {project}: workspace folder name is not a workspace id")
            continue
        if not Path(r.dest_path).is_dir():
            warnings.append(f"skip {project}: destination folder missing")
            continue
        dest_uri = path_to_file_uri(Path(r.dest_path), dest_os)
        if parse_workspace_uri(dest_uri).native_path != r.dest_path:
            warnings.append(f"skip {project}: destination path is not canonical (symlink?)")
            continue
        mac_id = workspace_id_for_folder(Path(r.dest_path))
        dest_uris[mac_id] = dest_uri
        groups.setdefault(mac_id, []).append(
            (r.workspace_id, r.source_path, r.dest_path, r.source_uri)
        )

    for mac_id in sorted(groups):
        group = sorted(groups[mac_id])
        project = Path(group[0][1]).name
        if len(group) > 1 and skip_duplicates:
            warnings.append(
                f"skip {project}: {len(group)} Ubuntu workspaces map to mac workspace {mac_id}"
            )
            continue
        mac_exists = (ws_root / mac_id).is_dir()
        policy = _policy_for_project(project, mac_exists, replace_projects, link_chats_projects)
        if policy == CollisionPolicy.SKIP:
            warnings.append(f"skip {project}: mac workspace folder {mac_id} already exists")
            continue
        id_pairs.extend((ubuntu_id, mac_id) for ubuntu_id, *_ in group)
        folder_links.extend(
            FolderLink(ubuntu_id, mac_id, source_path, dest_path, source_uri, dest_uris[mac_id])
            for ubuntu_id, source_path, dest_path, source_uri in group
        )
        if policy == CollisionPolicy.LINK_CHATS:
            continue
        newest = max(group, key=lambda g: (_workspace_recency(ws_root / g[0]), g[0]))
        ubuntu_id, source_path, dest_path, _ = newest
        merged = [other[0] for other in group if other[0] != ubuntu_id]
        if merged:
            warnings.append(
                f"merge {project}: {len(group)} Ubuntu workspaces; copying the most recently "
                "modified one and linking chats from all of them"
            )
        workspace_ops.append(
            {
                "op_id": f"ws-{len(workspace_ops) + 1}",
                "kind": "workspace_copy",
                "ubuntu_id": ubuntu_id,
                "merged_ubuntu_ids": merged,
                "mac_id": mac_id,
                "dest_folder_uri": dest_uris[mac_id],
                "replace_existing": policy == CollisionPolicy.REPLACE and mac_exists,
                "source_path": source_path,
                "dest_path": dest_path,
            }
        )

    with tempfile.TemporaryDirectory(prefix="cursor-port-remap-plan-") as tmp:
        staged = cold_copy_sqlite_set(global_db, Path(tmp) / "global")
        with open_readonly_db(StagedPath(staged.main_db)) as conn:
            det, _ = select_adapter(conn)
            if det.status != DetectionStatus.PASS:
                raise AppError(ErrorCode.UNKNOWN_SCHEMA, "global database schema not supported")
            adapter_label = f"{det.adapter_id}@{det.adapter_version}"
            include_recent = recent_paths_key_present(conn, old_prefix, source_os)
            try:
                link_counts = rewrite_links(conn, tuple(folder_links), write=False)
            except ChatLinkMismatch as exc:
                raise AppError(ErrorCode.PLAN_INVALID, f"cannot re-point chats: {exc}") from exc
        scan = scan_sqlite_file(staged.main_db, old_prefix, source_os)

    db_plan = plan_db_updates(
        scan.composer_workspace_counts,
        id_pairs,
        adapter_label,
        include_recent,
    )
    db_op = {
        "op_id": "db-1",
        "kind": "db_update",
        "adapter": db_plan.adapter_label,
        "updates": [
            {
                "from_id": u.from_id,
                "to_id": u.to_id,
                "expected_rows": u.expected_rows,
                "existing_rows": u.existing_rows,
            }
            for u in db_plan.workspace_id_updates
        ],
        "recent_paths_rewrite": db_plan.recent_paths_rewrite,
        "recent_paths_key": RECENT_PATHS_KEY if db_plan.recent_paths_rewrite else None,
        "folder_links": [asdict(link) for link in folder_links],
        "link_rewrites": link_counts.as_dict(),
    }

    body: dict[str, Any] = {
        "remap_plan_format": REMAP_PLAN_FORMAT,
        "source_os": source_os,
        "dest_os": dest_os,
        "target_user_data_dir": str(user_data_dir),
        "path_mapping": {"old": old_prefix, "new": new_prefix},
        "global_db_fingerprint": _global_db_fingerprint(global_db),
        "operations": [*workspace_ops, db_op],
        "warnings": warnings,
        "census_summary": {
            "link_chats_eligible": census.body.get("link_chats_eligible"),
            "mapped_count": census.body.get("mapped_count"),
        },
    }
    plan = wrap_plan(body)
    write_plan(output, plan)
    return RemapPlanCreateReport(
        str(output),
        plan.plan_id,
        len(workspace_ops),
        len(db_plan.workspace_id_updates),
        link_counts.as_dict(),
    )


def parse_remap_plan(path: Path) -> dict[str, Any]:
    plan = read_plan(path)
    body = plan.body
    if body.get("remap_plan_format") != REMAP_PLAN_FORMAT:
        raise AppError(ErrorCode.PLAN_INVALID, "not a remap plan")
    return body


def show_remap_plan(path: Path, shareable: bool = False) -> dict[str, Any]:
    plan = read_plan(path)
    body = dict(plan.body)
    if shareable:
        body.pop("target_user_data_dir", None)
        body["path_mapping"] = {"old": "<old prefix>", "new": "<new prefix>"}
        warnings = body.get("warnings")
        if isinstance(warnings, list):
            body["warnings"] = dict(Counter(str(w).split(" ", 1)[0] for w in warnings))
        body["operations"] = [
            _shareable_op(op) for op in body.get("operations", []) if isinstance(op, dict)
        ]
    return {"plan_id": plan.plan_id, "body": body}


def _short_id(workspace_id: object) -> str:
    return hashlib.sha256(str(workspace_id).encode()).hexdigest()[:12]


def _shareable_op(op: dict[str, Any]) -> dict[str, Any]:
    """Operation without paths; workspace ids hashed the same way as the shareable census."""
    out = {k: v for k, v in op.items() if k not in ("source_path", "dest_path", "dest_folder_uri")}
    for name in ("ubuntu_id", "mac_id"):
        if name in out:
            out[name] = _short_id(out[name])
    if isinstance(out.get("merged_ubuntu_ids"), list):
        out["merged_ubuntu_ids"] = [_short_id(i) for i in out["merged_ubuntu_ids"]]
    for name in ("updates", "folder_links"):
        if isinstance(out.get(name), list):
            out[name] = [
                {
                    **{k: v for k, v in entry.items() if k in ("expected_rows", "existing_rows")},
                    "from_id": _short_id(entry.get("from_id")),
                    "to_id": _short_id(entry.get("to_id")),
                }
                for entry in out[name]
                if isinstance(entry, dict)
            ]
    return out
