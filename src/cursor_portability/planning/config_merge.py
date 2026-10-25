from __future__ import annotations

import json
import re
from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from cursor_portability.planning.jsonc import (
    JsoncDocument,
    JsoncError,
    insert_array_entries,
    insert_object_entries,
    parse_jsonc,
    replace_object_values,
)
from cursor_portability.planning.targets import (
    SNIPPET_SUFFIXES,
    SNIPPETS_PREFIX,
    is_allowed_snippet_name,
    source_member_for,
)
from cursor_portability.security.credential_patterns import is_credential_key
from cursor_portability.storage.hashing import sha256_bytes

_PLATFORM_KEY_SUFFIXES = (".osx", ".mac", ".windows", ".linux")


class ConflictResolution(StrEnum):
    UNRESOLVED = "unresolved"
    KEEP_DESTINATION = "keep_destination"
    USE_SOURCE = "use_source"


@dataclass(frozen=True)
class ConfigConflict:
    file: str
    key: str
    resolution: ConflictResolution


@dataclass(frozen=True)
class PlannedOp:
    op_id: str
    kind: str  # create | merge_object | append_array
    target_rel: str
    pre_sha256: str | None
    post_sha256: str
    source_member: str
    source_sha256: str
    # Digests, not names, so credential-like key names stay out of the plan file.
    use_source_key_digests: tuple[str, ...] = ()


def _sha_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def key_digest(key: str) -> str:
    return sha256_bytes(key.encode("utf-8"))


def _same_json(a: Any, b: Any) -> bool:
    # Plain == treats true, 1 and 1.0 as equal.
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def merge_object_text(
    dest: JsoncDocument, source: dict[str, Any], use_source_digests: Collection[str]
) -> tuple[str, list[str]]:
    """Return the merged text and the keys whose source and destination values differ."""
    if not isinstance(dest.value, dict):
        raise JsoncError("expected object")
    differing = [
        k for k, v in source.items() if k in dest.value and not _same_json(dest.value[k], v)
    ]
    replacements = {k: source[k] for k in differing if key_digest(k) in use_source_digests}
    additions = {k: v for k, v in source.items() if k not in dest.value}
    text = replace_object_values(dest, replacements) if replacements else dest.text
    if additions:
        text = insert_object_entries(parse_jsonc(text), additions)
    if not _same_json(parse_jsonc(text).value, {**dest.value, **replacements, **additions}):
        raise JsoncError("object merge self-check failed")
    return text, differing


def keybinding_identity(entry: dict[str, Any]) -> str:
    return f"{entry.get('key', '')}|{entry.get('when', '')}"


def merge_keybindings_text(
    dest: JsoncDocument, source: list[Any], use_source_digests: Collection[str]
) -> tuple[str, list[str]]:
    """Return the merged text and the identities whose commands differ.

    A source binding that wins a conflict is appended after the destination's binding,
    because the last matching keybinding takes precedence.
    """
    if not isinstance(dest.value, list):
        raise JsoncError("expected array")
    commands = {keybinding_identity(e): e.get("command") for e in dest.value if isinstance(e, dict)}
    differing: list[str] = []
    to_append: list[Any] = []
    for entry in source:
        if not isinstance(entry, dict):
            continue
        ident = keybinding_identity(entry)
        if ident not in commands:
            to_append.append(entry)
        elif not _same_json(commands[ident], entry.get("command")):
            differing.append(ident)
            if key_digest(ident) in use_source_digests:
                to_append.append(entry)
    text = insert_array_entries(dest, to_append) if to_append else dest.text
    if not _same_json(parse_jsonc(text).value, [*dest.value, *to_append]):
        raise JsoncError("keybindings merge self-check failed")
    return text, differing


def _resolution(
    key: str, use_source_keys: set[str], default_conflict: ConflictResolution
) -> ConflictResolution:
    if key in use_source_keys:
        return ConflictResolution.USE_SOURCE
    if default_conflict == ConflictResolution.KEEP_DESTINATION:
        return ConflictResolution.KEEP_DESTINATION
    return ConflictResolution.UNRESOLVED


def _winning_digests(differing: list[str], use_source_keys: set[str]) -> tuple[str, ...]:
    return tuple(sorted({key_digest(k) for k in differing if k in use_source_keys}))


def _read_bundle_member(bundle_root: Path, member: str) -> bytes:
    p = bundle_root / member
    return p.read_bytes()


def _platform_key_warning(key: str) -> str | None:
    for suf in _PLATFORM_KEY_SUFFIXES:
        if key.endswith(suf):
            return f"platform-specific key suffix {suf}"
    return None


def _abs_path_value_warning(value: Any, source_os: str) -> str | None:
    if not isinstance(value, str):
        return None
    if source_os == "win32" and re.match(r"^[A-Za-z]:\\", value):
        return "value looks like a Windows absolute path"
    if source_os != "win32" and value.startswith("/"):
        return "value looks like a POSIX absolute path"
    return None


def plan_settings_merge(
    bundle_root: Path,
    dest_user: Path,
    source_os: str,
    use_source_keys: set[str],
    default_conflict: ConflictResolution,
    op_id_start: int,
) -> tuple[list[PlannedOp], list[ConfigConflict], list[str], int]:
    member = "user-data/User/settings.json"
    source_bytes = _read_bundle_member(bundle_root, member)
    source_doc = parse_jsonc(source_bytes.decode("utf-8"))
    if not isinstance(source_doc.value, dict):
        raise JsoncError("settings must be object")
    target = dest_user / "settings.json"
    warnings: list[str] = []
    conflicts: list[ConfigConflict] = []
    ops: list[PlannedOp] = []
    next_id = op_id_start
    if not target.is_file():
        post = source_bytes.decode("utf-8")
        ops.append(
            PlannedOp(
                f"op-{next_id}",
                "create",
                "User/settings.json",
                None,
                _sha_text(post),
                member,
                _sha_text(post),
            )
        )
        return ops, conflicts, warnings, next_id + 1
    dest_doc = parse_jsonc(target.read_text(encoding="utf-8"))
    if not isinstance(dest_doc.value, dict):
        raise JsoncError("destination settings must be object")
    for key, sval in source_doc.value.items():
        w = _platform_key_warning(key)
        if w:
            warnings.append(f"settings {key}: {w}")
        w2 = _abs_path_value_warning(sval, source_os)
        if w2:
            warnings.append(f"settings {key}: {w2}")
    wins = {key_digest(k) for k in use_source_keys}
    merged_text, differing = merge_object_text(dest_doc, source_doc.value, wins)
    conflicts = [
        ConfigConflict("User/settings.json", k, _resolution(k, use_source_keys, default_conflict))
        for k in differing
    ]
    if any(c.resolution == ConflictResolution.UNRESOLVED for c in conflicts):
        return ops, conflicts, warnings, next_id
    if merged_text != dest_doc.text:
        ops.append(
            PlannedOp(
                f"op-{next_id}",
                "merge_object",
                "User/settings.json",
                _sha_text(dest_doc.text),
                _sha_text(merged_text),
                member,
                _sha_text(source_bytes.decode("utf-8")),
                _winning_digests(differing, use_source_keys),
            )
        )
        next_id += 1
    return ops, conflicts, warnings, next_id


def plan_keybindings_merge(
    bundle_root: Path,
    dest_user: Path,
    dest_os: str,
    use_source_keys: set[str],
    default_conflict: ConflictResolution,
    op_id_start: int,
) -> tuple[list[PlannedOp], list[ConfigConflict], list[str], int]:
    member = "user-data/User/keybindings.json"
    path = bundle_root / member
    if not path.is_file():
        return [], [], [], op_id_start
    source_doc = parse_jsonc(path.read_text(encoding="utf-8"))
    if not isinstance(source_doc.value, list):
        raise JsoncError("keybindings must be array")
    target = dest_user / "keybindings.json"
    warnings: list[str] = []
    conflicts: list[ConfigConflict] = []
    ops: list[PlannedOp] = []
    next_id = op_id_start
    for entry in source_doc.value:
        if isinstance(entry, dict):
            cmd = str(entry.get("key", ""))
            if dest_os != "darwin" and "cmd" in cmd:
                warnings.append(f"keybinding uses cmd on non-macOS: {cmd}")
            if dest_os == "darwin" and ("win" in cmd or "meta" in cmd):
                warnings.append(f"keybinding uses win/meta on macOS: {cmd}")
    if not target.is_file():
        post = path.read_text(encoding="utf-8")
        ops.append(
            PlannedOp(
                f"op-{next_id}",
                "create",
                "User/keybindings.json",
                None,
                _sha_text(post),
                member,
                _sha_text(post),
            )
        )
        return ops, conflicts, warnings, next_id + 1
    dest_doc = parse_jsonc(target.read_text(encoding="utf-8"))
    if not isinstance(dest_doc.value, list):
        raise JsoncError("destination keybindings must be array")
    wins = {key_digest(k) for k in use_source_keys}
    merged_text, differing = merge_keybindings_text(dest_doc, source_doc.value, wins)
    conflicts = [
        ConfigConflict(
            "User/keybindings.json", k, _resolution(k, use_source_keys, default_conflict)
        )
        for k in differing
    ]
    if any(c.resolution == ConflictResolution.UNRESOLVED for c in conflicts):
        return ops, conflicts, warnings, next_id
    if merged_text == dest_doc.text:
        return ops, conflicts, warnings, next_id
    ops.append(
        PlannedOp(
            f"op-{next_id}",
            "append_array",
            "User/keybindings.json",
            _sha_text(dest_doc.text),
            _sha_text(merged_text),
            member,
            _sha_text(path.read_text(encoding="utf-8")),
            _winning_digests(differing, use_source_keys),
        )
    )
    return ops, conflicts, warnings, next_id + 1


def plan_snippet_merges(
    bundle_root: Path,
    dest_user: Path,
    default_conflict: ConflictResolution,
    use_source_keys: set[str],
    op_id_start: int,
) -> tuple[list[PlannedOp], list[ConfigConflict], list[str], int]:
    src_dir = bundle_root / "user-data/User/snippets"
    ops: list[PlannedOp] = []
    conflicts: list[ConfigConflict] = []
    warnings: list[str] = []
    next_id = op_id_start
    if not src_dir.is_dir():
        return ops, conflicts, warnings, next_id
    wins = {key_digest(k) for k in use_source_keys}
    for src_file in sorted(src_dir.iterdir()):
        if not src_file.is_file():
            continue
        if not src_file.name.endswith(SNIPPET_SUFFIXES):
            continue
        if not is_allowed_snippet_name(src_file.name):
            warnings.append(f"skip snippet with unsupported file name {src_file.name!r}")
            continue
        rel = f"{SNIPPETS_PREFIX}{src_file.name}"
        member = source_member_for(rel)
        source_text = src_file.read_text(encoding="utf-8")
        target = dest_user / "snippets" / src_file.name
        if not target.is_file():
            ops.append(
                PlannedOp(
                    f"op-{next_id}",
                    "create",
                    rel,
                    None,
                    _sha_text(source_text),
                    member,
                    _sha_text(source_text),
                )
            )
            next_id += 1
            continue
        sdoc = parse_jsonc(source_text)
        ddoc = parse_jsonc(target.read_text(encoding="utf-8"))
        if not isinstance(sdoc.value, dict) or not isinstance(ddoc.value, dict):
            warnings.append(f"skip non-object snippet {src_file.name}")
            continue
        merged, differing = merge_object_text(ddoc, sdoc.value, wins)
        file_conflicts = [
            ConfigConflict(rel, k, _resolution(k, use_source_keys, default_conflict))
            for k in differing
        ]
        conflicts.extend(file_conflicts)
        if any(c.resolution == ConflictResolution.UNRESOLVED for c in file_conflicts):
            continue
        if merged == ddoc.text:
            continue
        ops.append(
            PlannedOp(
                f"op-{next_id}",
                "merge_object",
                rel,
                _sha_text(ddoc.text),
                _sha_text(merged),
                member,
                _sha_text(source_text),
                _winning_digests(differing, use_source_keys),
            )
        )
        next_id += 1
    return ops, conflicts, warnings, next_id


def extension_ids_from_bundle(bundle_root: Path) -> list[str]:
    path = bundle_root / "cursor-home/extensions/extensions.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise JsoncError("extensions.json must be array")
    ids: list[str] = []
    for item in data:
        if isinstance(item, dict) and "identifier" in item:
            ident = item["identifier"]
            if isinstance(ident, dict) and "id" in ident:
                ids.append(str(ident["id"]))
            elif isinstance(ident, str):
                ids.append(ident)
        elif isinstance(item, str):
            ids.append(item)
    return ids


def extension_ids_on_disk(cursor_home: Path | None) -> set[str]:
    if cursor_home is None:
        return set()
    path = cursor_home / "extensions" / "extensions.json"
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    if not isinstance(data, list):
        return set()
    out: set[str] = set()
    for item in data:
        if isinstance(item, dict) and "identifier" in item:
            ident = item["identifier"]
            if isinstance(ident, dict) and "id" in ident:
                out.add(str(ident["id"]))
    return out


def redact_conflict_for_display(conflict: ConfigConflict) -> dict[str, str]:
    key = conflict.key
    if is_credential_key(key):
        key = "<redacted>"
    return {
        "file": conflict.file,
        "key": key,
        "resolution": conflict.resolution.value,
    }
