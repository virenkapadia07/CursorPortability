import hashlib
import json
from pathlib import Path

from cursor_portability.planning.config_merge import (
    ConfigConflict,
    ConflictResolution,
    extension_ids_from_bundle,
    key_digest,
    merge_keybindings_text,
    merge_object_text,
    plan_keybindings_merge,
    plan_settings_merge,
    plan_snippet_merges,
    redact_conflict_for_display,
)
from cursor_portability.planning.jsonc import parse_jsonc


def _layout(tmp_path: Path, member: str, source: str, dest: str | None) -> tuple[Path, Path]:
    bundle = tmp_path / "bundle"
    src = bundle / "user-data/User" / member
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(source, encoding="utf-8")
    dest_user = tmp_path / "dest/User"
    dest_user.mkdir(parents=True, exist_ok=True)
    if dest is not None:
        target = dest_user / member
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(dest, encoding="utf-8")
    return bundle, dest_user


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_settings_add_missing_key(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "user-data/User").mkdir(parents=True)
    (bundle / "user-data/User/settings.json").write_text(
        '{"editor.fontSize": 16, "new.key": true}', encoding="utf-8"
    )
    dest = tmp_path / "dest/User"
    dest.mkdir(parents=True)
    (dest / "settings.json").write_text('{"editor.fontSize": 14}', encoding="utf-8")
    ops, conflicts, _, _ = plan_settings_merge(
        bundle, dest, "linux", set(), ConflictResolution.KEEP_DESTINATION, 1
    )
    assert len(ops) == 1
    assert ops[0].kind == "merge_object"
    assert not [c for c in conflicts if c.resolution == ConflictResolution.UNRESOLVED]


def test_conflict_unresolved_without_resolution(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "user-data/User").mkdir(parents=True)
    (bundle / "user-data/User/settings.json").write_text('{"x": 1}', encoding="utf-8")
    dest = tmp_path / "dest/User"
    dest.mkdir(parents=True)
    (dest / "settings.json").write_text('{"x": 2}', encoding="utf-8")
    ops, conflicts, _, _ = plan_settings_merge(
        bundle, dest, "linux", set(), ConflictResolution.UNRESOLVED, 1
    )
    assert not ops
    assert conflicts[0].resolution == ConflictResolution.UNRESOLVED


def test_use_source_replaces_setting_in_place(tmp_path: Path) -> None:
    dest_text = '{\n  // mine\n  "editor.fontSize": 12,\n  "other": 1\n}'
    bundle, dest_user = _layout(
        tmp_path, "settings.json", '{"editor.fontSize": 16, "new.key": true}', dest_text
    )
    ops, conflicts, _, _ = plan_settings_merge(
        bundle, dest_user, "linux", {"editor.fontSize"}, ConflictResolution.KEEP_DESTINATION, 1
    )
    assert conflicts == [
        ConfigConflict("User/settings.json", "editor.fontSize", ConflictResolution.USE_SOURCE)
    ]
    assert len(ops) == 1
    assert ops[0].use_source_key_digests == (key_digest("editor.fontSize"),)

    source = json.loads((bundle / "user-data/User/settings.json").read_text())
    merged, _ = merge_object_text(parse_jsonc(dest_text), source, ops[0].use_source_key_digests)
    assert _sha(merged) == ops[0].post_sha256
    assert parse_jsonc(merged).value == {"editor.fontSize": 16, "other": 1, "new.key": True}
    assert merged.count('"editor.fontSize"') == 1
    assert "// mine" in merged


def test_null_destination_value_is_a_conflict(tmp_path: Path) -> None:
    bundle, dest_user = _layout(tmp_path, "settings.json", '{"x": 1}', '{"x": null}')
    ops, conflicts, _, _ = plan_settings_merge(
        bundle, dest_user, "linux", set(), ConflictResolution.KEEP_DESTINATION, 1
    )
    assert not ops
    assert conflicts == [
        ConfigConflict("User/settings.json", "x", ConflictResolution.KEEP_DESTINATION)
    ]


def test_json_types_are_not_conflated(tmp_path: Path) -> None:
    bundle, dest_user = _layout(tmp_path, "settings.json", '{"x": true}', '{"x": 1}')
    _, conflicts, _, _ = plan_settings_merge(
        bundle, dest_user, "linux", set(), ConflictResolution.KEEP_DESTINATION, 1
    )
    assert [c.key for c in conflicts] == ["x"]


def test_use_source_keybinding_is_appended_after_destination(tmp_path: Path) -> None:
    dest_text = '[\n  {"key": "ctrl+k", "command": "mine"}\n]'
    bundle, dest_user = _layout(
        tmp_path, "keybindings.json", '[{"key": "ctrl+k", "command": "theirs"}]', dest_text
    )
    ops, conflicts, _, _ = plan_keybindings_merge(
        bundle, dest_user, "linux", {"ctrl+k|"}, ConflictResolution.KEEP_DESTINATION, 1
    )
    assert [c.resolution for c in conflicts] == [ConflictResolution.USE_SOURCE]
    assert ops[0].use_source_key_digests == (key_digest("ctrl+k|"),)
    source = [{"key": "ctrl+k", "command": "theirs"}]
    merged, _ = merge_keybindings_text(
        parse_jsonc(dest_text), source, ops[0].use_source_key_digests
    )
    assert _sha(merged) == ops[0].post_sha256
    assert [e["command"] for e in parse_jsonc(merged).value] == ["mine", "theirs"]


def test_unresolved_snippet_conflict_only_blocks_its_own_file(tmp_path: Path) -> None:
    bundle, dest_user = _layout(
        tmp_path, "snippets/a.json", '{"s": {"body": "x"}}', '{"s": {"body": "y"}}'
    )
    _layout(tmp_path, "snippets/b.json", '{"t": {"body": "x"}}', '{"u": {"body": "z"}}')
    ops, conflicts, _, _ = plan_snippet_merges(
        bundle, dest_user, ConflictResolution.UNRESOLVED, set(), 1
    )
    assert [c.file for c in conflicts] == ["User/snippets/a.json"]
    assert [(op.kind, op.target_rel) for op in ops] == [("merge_object", "User/snippets/b.json")]


def test_snippet_planning_does_not_create_destination_dir(tmp_path: Path) -> None:
    bundle, dest_user = _layout(tmp_path, "snippets/a.json", '{"s": {"body": "x"}}', None)
    ops, _, _, _ = plan_snippet_merges(
        bundle, dest_user, ConflictResolution.KEEP_DESTINATION, set(), 1
    )
    assert not (dest_user / "snippets").exists()
    assert [op.kind for op in ops] == ["create"]


def test_snippet_with_unsafe_file_name_is_skipped(tmp_path: Path) -> None:
    bundle, dest_user = _layout(tmp_path, "snippets/a:b.json", '{"s": {"body": "x"}}', None)
    ops, _, warnings, _ = plan_snippet_merges(
        bundle, dest_user, ConflictResolution.KEEP_DESTINATION, set(), 1
    )
    assert not ops
    assert warnings == ["skip snippet with unsupported file name 'a:b.json'"]


def test_redact_credential_key_name() -> None:
    row = redact_conflict_for_display(
        ConfigConflict("User/settings.json", "auth.token", ConflictResolution.USE_SOURCE)
    )
    assert row["key"] == "<redacted>"


def test_extensions_list(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "cursor-home/extensions").mkdir(parents=True)
    (bundle / "cursor-home/extensions/extensions.json").write_text(
        '[{"identifier": {"id": "publisher.ext"}}]', encoding="utf-8"
    )
    assert extension_ids_from_bundle(bundle) == ["publisher.ext"]
