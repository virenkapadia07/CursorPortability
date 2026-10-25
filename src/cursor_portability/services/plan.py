from __future__ import annotations

import platform
import tempfile
import zipfile
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
from cursor_portability.bundle.manifest import read_manifest
from cursor_portability.bundle.reader import ZipBundle, read_bundle_manifest
from cursor_portability.bundle.verify import verify_bundle
from cursor_portability.discovery.locate import locate_profiles
from cursor_portability.errors import AppError, ErrorCode
from cursor_portability.models.enums import ValidationOutcome
from cursor_portability.planning.config_merge import (
    ConfigConflict,
    ConflictResolution,
    PlannedOp,
    extension_ids_from_bundle,
    extension_ids_on_disk,
    plan_keybindings_merge,
    plan_settings_merge,
    plan_snippet_merges,
    redact_conflict_for_display,
)
from cursor_portability.planning.pathmap import (
    build_workspace_mappings,
    collect_bundle_workspaces,
    detect_destination_collisions,
    interactive_fill_mappings,
    load_path_map_file,
    parse_path_map_pairs,
)
from cursor_portability.planning.plan import (
    fingerprint_target_user,
    wrap_plan,
    write_plan,
)
from cursor_portability.platform.base import get_platform_paths
from cursor_portability.platform.env import Environment
from cursor_portability.storage.hashing import sha256_bytes


@dataclass(frozen=True)
class PlanCreateReport:
    plan_path: str
    plan_id: str
    operations: int
    unresolved_conflicts: int
    warnings: tuple[str, ...]


def _materialize_bundle(bundle: Path, staging: Path) -> Path:
    if bundle.is_dir():
        return bundle
    if not zipfile.is_zipfile(bundle):
        raise AppError(ErrorCode.BACKUP_INVALID, "not a bundle")
    manifest = read_bundle_manifest(bundle)
    meta = (MANIFEST_NAME, CHECKSUMS_NAME, EXCLUSIONS_NAME, SOURCE_REPORT_NAME, README_NAME)
    with ZipBundle(bundle) as zb:
        for name in meta:
            try:
                zb.extract_member(name, staging / name)
            except AppError:
                pass
        for fe in manifest.files:
            zb.extract_member(fe.path, staging / fe.path)
    return staging


def create_plan(
    env: Environment,
    bundle_path: Path,
    output: Path,
    target_profile: Path | None,
    path_maps: list[str],
    path_map_file: Path | None,
    interactive: bool,
    ask_mapping: Callable[[str], str] | None,
    use_source_keys: set[str],
    on_conflict: ConflictResolution,
    source_os: str | None = None,
    dest_os: str | None = None,
) -> PlanCreateReport:
    verify = verify_bundle(bundle_path, full_integrity=True)
    if verify.outcome != ValidationOutcome.PASS:
        raise AppError(ErrorCode.BACKUP_INVALID, "bundle verify failed", {"errors": verify.errors})

    paths = get_platform_paths(env)
    profiles = locate_profiles(paths, target_profile)
    if not profiles:
        raise AppError(ErrorCode.PROFILE_NOT_FOUND, "destination profile not found")
    ud = profiles[0].user_data_dir
    ch = profiles[0].cursor_home
    dest_user = ud / "User"

    mappings = parse_path_map_pairs(path_maps)
    if path_map_file:
        mappings.extend(load_path_map_file(path_map_file))

    with tempfile.TemporaryDirectory(prefix="cursor-port-plan-") as tmp:
        bundle_root = _materialize_bundle(bundle_path, Path(tmp) / "bundle")
        manifest = read_manifest(bundle_root / MANIFEST_NAME)
        bundle_hash = sha256_bytes((bundle_root / MANIFEST_NAME).read_bytes())

        bundle_ws = collect_bundle_workspaces(bundle_root)
        src_os = source_os or manifest.source_os.lower()
        if src_os == "darwin":
            src_os = "darwin"
        elif src_os.startswith("win"):
            src_os = "win32"
        else:
            src_os = "linux"
        dst_os = dest_os or platform.system().lower()
        if dst_os == "darwin":
            dst_os = "darwin"
        elif dst_os.startswith("win"):
            dst_os = "win32"
        else:
            dst_os = "linux"

        ws_results = build_workspace_mappings(bundle_ws, mappings, src_os, dst_os)
        missing = [r for r in ws_results if r.status.value == "missing"]
        skipped: set[str] = set()
        if interactive and missing and ask_mapping:
            extra, skipped = interactive_fill_mappings(missing, ask_mapping)
            mappings.extend(extra)
            ws_results = build_workspace_mappings(bundle_ws, mappings, src_os, dst_os, skipped)

        collisions = detect_destination_collisions(ws_results)
        warnings: list[str] = []
        if collisions:
            warnings.append(f"destination path collisions: {collisions}")

        ops: list[PlannedOp] = []
        conflicts: list[ConfigConflict] = []
        next_id = 1
        o, c, w, next_id = plan_settings_merge(
            bundle_root, dest_user, src_os, use_source_keys, on_conflict, next_id
        )
        ops.extend(o)
        conflicts.extend(c)
        warnings.extend(w)
        o, c, w, next_id = plan_keybindings_merge(
            bundle_root, dest_user, dst_os, use_source_keys, on_conflict, next_id
        )
        ops.extend(o)
        conflicts.extend(c)
        warnings.extend(w)
        o, c, w, next_id = plan_snippet_merges(
            bundle_root, dest_user, on_conflict, use_source_keys, next_id
        )
        ops.extend(o)
        conflicts.extend(c)
        warnings.extend(w)

        unresolved = [c for c in conflicts if c.resolution == ConflictResolution.UNRESOLVED]
        rel_targets = sorted({op.target_rel for op in ops})
        target_fp = fingerprint_target_user(dest_user, rel_targets)

        ext_bundle = extension_ids_from_bundle(bundle_root)
        ext_dest = extension_ids_on_disk(ch)
        ext_to_install = sorted(set(ext_bundle) - ext_dest)

        db_inventory = [
            {"path": p, "adapter": manifest.schema_adapters.get(p, "unknown"), "phase": "not_migrated"}
            for p in sorted(manifest.schema_adapters)
        ]

        body = {
            "bundle_path_hint": str(bundle_path.name),
            "bundle_manifest_sha256": bundle_hash,
            "source_os": src_os,
            "dest_os": dst_os,
            "target_user_data_dir": str(ud),
            "target_fingerprint": target_fp,
            "path_mappings": [{"old": m.old_path, "new": m.new_path} for m in mappings],
            "workspaces": [
                {
                    "id": r.workspace_id,
                    "source_uri": r.source_uri,
                    "source_path": r.source_path,
                    "dest_path": r.dest_path,
                    "dest_uri": r.dest_uri,
                    "status": r.status.value,
                    "detail": r.detail,
                }
                for r in ws_results
            ],
            "operations": [op.__dict__ for op in ops],
            "conflicts": [redact_conflict_for_display(c) for c in conflicts],
            "warnings": warnings,
            "extensions_to_install": ext_to_install,
            "database_inventory": db_inventory,
            "required_acknowledgements": [],
        }
        if unresolved:
            body["required_acknowledgements"] = ["resolve_conflicts_before_apply"]

        plan = wrap_plan(body)
        write_plan(output, plan)
        return PlanCreateReport(
            str(output),
            plan.plan_id,
            len(ops),
            len(unresolved),
            tuple(warnings),
        )
    raise AppError(ErrorCode.PLAN_INVALID, "plan creation failed")


def show_plan(path: Path, shareable: bool = False) -> dict[str, object]:
    from cursor_portability.planning.plan import read_plan

    plan = read_plan(path)
    body = dict(plan.body)
    if shareable:
        body.pop("target_user_data_dir", None)
    return {"plan_id": plan.plan_id, "body": body}
