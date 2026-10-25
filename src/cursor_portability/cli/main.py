from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable
from pathlib import Path

from cursor_portability.bundle.reader import read_bundle_manifest
from cursor_portability.bundle.restore import restore_bundle
from cursor_portability.bundle.verify import verify_bundle
from cursor_portability.cli.output import emit_error, emit_json, emit_text
from cursor_portability.errors import AppError, ErrorCode, ExitCode
from cursor_portability.migration.engine import (
    ApplyOutcome,
    apply_plan,
    rollback_run,
    verify_run,
)
from cursor_portability.models.enums import DataCategory
from cursor_portability.planning.config_merge import ConflictResolution
from cursor_portability.platform.base import get_platform_paths
from cursor_portability.platform.env import Environment, current_environment
from cursor_portability.reporting.log import configure_logging
from cursor_portability.security.permissions import register_protected_roots
from cursor_portability.security.tripwire import TripwireMode, install_tripwire
from cursor_portability.services.backup import create_backup
from cursor_portability.services.compatibility import run_compatibility
from cursor_portability.services.detect import run_detect
from cursor_portability.services.doctor import run_doctor
from cursor_portability.services.inspect import run_inspect
from cursor_portability.services.plan import create_plan, show_plan

_RUN_ID_RE = re.compile(r"[0-9a-f]{12}")


def _protected_roots(env_path: Path | None = None) -> tuple[Path, ...]:
    env = current_environment()
    paths = get_platform_paths(env)
    roots: list[Path] = []
    if paths.user_data_dir:
        roots.append(paths.user_data_dir)
    if paths.cursor_home:
        roots.append(paths.cursor_home)
    if env_path:
        roots.append(env_path.resolve())
    return tuple(roots)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    configure_logging(str(current_environment().home))
    roots = _protected_roots()
    register_protected_roots(roots)
    mode: TripwireMode = "detect_only" if args.command == "detect" else "normal"
    install_tripwire(roots, mode=mode)

    try:
        return _dispatch(args)
    except AppError as err:
        if args.json:
            emit_json({"error": err.code.value, "message": err.message, "details": err.details})
        else:
            emit_error(str(err))
        if err.code.value == "CURSOR_RUNNING":
            return ExitCode.PRECONDITION
        if err.code.value == "UNKNOWN_SCHEMA":
            return ExitCode.UNKNOWN
        return ExitCode.FAIL
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else ExitCode.USAGE


def _dispatch(args: argparse.Namespace) -> int:
    env = current_environment()
    if args.command == "doctor":
        paths = get_platform_paths(env)
        report = run_doctor(env.home, paths.user_data_dir)
        if args.json:
            emit_json(
                {
                    "outcome": report.outcome.value,
                    "runtime": report.runtime,
                    "sqlite": report.sqlite,
                    "cursor_state": report.cursor_state,
                    "free_bytes_home": report.free_bytes_home,
                    "notes": report.notes,
                }
            )
        else:
            emit_text(f"Outcome: {report.outcome.value}")
            for n in report.notes:
                emit_text(f"  - {n}")
        return ExitCode.PASS if report.outcome.value == "PASS" else ExitCode.PARTIAL

    if args.command == "detect":
        ud = Path(args.user_data_dir) if args.user_data_dir else None
        detected = run_detect(env, ud)
        if args.json:
            emit_json(
                {
                    "notes": detected.platform_notes,
                    "profiles": [p.__dict__ for p in detected.profiles],
                    "wsl_windows_cursor_home": detected.wsl_windows_cursor_home,
                }
            )
        else:
            for p in detected.profiles:
                emit_text(f"Profile: {p.user_data_dir}")
        return ExitCode.PASS

    if args.command == "inspect":
        profile = Path(args.profile) if args.profile else None
        skeleton = Path(args.emit_skeleton) if args.emit_skeleton else None
        inspected = run_inspect(env, profile, args.shareable, skeleton)
        if args.json:
            emit_json(inspected.__dict__)
        else:
            emit_text(f"Profile: {inspected.profile_path}")
            emit_text(f"Databases: {len(inspected.databases)}")
        return ExitCode.PASS

    if args.command == "compatibility":
        compat = run_compatibility()
        if args.json:
            emit_json(compat.__dict__)
        else:
            emit_text(f"Tool {compat.tool_version} phase {compat.phase}")
        return ExitCode.PASS

    if args.command == "backup":
        return _backup_dispatch(args, env)

    if args.command == "plan":
        return _plan_dispatch(args, env)

    if args.command == "migrate":
        return _migrate_dispatch(args, env)

    return ExitCode.USAGE


def _backup_dispatch(args: argparse.Namespace, env: Environment) -> int:
    if args.backup_cmd == "create":
        inc = {DataCategory(x) for x in args.include} if args.include else None
        exc = {DataCategory(x) for x in args.exclude} if args.exclude else None
        report = create_backup(
            env,
            Path(args.output),
            as_zip=args.zip,
            overwrite=args.overwrite,
            confirm_overwrite=_overwrite_confirmation(args.yes),
            confirm_cursor_closed=args.confirm_cursor_closed,
            include_categories=inc,
            exclude_categories=exc,
            opt_in_mcp=args.include_mcp,
        )
        if args.json:
            emit_json(report.__dict__)
        else:
            for w in report.warnings:
                emit_error(w)
            emit_text(f"Backup created: {report.bundle_path}")
        return ExitCode.PASS
    if args.backup_cmd == "verify":
        rep = verify_bundle(Path(args.path), full_integrity=args.full)
        if args.json:
            emit_json(
                {"outcome": rep.outcome.value, "errors": rep.errors, "files": rep.files_checked}
            )
        else:
            emit_text(f"Verify: {rep.outcome.value} ({rep.files_checked} files)")
        return ExitCode.PASS if rep.outcome.value == "PASS" else ExitCode.FAIL
    if args.backup_cmd == "list":
        m = read_bundle_manifest(Path(args.path))
        if args.json:
            emit_json(
                {"files": len(m.files), "created_at": m.created_at, "tool_version": m.tool_version}
            )
        else:
            emit_text(f"Bundle files: {len(m.files)} created {m.created_at}")
        return ExitCode.PASS
    if args.backup_cmd == "restore":
        target = restore_bundle(Path(args.bundle), Path(args.to))
        if args.json:
            emit_json({"restored_to": str(target)})
        else:
            emit_text(f"Restored to {target}")
        return ExitCode.PASS
    return ExitCode.USAGE


def _yes_confirmation(assume_yes: bool, prompt: str) -> Callable[[], bool]:
    def confirm() -> bool:
        if assume_yes:
            return True
        if not sys.stdin.isatty():
            raise AppError(
                ErrorCode.OUTPUT_EXISTS,
                f"{prompt}; run in a terminal or add --yes",
            )
        sys.stderr.write(f"{prompt} Type 'yes' to confirm: ")
        sys.stderr.flush()
        return sys.stdin.readline().strip() == "yes"

    return confirm


def _plan_dispatch(args: argparse.Namespace, env: Environment) -> int:
    if args.plan_cmd == "create":
        on_conflict = ConflictResolution.KEEP_DESTINATION
        if getattr(args, "on_conflict", None) == "unresolved":
            on_conflict = ConflictResolution.UNRESOLVED
        use_source = set(args.use_source or [])

        def _ask_map(src: str) -> str:
            if not sys.stdin.isatty():
                return ""
            sys.stderr.write(f"Destination path for {src} (empty=skip): ")
            sys.stderr.flush()
            return sys.stdin.readline().strip()

        report = create_plan(
            env,
            Path(args.backup),
            Path(args.output),
            Path(args.target_profile) if args.target_profile else None,
            list(args.path_map or []),
            Path(args.path_map_file) if args.path_map_file else None,
            args.interactive,
            _ask_map if args.interactive else None,
            use_source,
            on_conflict,
        )
        if args.json:
            emit_json(report.__dict__)
        else:
            for w in report.warnings:
                emit_error(w)
            emit_text(f"Plan written: {report.plan_path} ({report.plan_id})")
        return ExitCode.PASS
    if args.plan_cmd == "show":
        data = show_plan(Path(args.plan), args.shareable)
        if args.json:
            emit_json(data)
        else:
            body_obj = data["body"]
            assert isinstance(body_obj, dict)
            emit_text(f"Plan {data['plan_id']}")
            ops = body_obj.get("operations", [])
            emit_text(f"Operations: {len(ops) if isinstance(ops, list) else 0}")
        return ExitCode.PASS
    return ExitCode.USAGE


def _migrate_dispatch(args: argparse.Namespace, env: Environment) -> int:
    runs_dir = Path(args.runs_dir) if args.runs_dir else env.home / ".cursor-portability" / "runs"
    if args.migrate_cmd == "apply":
        from cursor_portability.planning.plan import read_plan

        plan = read_plan(Path(args.plan))
        dest_user = Path(str(plan.body["target_user_data_dir"])) / "User"
        if not args.json:
            emit_error("Migration will modify these destination files:")
            for op in plan.body.get("operations", []):
                if isinstance(op, dict):
                    emit_error(f"  - {op.get('target_rel')}")
        confirmed = _yes_confirmation(args.yes, "Apply migration plan?")()
        report = apply_plan(
            Path(args.plan),
            Path(args.backup),
            runs_dir,
            confirmed,
        )
        if args.json:
            emit_json(
                {
                    "run_id": report.run_id,
                    "run_dir": report.run_dir,
                    "outcome": report.outcome.value,
                    "operations": report.operations,
                }
            )
        elif report.outcome is ApplyOutcome.ALREADY_APPLIED:
            emit_text("Apply: already_applied (destination already matches the plan)")
        else:
            emit_text(f"Apply: {report.outcome.value} (run {report.run_id})")
        return ExitCode.PASS
    if not _RUN_ID_RE.fullmatch(args.run_id):
        raise AppError(ErrorCode.PLAN_INVALID, "invalid run id", {"run_id": args.run_id})
    if args.migrate_cmd == "verify":
        run_dir = runs_dir / args.run_id
        from cursor_portability.planning.plan import read_plan

        plan = read_plan(run_dir / "plan.json")
        dest_user = Path(str(plan.body["target_user_data_dir"])) / "User"
        result = verify_run(run_dir, dest_user)
        if args.json:
            emit_json(result)
        else:
            emit_text(f"Verify run: {'PASS' if result['ok'] else 'FAIL'}")
        return ExitCode.PASS if result["ok"] else ExitCode.FAIL
    if args.migrate_cmd == "rollback":
        run_dir = runs_dir / args.run_id
        from cursor_portability.planning.plan import read_plan

        plan = read_plan(run_dir / "plan.json")
        dest_user = Path(str(plan.body["target_user_data_dir"])) / "User"
        confirmed = _yes_confirmation(args.yes, "Rollback migration run?")()
        rollback_run(run_dir, dest_user, confirmed)
        if args.json:
            emit_json({"rolled_back": args.run_id})
        else:
            emit_text(f"Rolled back run {args.run_id}")
        return ExitCode.PASS
    return ExitCode.USAGE


def _overwrite_confirmation(assume_yes: bool) -> Callable[[Path], bool]:
    def confirm(path: Path) -> bool:
        if assume_yes:
            return True
        if not sys.stdin.isatty():
            raise AppError(
                ErrorCode.OUTPUT_EXISTS,
                "replacing an existing backup needs confirmation; run in a terminal or add --yes",
                {"path": str(path)},
            )
        sys.stderr.write(f"Replace the existing backup at {path}? Type 'yes' to confirm: ")
        sys.stderr.flush()
        return sys.stdin.readline().strip() == "yes"

    return confirm


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    # SUPPRESS keeps subparser defaults from overwriting a root-level --json.
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    p = argparse.ArgumentParser(prog="cursor-port")
    p.add_argument("--json", action="store_true", help="JSON output on stdout")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", parents=[common])
    d = sub.add_parser("detect", parents=[common])
    d.add_argument("--user-data-dir")

    ins = sub.add_parser("inspect", parents=[common])
    ins.add_argument("--profile")
    ins.add_argument("--shareable", action="store_true")
    ins.add_argument("--emit-skeleton")

    sub.add_parser("compatibility", parents=[common])

    b = sub.add_parser("backup", parents=[common])
    bsub = b.add_subparsers(dest="backup_cmd", required=True)
    c = bsub.add_parser("create", parents=[common])
    c.add_argument("--output", required=True)
    c.add_argument("--zip", action="store_true")
    c.add_argument("--overwrite", action="store_true", help="replace an existing bundle")
    c.add_argument("--yes", action="store_true", help="skip the --overwrite confirmation prompt")
    c.add_argument("--confirm-cursor-closed", action="store_true")
    c.add_argument("--include", action="append")
    c.add_argument("--exclude", action="append")
    c.add_argument("--include-mcp", action="store_true")
    v = bsub.add_parser("verify", parents=[common])
    v.add_argument("path")
    v.add_argument("--full", action="store_true")
    list_p = bsub.add_parser("list", parents=[common])
    list_p.add_argument("path")
    r = bsub.add_parser("restore", parents=[common])
    r.add_argument("bundle")
    r.add_argument("--to", required=True)

    pl = sub.add_parser("plan", parents=[common])
    plsub = pl.add_subparsers(dest="plan_cmd", required=True)
    pc = plsub.add_parser("create", parents=[common])
    pc.add_argument("--backup", required=True)
    pc.add_argument("--output", required=True)
    pc.add_argument("--target-profile")
    pc.add_argument("--path-map", action="append")
    pc.add_argument("--path-map-file")
    pc.add_argument("--interactive", action="store_true")
    pc.add_argument("--on-conflict", choices=["keep-destination", "unresolved"])
    pc.add_argument("--use-source", action="append")
    ps = plsub.add_parser("show", parents=[common])
    ps.add_argument("plan")
    ps.add_argument("--shareable", action="store_true")

    mg = sub.add_parser("migrate", parents=[common])
    mgsub = mg.add_subparsers(dest="migrate_cmd", required=True)
    ma = mgsub.add_parser("apply", parents=[common])
    ma.add_argument("plan")
    ma.add_argument("--backup", required=True)
    ma.add_argument("--yes", action="store_true")
    ma.add_argument("--runs-dir")
    mv = mgsub.add_parser("verify", parents=[common])
    mv.add_argument("--run-id", required=True)
    mv.add_argument("--runs-dir")
    mr = mgsub.add_parser("rollback", parents=[common])
    mr.add_argument("--run-id", required=True)
    mr.add_argument("--yes", action="store_true")
    mr.add_argument("--runs-dir")
    return p


if __name__ == "__main__":
    sys.exit(main())
