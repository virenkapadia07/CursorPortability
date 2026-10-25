from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cursor_portability.errors import AppError, ErrorCode
from cursor_portability.security.json_strict import dumps_canonical, loads_strict
from cursor_portability.storage.hashing import sha256_bytes, sha256_file

PLAN_FORMAT = 1
MAX_PLAN_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class MigrationPlan:
    plan_format: int
    plan_id: str
    body: dict[str, Any]


def plan_body_digest(body: dict[str, Any]) -> str:
    return sha256_bytes(dumps_canonical(body).encode("utf-8"))


def wrap_plan(body: dict[str, Any]) -> MigrationPlan:
    plan_id = plan_body_digest(body)
    return MigrationPlan(PLAN_FORMAT, plan_id, body)


def parse_plan(text: str) -> MigrationPlan:
    if len(text) > MAX_PLAN_BYTES:
        raise AppError(ErrorCode.PLAN_INVALID, "plan too large")
    data = loads_strict(text, max_size=MAX_PLAN_BYTES)
    if not isinstance(data, dict):
        raise AppError(ErrorCode.PLAN_INVALID, "plan must be object")
    fmt = int(data.get("plan_format", 0))
    if fmt != PLAN_FORMAT:
        raise AppError(ErrorCode.PLAN_INVALID, f"unsupported plan format: {fmt}")
    plan_id = str(data.get("plan_id", ""))
    body = data.get("body")
    if not isinstance(body, dict):
        raise AppError(ErrorCode.PLAN_INVALID, "plan body must be object")
    if plan_body_digest(body) != plan_id:
        raise AppError(ErrorCode.PLAN_INVALID, "plan_id does not match body")
    return MigrationPlan(fmt, plan_id, body)


def write_plan(path: Path, plan: MigrationPlan) -> None:
    payload = {
        "plan_format": plan.plan_format,
        "plan_id": plan.plan_id,
        "body": plan.body,
    }
    text = dumps_canonical(payload) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(path), flags, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)


def read_plan(path: Path) -> MigrationPlan:
    return parse_plan(path.read_text(encoding="utf-8"))


def fingerprint_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"exists": False}
    return {
        "exists": True,
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def fingerprint_target_user(dest_user: Path, rel_paths: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for rel in rel_paths:
        suffix = rel.removeprefix("User/")
        out[rel] = fingerprint_file(dest_user / suffix)
    snippets_dir = dest_user / "snippets"
    listing: list[str] = []
    if snippets_dir.is_dir():
        listing = sorted(p.name for p in snippets_dir.iterdir() if p.is_file())
    out["User/snippets/_listing"] = listing
    return out
