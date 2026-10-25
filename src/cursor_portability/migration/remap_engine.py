from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cursor_portability.errors import AppError, ErrorCode
from cursor_portability.migration.engine import (
    ApplyOutcome,
    _error_summary,
    _journal_best_effort,
)
from cursor_portability.migration.journal import acquire_lock, append_journal, release_lock
from cursor_portability.planning.plan import read_plan
from cursor_portability.planning.remap import REMAP_PLAN_FORMAT
from cursor_portability.planning.workspace_id import (
    is_folder_workspace_id,
    workspace_id_for_folder,
)
from cursor_portability.platform.process_probe import effective_running, probe_cursor_running
from cursor_portability.remap.sites import DbUpdatePlan, WorkspaceIdUpdate, apply_db_updates
from cursor_portability.security.json_strict import dumps_canonical, loads_strict
from cursor_portability.security.tripwire import allow_profile_writes
from cursor_portability.storage.diskspace import ensure_space
from cursor_portability.storage.fs_safe import new_run_id
from cursor_portability.storage.hashing import sha256_file
from cursor_portability.storage.paths import StagedPath
from cursor_portability.storage.sqlite_conn import open_readonly_db, open_staged_writable
from cursor_portability.storage.sqlite_snapshot import cold_copy_sqlite_set, settle_staged_copy

# A hot -journal left beside a replaced main file would be rolled back into it on next open.
_GLOBAL_SIDECARS = ("-wal", "-shm", "-journal")


@dataclass(frozen=True)
class RemapApplyReport:
    run_id: str
    run_dir: str
    outcome: ApplyOutcome
    operations: int


def _sidecar(db: Path, suffix: str) -> Path:
    return Path(str(db) + suffix)


def _global_db_set(global_db: Path) -> list[Path]:
    return [global_db, *(_sidecar(global_db, s) for s in _GLOBAL_SIDECARS)]


def _read_remap_body(plan_path: Path) -> dict[str, Any]:
    plan = read_plan(plan_path)
    body = plan.body
    if body.get("remap_plan_format") != REMAP_PLAN_FORMAT:
        raise AppError(ErrorCode.PLAN_INVALID, "not a remap plan")
    return body


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_update(upd: object) -> bool:
    return (
        isinstance(upd, dict)
        and is_folder_workspace_id(upd.get("from_id"))
        and is_folder_workspace_id(upd.get("to_id"))
        and _is_count(upd.get("expected_rows"))
        and _is_count(upd.get("existing_rows"))
    )


def _valid_workspace_copy(op: dict[str, Any]) -> bool:
    merged = op.get("merged_ubuntu_ids", [])
    return (
        is_folder_workspace_id(op.get("ubuntu_id"))
        and is_folder_workspace_id(op.get("mac_id"))
        and isinstance(merged, list)
        and all(is_folder_workspace_id(m) for m in merged)
        and isinstance(op.get("dest_folder_uri"), str)
    )


def _remap_operations(body: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Workspace copy ops and the single db_update op; every id becomes a path component."""
    ops = body.get("operations")
    if not isinstance(ops, list):
        raise AppError(ErrorCode.PLAN_INVALID, "invalid operations")
    ws_ops: list[dict[str, Any]] = []
    db_ops: list[dict[str, Any]] = []
    for op in ops:
        kind = op.get("kind") if isinstance(op, dict) else None
        if kind == "workspace_copy" and _valid_workspace_copy(op):
            ws_ops.append(op)
        elif kind == "db_update" and isinstance(op.get("updates"), list):
            if not all(_valid_update(u) for u in op["updates"]):
                raise AppError(ErrorCode.PLAN_INVALID, "invalid db_update entry")
            db_ops.append(op)
        else:
            raise AppError(ErrorCode.PLAN_INVALID, "invalid remap operation", {"kind": kind})
    if len(db_ops) != 1:
        raise AppError(ErrorCode.PLAN_INVALID, "remap plan needs exactly one db_update")
    mac_ids = [op["mac_id"] for op in ws_ops]
    from_ids = [u["from_id"] for u in db_ops[0]["updates"]]
    if len(set(mac_ids)) != len(mac_ids) or len(set(from_ids)) != len(from_ids):
        raise AppError(ErrorCode.PLAN_INVALID, "remap plan repeats a workspace id")
    return ws_ops, db_ops[0]


def _fingerprint_matches(global_db: Path, fp: dict[str, Any]) -> bool:
    live = _live_fingerprint(global_db)
    return live == fp


def _live_fingerprint(global_db: Path) -> dict[str, Any]:
    wal = Path(str(global_db) + "-wal")
    shm = Path(str(global_db) + "-shm")
    main = {
        "exists": global_db.is_file(),
        "size": global_db.stat().st_size if global_db.is_file() else 0,
        "mtime_ns": global_db.stat().st_mtime_ns if global_db.is_file() else 0,
    }
    wal_part: dict[str, Any] = {"exists": wal.is_file()}
    if wal.is_file():
        wal_part["sha256"] = sha256_file(wal)
    wal_part["shm_exists"] = shm.is_file()
    return {"main": main, "wal": wal_part}


def _same_volume(a: Path, b: Path) -> bool:
    def anchor(p: Path) -> Path:
        cur = p
        while not cur.exists() and cur.parent != cur:
            cur = cur.parent
        return cur

    try:
        return os.stat(anchor(a)).st_dev == os.stat(anchor(b)).st_dev
    except OSError:
        return False


def apply_remap_plan(
    plan_path: Path,
    runs_dir: Path,
    confirm: bool,
) -> RemapApplyReport:
    body = _read_remap_body(plan_path)
    ws_ops, db_op = _remap_operations(body)
    dest_ud = Path(str(body["target_user_data_dir"]))
    dest_user = dest_ud / "User"
    global_db = dest_user / "globalStorage" / "state.vscdb"
    state = probe_cursor_running(dest_ud)
    if effective_running(state):
        raise AppError(ErrorCode.CURSOR_RUNNING, "close Cursor before remap apply")

    fp = body.get("global_db_fingerprint")
    if not isinstance(fp, dict) or not _fingerprint_matches(global_db, fp):
        raise AppError(ErrorCode.TARGET_CHANGED, "global database changed since plan")

    if not ensure_space(dest_ud, global_db.stat().st_size * 2):
        raise AppError(ErrorCode.INSUFFICIENT_DISK_SPACE, "not enough disk space for remap")

    if not _same_volume(dest_ud, runs_dir):
        raise AppError(ErrorCode.PLAN_INVALID, "runs-dir must be on the same volume as the profile")

    if not confirm:
        raise AppError(ErrorCode.OUTPUT_EXISTS, "remap apply not confirmed")

    run_id = new_run_id()
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, mode=0o700)
    acquire_lock(run_dir)
    shutil.copy2(plan_path, run_dir / "plan.json")
    snapshot = run_dir / "snapshot"
    snapshot.mkdir()
    append_journal(run_dir, {"event": "remap_started", "run_id": run_id})

    ws_root = dest_user / "workspaceStorage"
    write_targets = [*_global_db_set(global_db), *(ws_root / op["mac_id"] for op in ws_ops)]

    preimages: list[tuple[Path, Path | None]] = []
    global_snap = snapshot / "globalStorage_state.vscdb"
    for src in _global_db_set(global_db):
        if src.is_file():
            shutil.copy2(src, snapshot / f"globalStorage_{src.name}")
    if global_snap.is_file():
        preimages.append((global_db, global_snap))
    for op in ws_ops:
        mac_id = op["mac_id"]
        target_dir = ws_root / mac_id
        if target_dir.is_dir() and op.get("replace_existing"):
            snap_dir = snapshot / f"ws_{mac_id}"
            shutil.copytree(target_dir, snap_dir)
            preimages.append((target_dir, snap_dir))
        else:
            preimages.append((target_dir, None))

    append_journal(run_dir, {"event": "snapshot_done"})

    old_prefix = str(body["path_mapping"]["old"])
    new_prefix = str(body["path_mapping"]["new"])
    source_os = str(body.get("source_os", "linux"))

    touched: set[Path] = set()
    try:
        with (
            allow_profile_writes(dest_ud, write_targets),
            tempfile.TemporaryDirectory(prefix="cursor-port-remap-stage-") as tmp,
        ):
            staged_global = cold_copy_sqlite_set(global_db, Path(tmp) / "global")
            updates = tuple(
                WorkspaceIdUpdate(u["from_id"], u["to_id"], u["expected_rows"], u["existing_rows"])
                for u in db_op["updates"]
            )
            db_plan = DbUpdatePlan(
                str(db_op.get("adapter", "")),
                updates,
                bool(db_op.get("recent_paths_rewrite")),
            )
            with open_staged_writable(StagedPath(staged_global.main_db)) as conn:
                apply_db_updates(conn, db_plan, old_prefix, new_prefix, source_os)
                row = conn.execute("PRAGMA integrity_check").fetchone()
                if row is None or row[0] != "ok":
                    raise AppError(
                        ErrorCode.SQLITE_INTEGRITY_FAILED, "staged global db failed integrity"
                    )
                conn.commit()
            settle_staged_copy(staged_global.main_db)
            # Before each publish: an interrupted publish may already have changed the target.
            touched.add(global_db)
            _publish_global_db(global_db, staged_global.main_db, run_dir)
            for op in ws_ops:
                touched.add(ws_root / op["mac_id"])
                _publish_workspace_copy(op, ws_root, run_dir)
        operations = len(ws_ops) + 1
        report = {
            "outcome": ApplyOutcome.APPLIED.value,
            "operations": operations,
            "global_db_fingerprint_post": _live_fingerprint(global_db),
        }
        (run_dir / "report.json").write_text(dumps_canonical(report) + "\n", encoding="utf-8")
        append_journal(run_dir, {"event": "remap_completed"})
        release_lock(run_dir)
        return RemapApplyReport(run_id, str(run_dir), ApplyOutcome.APPLIED, operations)
    except Exception as exc:
        cause = _error_summary(exc)
        with allow_profile_writes(dest_ud, write_targets):
            not_restored = _restore_remap(
                [(target, snap) for target, snap in preimages if target in touched], global_db
            )
        _journal_best_effort(
            run_dir,
            {"event": "remap_failed", "cause": cause, "not_restored": not_restored},
        )
        details: dict[str, object] = {"cause": cause, "run_dir": str(run_dir)}
        if not_restored:
            details["not_restored"] = not_restored
            raise AppError(
                ErrorCode.APPLY_FAILED_ROLLBACK_INCOMPLETE,
                f"remap failed ({cause['code']}: {cause['message']})",
                details,
            ) from exc
        _journal_best_effort(run_dir, {"event": "rollback_completed"})
        release_lock(run_dir)
        raise AppError(
            ErrorCode.APPLY_FAILED_ROLLBACK_SUCCEEDED,
            f"remap failed ({cause['code']}: {cause['message']}); restored from snapshot",
            details,
        ) from exc


def _publish_global_db(live: Path, staged: Path, run_dir: Path) -> None:
    append_journal(run_dir, {"event": "publish_global_begin"})
    for suffix in _GLOBAL_SIDECARS:
        side = _sidecar(live, suffix)
        if side.is_file():
            side.unlink()
    os.replace(staged, live)
    append_journal(run_dir, {"event": "publish_global_done"})


def _publish_workspace_copy(op: dict[str, Any], ws_root: Path, run_dir: Path) -> None:
    ubuntu_id = op["ubuntu_id"]
    mac_id = op["mac_id"]
    dest_uri = op["dest_folder_uri"]
    src_dir = ws_root / ubuntu_id
    if not src_dir.is_dir():
        raise AppError(ErrorCode.SOURCE_CHANGED, f"missing ubuntu workspace {ubuntu_id}")
    dest_dir = ws_root / mac_id
    if dest_dir.exists() and op.get("replace_existing"):
        shutil.rmtree(dest_dir)
    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cursor-port-ws-copy-") as tmp:
        staging = Path(tmp) / "ws"
        shutil.copytree(src_dir, staging)
        wj = staging / "workspace.json"
        data = loads_strict(wj.read_text(encoding="utf-8"), max_size=65536)
        if not isinstance(data, dict):
            raise AppError(ErrorCode.PLAN_INVALID, "workspace.json must be object")
        data["folder"] = dest_uri
        wj.write_text(dumps_canonical(data) + "\n", encoding="utf-8")
        ws_db = staging / "state.vscdb"
        if ws_db.is_file():
            settle_staged_copy(ws_db)
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        shutil.move(str(staging), str(dest_dir))
    append_journal(run_dir, {"event": "workspace_copy_done", "mac_id": mac_id})


def _restore_global_db(live: Path, snap: Path) -> bool:
    shutil.copy2(snap, live)
    ok = sha256_file(live) == sha256_file(snap)
    for suffix in _GLOBAL_SIDECARS:
        s_snap = _sidecar(snap, suffix)
        s_live = _sidecar(live, suffix)
        if s_snap.is_file():
            shutil.copy2(s_snap, s_live)
            ok = ok and sha256_file(s_live) == sha256_file(s_snap)
        else:
            s_live.unlink(missing_ok=True)
            ok = ok and not s_live.exists()
    return ok


def _restore_remap(preimages: list[tuple[Path, Path | None]], global_db: Path) -> list[str]:
    not_restored: list[str] = []
    for target, snap in reversed(preimages):
        try:
            if snap is not None and snap.is_dir():
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(snap, target)
                ok = target.is_dir()
            elif snap is not None and target == global_db:
                ok = _restore_global_db(target, snap)
            elif snap is None:
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                    ok = not target.exists()
                elif target.is_file() or target.is_symlink():
                    target.unlink(missing_ok=True)
                    ok = not target.exists()
                else:
                    ok = True
            elif snap is not None:
                shutil.copy2(snap, target)
                ok = sha256_file(target) == sha256_file(snap)
            else:
                ok = False
        except (OSError, RuntimeError):
            ok = False
        if not ok:
            not_restored.append(str(target))
    return not_restored


def _composer_rows(conn: sqlite3.Connection, workspace_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM composerHeaders WHERE workspaceId = ?", (workspace_id,)
    ).fetchone()
    return int(row[0])


def verify_remap_run(run_dir: Path, dest_user: Path) -> dict[str, Any]:
    body = _read_remap_body(run_dir / "plan.json")
    ws_ops, db_op = _remap_operations(body)
    errors: list[str] = []
    ws_root = dest_user / "workspaceStorage"
    global_db = dest_user / "globalStorage" / "state.vscdb"
    updates: list[dict[str, Any]] = db_op["updates"]
    expected_mac_rows: dict[str, int] = {}
    for upd in updates:
        expected_mac_rows.setdefault(upd["to_id"], upd["existing_rows"])
        expected_mac_rows[upd["to_id"]] += upd["expected_rows"]
    with tempfile.TemporaryDirectory(prefix="cursor-port-remap-verify-") as tmp:
        staged = cold_copy_sqlite_set(global_db, Path(tmp) / "global")
        with open_readonly_db(StagedPath(staged.main_db)) as conn:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            if row is None or row[0] != "ok":
                errors.append("global integrity_check failed")
            for upd in updates:
                remaining = _composer_rows(conn, upd["from_id"])
                if remaining:
                    errors.append(f"ubuntu id {upd['from_id']} still has {remaining} rows")
            for mac_id, expected in expected_mac_rows.items():
                mac_rows = _composer_rows(conn, mac_id)
                if mac_rows != expected:
                    errors.append(f"mac id {mac_id} has {mac_rows} rows, expected {expected}")
    for op in ws_ops:
        mac_id = op["mac_id"]
        dest_path = op.get("dest_path")
        wj = ws_root / mac_id / "workspace.json"
        if not wj.is_file():
            errors.append(f"missing workspace.json for {mac_id}")
            continue
        data = loads_strict(wj.read_text(encoding="utf-8"), max_size=65536)
        if isinstance(data, dict) and data.get("folder") != op.get("dest_folder_uri"):
            errors.append(f"folder uri mismatch for {mac_id}")
        if dest_path:
            try:
                if workspace_id_for_folder(Path(dest_path)) != mac_id:
                    errors.append(f"workspace id mismatch for {mac_id}")
            except AppError:
                errors.append(f"cannot verify folder id for {mac_id}")
    return {"ok": not errors, "errors": errors}


def rollback_remap_run(run_dir: Path, dest_user: Path, confirm: bool) -> None:
    if not confirm:
        raise AppError(ErrorCode.OUTPUT_EXISTS, "rollback not confirmed")
    body = _read_remap_body(run_dir / "plan.json")
    ws_ops, _ = _remap_operations(body)
    global_db = dest_user / "globalStorage" / "state.vscdb"
    report_path = run_dir / "report.json"
    if not report_path.is_file():
        raise AppError(ErrorCode.RUN_INCOMPLETE, "missing remap report")
    report = loads_strict(report_path.read_text(encoding="utf-8"), max_size=65536)
    expected_post = report.get("global_db_fingerprint_post") if isinstance(report, dict) else None
    live_fp = _live_fingerprint(global_db)
    if expected_post != live_fp:
        raise AppError(
            ErrorCode.TARGET_CHANGED, "global database changed since remap; refusing rollback"
        )
    snapshot = run_dir / "snapshot"
    main_snap = snapshot / "globalStorage_state.vscdb"
    if not main_snap.is_file():
        raise AppError(ErrorCode.RUN_INCOMPLETE, "snapshot is missing the global database")
    pairs: list[tuple[Path, Path | None]] = [(global_db, main_snap)]
    ws_root = dest_user / "workspaceStorage"
    for op in ws_ops:
        mac_id = op["mac_id"]
        target = ws_root / mac_id
        snap = snapshot / f"ws_{mac_id}"
        if snap.is_dir():
            pairs.append((target, snap))
        elif target.is_dir() and not op.get("replace_existing"):
            pairs.append((target, None))
    restore_targets = [*_global_db_set(global_db), *(target for target, _ in pairs)]
    with allow_profile_writes(dest_user.parent, restore_targets):
        not_restored = _restore_remap(pairs, global_db)
    if not_restored:
        append_journal(run_dir, {"event": "rollback_incomplete", "not_restored": not_restored})
        raise AppError(
            ErrorCode.RUN_INCOMPLETE,
            "remap rollback incomplete",
            {"not_restored": not_restored},
        )
    append_journal(run_dir, {"event": "rollback_completed"})
