from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cursor_portability.discovery.classify import classify_path
from cursor_portability.discovery.locate import locate_profiles
from cursor_portability.discovery.walker import walk_root
from cursor_portability.platform.base import get_platform_paths
from cursor_portability.platform.env import Environment
from cursor_portability.schemas.fingerprint import compute_fingerprint
from cursor_portability.schemas.registry import select_adapter
from cursor_portability.security.json_strict import loads_strict
from cursor_portability.security.redaction import redact_path
from cursor_portability.storage.paths import StagedPath
from cursor_portability.storage.sqlite_conn import open_readonly_db
from cursor_portability.storage.sqlite_snapshot import cold_copy_sqlite_set


@dataclass(frozen=True)
class InspectReport:
    profile_path: str
    entries: tuple[dict[str, object], ...]
    databases: tuple[dict[str, object], ...]
    workspace_uris: tuple[str, ...]


def run_inspect(
    env: Environment,
    profile: Path | None,
    shareable: bool,
    emit_skeleton: Path | None,
) -> InspectReport:
    paths = get_platform_paths(env)
    candidates = locate_profiles(paths, profile)
    if not candidates:
        raise FileNotFoundError("profile not found")
    cand = candidates[0]
    ud = cand.user_data_dir
    ch = cand.cursor_home
    entries: list[dict[str, object]] = []
    for root, token in ((ud, "user-data"), (ch, "cursor-home") if ch else (None, "")):
        if root is None:
            continue
        for we in walk_root(root, token, max_depth=6):
            cls = classify_path(token, we.relative_path)
            entries.append(
                {
                    "path": redact_path(we.relative_path, env.home, shareable),
                    "category": cls.category.value,
                    "action": cls.action.value,
                    "size": we.size,
                    "symlink": we.is_symlink,
                    "skipped": we.skip_reason,
                }
            )
    databases: list[dict[str, object]] = []
    workspace_uris: list[str] = []
    ws_root = ud / "User" / "workspaceStorage"
    if ws_root.is_dir():
        for ws in ws_root.iterdir():
            wj = ws / "workspace.json"
            if wj.is_file() and wj.stat().st_size < 64 * 1024:
                try:
                    data = loads_strict(wj.read_text(encoding="utf-8"), max_size=65536)
                    if isinstance(data, dict):
                        folder = data.get("folder") or data.get("workspace")
                        if isinstance(folder, str):
                            workspace_uris.append(redact_path(folder, env.home, shareable))
                except ValueError:
                    pass
            db = ws / "state.vscdb"
            if db.is_file():
                databases.append(_inspect_db(db, shareable, env.home, emit_skeleton))
    gdb = ud / "User" / "globalStorage" / "state.vscdb"
    if gdb.is_file():
        databases.append(_inspect_db(gdb, shareable, env.home, emit_skeleton))

    if emit_skeleton:
        skeleton = {
            "databases": databases,
            "entry_count": len(entries),
        }
        emit_skeleton.write_text(json.dumps(skeleton, indent=2, sort_keys=True), encoding="utf-8")

    return InspectReport(
        redact_path(str(ud), env.home, shareable),
        tuple(entries[:500]),
        tuple(databases),
        tuple(workspace_uris),
    )


def _inspect_db(
    db_path: Path,
    shareable: bool,
    home: Path,
    emit_skeleton: Path | None,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="cursor-port-staging-") as tmp:
        snap = cold_copy_sqlite_set(db_path, Path(tmp))
        with open_readonly_db(StagedPath(snap.main_db)) as conn:
            fp = compute_fingerprint(conn)
            det, inv = select_adapter(conn)
            out: dict[str, object] = {
                "path": redact_path(str(db_path.name), home, shareable),
                "detection": det.status.value,
                "adapter": det.adapter_id,
                "adapter_version": det.adapter_version,
                "fingerprint": fp.digest,
                "message": det.message,
            }
            if inv:
                out["tables"] = [
                    {"name": t.name, "rows": t.row_count, "key_shapes": t.key_shapes}
                    for t in inv.tables
                ]
            return out
