from pathlib import Path

from cursor_portability.planning.pathmap import (
    PathMapping,
    WorkspaceMapStatus,
    build_workspace_mappings,
    detect_destination_collisions,
    resolve_path_for_source,
)
from cursor_portability.planning.uri import parse_workspace_uri, path_to_file_uri


def test_parse_posix_file_uri() -> None:
    p = parse_workspace_uri("file:///tmp/project")
    assert p.native_path == "/tmp/project"


def test_parse_windows_drive_uri() -> None:
    p = parse_workspace_uri("file:///c%3A/Users/me/proj")
    assert p.native_path and "c:" in p.native_path.lower()


def test_parse_unicode_percent_encoded() -> None:
    p = parse_workspace_uri("file:///tmp/hello%20world")
    assert p.native_path == "/tmp/hello world"


def test_remote_uri_unsupported() -> None:
    p = parse_workspace_uri("vscode-remote://wsl+ubuntu/tmp/x")
    assert p.kind.value == "unsupported"


def test_path_to_file_uri_win32(tmp_path: Path) -> None:
    uri = path_to_file_uri(tmp_path, "win32")
    assert uri.startswith("file:///")


def test_longest_prefix_mapping() -> None:
    maps = [
        PathMapping("/Users/me", "/home/me"),
        PathMapping("/Users/me/repos", "/home/me/repos"),
    ]
    dest, err = resolve_path_for_source(
        "/Users/me/repos/app", maps, "darwin", "linux"
    )
    assert err is None
    assert dest == "/home/me/repos/app"


def test_ambiguous_equal_prefix() -> None:
    maps = [
        PathMapping("/a/b", "/x"),
        PathMapping("/a/b", "/y"),
    ]
    dest, err = resolve_path_for_source("/a/b/c", maps, "linux", "linux")
    assert dest is None
    assert err == "PATH_MAPPING_AMBIGUOUS"


def test_cross_os_mapping_matrix() -> None:
    # macOS -> Windows
    dest, _ = resolve_path_for_source(
        "/Users/dev/project",
        [PathMapping("/Users/dev", "C:\\Users\\dev")],
        "darwin",
        "win32",
    )
    assert dest and "dev" in dest.lower()
    # Windows -> Linux
    dest2, _ = resolve_path_for_source(
        "C:\\Users\\dev\\project",
        [PathMapping("C:\\Users\\dev", "/home/dev")],
        "win32",
        "linux",
    )
    assert dest2 == "/home/dev/project"


def test_destination_collision_detection() -> None:
    from cursor_portability.planning.pathmap import WorkspaceMappingResult

    rows = [
        WorkspaceMappingResult("a", "u1", "/s/a", "/d/x", "uri", WorkspaceMapStatus.MAPPED),
        WorkspaceMappingResult("b", "u2", "/s/b", "/d/x", "uri", WorkspaceMapStatus.MAPPED),
    ]
    assert detect_destination_collisions(rows) == [("a", "b")]


def test_build_workspace_mappings_missing(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    ws = bundle / "user-data/User/workspaceStorage/wsid1234567890123456789012345678"
    ws.mkdir(parents=True)
    (ws / "workspace.json").write_text('{"folder": "file:///src/proj"}', encoding="utf-8")
    rows = build_workspace_mappings(
        [("wsid1234567890123456789012345678", "file:///src/proj")],
        [],
        "linux",
        "linux",
    )
    assert rows[0].status == WorkspaceMapStatus.MISSING
