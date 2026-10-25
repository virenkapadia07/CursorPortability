import hashlib
import sys
from pathlib import Path

from tests.fixtures.remap_profile import build_ubuntu_workspace_profile

from cursor_portability.cli.main import main


def _tree_hash(root: Path) -> str:
    lines = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            lines.append(f"{p.relative_to(root)} {st.st_size} {st.st_mtime_ns}")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def test_census_is_read_only(fake_home, tmp_path) -> None:
    if sys.platform != "darwin":
        return
    ud = fake_home / "Library" / "Application Support" / "Cursor"
    ubuntu_id = "a" * 32
    mac_proj = tmp_path / "Projects" / "demo"
    build_ubuntu_workspace_profile(
        ud,
        ubuntu_id,
        "/home/u/Projects/demo",
        mac_proj,
    )
    before = _tree_hash(ud)
    assert (
        main(
            [
                "workspaces",
                "census",
                "--from",
                "/home/u/Projects",
                "--to",
                str(tmp_path / "Projects"),
                "--target-profile",
                str(ud),
                "--json",
            ]
        )
        == 0
    )
    assert before == _tree_hash(ud)
