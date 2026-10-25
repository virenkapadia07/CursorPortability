from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from cursor_portability.bundle.layout import MANIFEST_NAME
from cursor_portability.errors import AppError, ErrorCode
from cursor_portability.migration.journal import acquire_lock, append_journal, release_lock
from cursor_portability.migration.profile_writer import assert_allowed_target, write_text_atomic
from cursor_portability.planning.config_merge import merge_keybindings_text, merge_object_text
from cursor_portability.planning.jsonc import JsoncError, parse_jsonc
from cursor_portability.planning.plan import MigrationPlan, fingerprint_file, read_plan
from cursor_portability.planning.targets import is_allowed_target, source_member_for
from cursor_portability.platform.process_probe import effective_running, probe_cursor_running
from cursor_portability.security.json_strict import dumps_canonical
from cursor_portability.security.tripwire import allow_profile_writes
from cursor_portability.services.plan import _materialize_bundle
from cursor_portability.storage.fs_safe import new_run_id
from cursor_portability.storage.hashing import sha256_bytes, sha256_file


class ApplyOutcome(StrEnum):
    APPLIED = "applied"
    ALREADY_APPLIED = "already_applied"
    FAILED_ROLLBACK_OK = "failed_rollback_ok"
    FAILED_ROLLBACK_INCOMPLETE = "failed_rollback_incomplete"


@dataclass(frozen=True)
class ApplyReport:
    run_id: str
    run_dir: str
    outcome: ApplyOutcome
    operations: int


_OP_KINDS = ("create", "merge_object", "append_array")


def _plan_ops(plan: MigrationPlan) -> list[dict[str, Any]]:
    ops = plan.body.get("operations", [])
    if not isinstance(ops, list):
        raise AppError(ErrorCode.PLAN_INVALID, "invalid operations")
    seen: set[str] = set()
    for op in ops:
        if not isinstance(op, dict):
            raise AppError(ErrorCode.PLAN_INVALID, "operation must be an object")
        rel = op.get("target_rel")
        if not isinstance(rel, str):
            raise AppError(ErrorCode.PLAN_INVALID, "operation target must be a string")
        assert_allowed_target(rel)
        if rel in seen:
            raise AppError(ErrorCode.PLAN_INVALID, "duplicate operation target", {"path": rel})
        seen.add(rel)
        if op.get("source_member") != source_member_for(rel):
            raise AppError(ErrorCode.PLAN_INVALID, "operation source does not match its target")
        if op.get("kind") not in _OP_KINDS or not isinstance(op.get("post_sha256"), str):
            raise AppError(ErrorCode.PLAN_INVALID, "invalid operation", {"path": rel})
    return ops


def _target_path(dest_user: Path, rel: str) -> Path:
    return dest_user / rel.removeprefix("User/")


def _already_applied(ops: list[dict[str, Any]], dest_user: Path) -> bool:
    return bool(ops) and all(
        fingerprint_file(_target_path(dest_user, op["target_rel"])).get("sha256")
        == op["post_sha256"]
        for op in ops
    )


def _recompute_post_text(
    op: dict[str, Any],
    bundle_root: Path,
    dest_user: Path,
) -> str:
    rel = op["target_rel"]
    source_text: str = (bundle_root / op["source_member"]).read_text(encoding="utf-8")
    target = _target_path(dest_user, rel)
    kind = op["kind"]
    if kind == "create":
        return source_text
    if not target.is_file():
        raise AppError(ErrorCode.TARGET_CHANGED, f"missing destination file {rel}")
    digests = op.get("use_source_key_digests", [])
    if not isinstance(digests, list) or not all(isinstance(d, str) for d in digests):
        raise AppError(ErrorCode.PLAN_INVALID, "invalid use_source_key_digests")
    try:
        dest_doc = parse_jsonc(target.read_text(encoding="utf-8"))
        src_doc = parse_jsonc(source_text)
        if kind == "merge_object":
            if not isinstance(dest_doc.value, dict) or not isinstance(src_doc.value, dict):
                raise AppError(ErrorCode.PLAN_INVALID, "merge_object expects objects")
            return merge_object_text(dest_doc, src_doc.value, set(digests))[0]
        if kind == "append_array":
            if not isinstance(dest_doc.value, list) or not isinstance(src_doc.value, list):
                raise AppError(ErrorCode.PLAN_INVALID, "append_array expects arrays")
            return merge_keybindings_text(dest_doc, src_doc.value, set(digests))[0]
    except JsoncError as exc:
        raise AppError(ErrorCode.PLAN_INVALID, f"cannot merge {rel}: {exc}") from exc
    raise AppError(ErrorCode.PLAN_INVALID, f"unknown op kind {kind}")


def _check_target_fingerprint(plan: MigrationPlan, dest_user: Path) -> None:
    fp = plan.body.get("target_fingerprint", {})
    if not isinstance(fp, dict):
        return
    for rel, meta in fp.items():
        if rel == "User/snippets/_listing":
            continue
        if not is_allowed_target(rel):
            raise AppError(ErrorCode.PLAN_INVALID, "fingerprint path not on migration allowlist")
        if not isinstance(meta, dict):
            continue
        current = fingerprint_file(_target_path(dest_user, rel))
        if meta.get("exists") != current.get("exists"):
            raise AppError(ErrorCode.TARGET_CHANGED, f"fingerprint mismatch {rel}")
        if meta.get("exists") and meta.get("sha256") != current.get("sha256"):
            raise AppError(ErrorCode.TARGET_CHANGED, f"fingerprint mismatch {rel}")


def apply_plan(
    plan_path: Path,
    bundle_path: Path,
    runs_dir: Path,
    confirm: bool,
) -> ApplyReport:
    plan = read_plan(plan_path)
    if plan.body.get("required_acknowledgements"):
        raise AppError(ErrorCode.DESTINATION_CONFLICT, "plan has unresolved requirements")
    dest_ud = Path(str(plan.body["target_user_data_dir"]))
    dest_user = dest_ud / "User"
    state = probe_cursor_running(dest_ud)
    if effective_running(state):
        raise AppError(ErrorCode.CURSOR_RUNNING, "close Cursor on destination before apply")

    ops = _plan_ops(plan)
    for op in ops:
        if _target_path(dest_user, op["target_rel"]).is_symlink():
            raise AppError(
                ErrorCode.UNSAFE_PATH,
                "destination file is a symlink; refusing to apply",
                {"path": op["target_rel"]},
            )
    # Checked before the fingerprint, which no longer matches once the plan has been applied.
    if _already_applied(ops, dest_user):
        return ApplyReport("", "", ApplyOutcome.ALREADY_APPLIED, 0)
    _check_target_fingerprint(plan, dest_user)

    with tempfile.TemporaryDirectory(prefix="cursor-port-apply-") as tmp:
        bundle_root = _materialize_bundle(bundle_path, Path(tmp) / "bundle")
        manifest_path = bundle_root / MANIFEST_NAME
        if sha256_bytes(manifest_path.read_bytes()) != plan.body.get("bundle_manifest_sha256"):
            raise AppError(ErrorCode.SOURCE_CHANGED, "bundle manifest changed since plan")

        staged_posts: list[tuple[dict[str, Any], str]] = []
        for op in ops:
            text = _recompute_post_text(op, bundle_root, dest_user)
            if sha256_bytes(text.encode("utf-8")) != op["post_sha256"]:
                raise AppError(ErrorCode.PLAN_INVALID, "staged content hash mismatch")
            staged_posts.append((op, text))

        if not confirm:
            raise AppError(ErrorCode.OUTPUT_EXISTS, "apply not confirmed")

        run_id = new_run_id()
        run_dir = runs_dir / run_id
        run_dir.mkdir(parents=True, mode=0o700)
        acquire_lock(run_dir)
        shutil.copy2(plan_path, run_dir / "plan.json")
        snapshot = run_dir / "snapshot"
        snapshot.mkdir()
        append_journal(run_dir, {"event": "run_started", "run_id": run_id})

        preimages: list[tuple[Path, Path | None]] = []
        for op, _ in staged_posts:
            rel = op["target_rel"]
            target = _target_path(dest_user, rel)
            snap_path = snapshot / rel.replace("/", "_")
            if target.is_file():
                shutil.copy2(target, snap_path)
                preimages.append((target, snap_path))
            else:
                preimages.append((target, None))
        append_journal(run_dir, {"event": "snapshot_done"})

        with allow_profile_writes(dest_ud, [target for target, _ in preimages]):
            _commit_run(run_dir, plan, staged_posts, preimages)
        return ApplyReport(run_id, str(run_dir), ApplyOutcome.APPLIED, len(staged_posts))


def _commit_run(
    run_dir: Path,
    plan: MigrationPlan,
    staged_posts: list[tuple[dict[str, Any], str]],
    preimages: list[tuple[Path, Path | None]],
) -> None:
    committed: list[tuple[Path, Path | None]] = []
    try:
        for (op, text), (target, snap) in zip(staged_posts, preimages, strict=True):
            append_journal(run_dir, {"event": "op_begin", "op_id": op["op_id"]})
            # Before the write: an interrupted write may already have replaced the file.
            committed.append((target, snap))
            write_text_atomic(target, text)
            append_journal(run_dir, {"event": "op_done", "op_id": op["op_id"]})
        for (op, _), (target, _) in zip(staged_posts, preimages, strict=True):
            if sha256_file(target) != op["post_sha256"]:
                raise AppError(ErrorCode.TARGET_CHANGED, "post-apply hash mismatch")
        ext = plan.body.get("extensions_to_install", [])
        if isinstance(ext, list):
            (run_dir / "extensions-to-install.txt").write_text(
                "\n".join(str(x) for x in ext) + "\n", encoding="utf-8"
            )
        report = {"outcome": ApplyOutcome.APPLIED.value, "operations": len(staged_posts)}
        (run_dir / "report.json").write_text(dumps_canonical(report) + "\n", encoding="utf-8")
        append_journal(run_dir, {"event": "run_completed"})
        release_lock(run_dir)
    except Exception as exc:
        cause = _error_summary(exc)
        not_restored = _restore_files(committed)
        _journal_best_effort(
            run_dir,
            {"event": "apply_failed", "cause": cause, "not_restored": not_restored},
        )
        details: dict[str, object] = {"cause": cause, "run_dir": str(run_dir)}
        if not_restored:
            # The lock stays so the run is visibly incomplete; the snapshot remains in run_dir.
            details["not_restored"] = not_restored
            raise AppError(
                ErrorCode.APPLY_FAILED_ROLLBACK_INCOMPLETE,
                f"apply failed ({cause['code']}: {cause['message']}); "
                f"{len(not_restored)} file(s) could not be restored from the snapshot",
                details,
            ) from exc
        _journal_best_effort(run_dir, {"event": "rollback_completed"})
        release_lock(run_dir)
        raise AppError(
            ErrorCode.APPLY_FAILED_ROLLBACK_SUCCEEDED,
            f"apply failed ({cause['code']}: {cause['message']}); "
            "destination restored from snapshot",
            details,
        ) from exc


def _error_summary(exc: Exception) -> dict[str, str]:
    if isinstance(exc, AppError):
        return {"code": exc.code.value, "message": exc.message}
    return {"code": type(exc).__name__, "message": str(exc)}


def _journal_best_effort(run_dir: Path, record: dict[str, Any]) -> None:
    try:
        append_journal(run_dir, record)
    except OSError:
        pass


def _restore_files(pairs: list[tuple[Path, Path | None]]) -> list[str]:
    """Restore each target from its snapshot and return the targets that do not match afterwards."""
    not_restored: list[str] = []
    for target, snap in reversed(pairs):
        try:
            if target.is_symlink():
                restored = False
            elif snap is None:
                target.unlink(missing_ok=True)
                restored = not target.exists()
            else:
                shutil.copy2(snap, target)
                restored = sha256_file(target) == sha256_file(snap)
        except OSError:
            restored = False
        if not restored:
            not_restored.append(str(target))
    return not_restored


def verify_run(run_dir: Path, dest_user: Path) -> dict[str, Any]:
    plan = read_plan(run_dir / "plan.json")
    errors: list[str] = []
    for op in _plan_ops(plan):
        target = _target_path(dest_user, op["target_rel"])
        if not target.is_file():
            errors.append(f"missing {op['target_rel']}")
            continue
        if sha256_file(target) != op["post_sha256"]:
            errors.append(f"hash mismatch {op['target_rel']}")
    return {"ok": not errors, "errors": errors}


def rollback_run(run_dir: Path, dest_user: Path, confirm: bool) -> None:
    if not confirm:
        raise AppError(ErrorCode.OUTPUT_EXISTS, "rollback not confirmed")
    plan = read_plan(run_dir / "plan.json")
    snapshot = run_dir / "snapshot"
    pairs: list[tuple[Path, Path | None]] = []
    for op in _plan_ops(plan):
        rel = op["target_rel"]
        target = _target_path(dest_user, rel)
        snap = snapshot / rel.replace("/", "_")
        current = fingerprint_file(target)
        if current.get("exists") and current.get("sha256") != op.get("post_sha256"):
            raise AppError(
                ErrorCode.TARGET_CHANGED,
                "destination changed since apply; refusing rollback",
                {"path": rel},
            )
        if op.get("pre_sha256") is not None and not snap.is_file():
            raise AppError(
                ErrorCode.RUN_INCOMPLETE,
                "snapshot missing for a file that existed before apply; refusing rollback",
                {"path": rel},
            )
        pairs.append((target, snap if snap.is_file() else None))
    with allow_profile_writes(dest_user.parent, [target for target, _ in pairs]):
        not_restored = _restore_files(pairs)
    if not_restored:
        append_journal(run_dir, {"event": "rollback_incomplete", "not_restored": not_restored})
        raise AppError(
            ErrorCode.RUN_INCOMPLETE,
            f"rollback incomplete; {len(not_restored)} file(s) do not match the snapshot",
            {"not_restored": not_restored, "run_dir": str(run_dir)},
        )
    append_journal(run_dir, {"event": "rollback_completed"})
