import os
import shutil
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from cursor_portability.migration.profile_writer import write_text_atomic
from cursor_portability.security.tripwire import allow_profile_writes, install_tripwire


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    root = tmp_path / "profile"
    (root / "User").mkdir(parents=True)
    (root / "User" / "settings.json").write_text("{}", encoding="utf-8")
    return root


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    path = tmp_path / "outside.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    return path


_WRITES: dict[str, Callable[[Path, Path], object]] = {
    "open w": lambda p, _: open(p / "User/settings.json", "w").close(),  # noqa: SIM115
    "open a": lambda p, _: open(p / "User/settings.json", "a").close(),  # noqa: SIM115
    "open r+": lambda p, _: open(p / "User/settings.json", "r+").close(),  # noqa: SIM115
    "os.open O_CREAT": lambda p, _: os.close(os.open(p / "User/new.json", os.O_CREAT, 0o600)),
    "Path.write_text": lambda p, _: (p / "User/new.json").write_text("x"),
    "os.replace into": lambda p, o: os.replace(o, p / "User/settings.json"),
    "os.replace out of": lambda p, o: os.replace(p / "User/settings.json", o),
    "unlink": lambda p, _: (p / "User/settings.json").unlink(),
    "mkdir": lambda p, _: (p / "User/snippets").mkdir(),
    "shutil.copy2 into": lambda p, o: shutil.copy2(o, p / "User/copy.json"),
    "shutil.rmtree": lambda p, _: shutil.rmtree(p / "User"),
    "chmod": lambda p, _: os.chmod(p / "User/settings.json", 0o600),
    "utime": lambda p, _: os.utime(p / "User/settings.json"),
    "symlink": lambda p, o: os.symlink(o, p / "User/link.json"),
}


@pytest.mark.parametrize("name", sorted(_WRITES))
def test_writes_under_protected_root_are_blocked(profile: Path, outside: Path, name: str) -> None:
    install_tripwire((profile,))
    with pytest.raises(RuntimeError, match="tripwire"):
        _WRITES[name](profile, outside)
    assert (profile / "User/settings.json").read_text(encoding="utf-8") == "{}"
    assert sorted(p.name for p in (profile / "User").iterdir()) == ["settings.json"]


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
def test_junction_into_protected_root_is_blocked(profile: Path, tmp_path: Path) -> None:
    import _winapi

    install_tripwire((profile,))
    with pytest.raises(RuntimeError, match="tripwire"):
        _winapi.CreateJunction(str(tmp_path), str(profile / "User" / "junction"))
    assert not (profile / "User" / "junction").exists()


def test_reads_and_outside_writes_are_allowed(profile: Path, tmp_path: Path) -> None:
    install_tripwire((profile,))
    assert (profile / "User/settings.json").read_text(encoding="utf-8") == "{}"
    assert os.listdir(profile / "User") == ["settings.json"]
    shutil.copy2(profile / "User/settings.json", tmp_path / "copy.json")
    (tmp_path / "copy.json").write_text("changed", encoding="utf-8")


@pytest.mark.parametrize(
    "form",
    ["plain", "uri", "uri_authority", "uri_localhost", "uri_percent"],
)
def test_sqlite_opens_of_protected_paths_are_blocked(profile: Path, form: str) -> None:
    db = profile / "User" / "state db.vscdb"
    sqlite3.connect(db).close()
    posix = db.as_posix()
    rooted = posix if posix.startswith("/") else f"/{posix}"
    database = {
        "plain": str(db),
        "uri": f"file:{posix}?mode=ro&immutable=1",
        "uri_authority": f"file://{rooted}?mode=ro",
        "uri_localhost": f"file://localhost{rooted}",
        "uri_percent": f"file:{posix.replace(' ', '%20')}?mode=ro",
    }[form]
    install_tripwire((profile,))
    with pytest.raises(RuntimeError, match="sqlite3.connect"):
        sqlite3.connect(database, uri=form != "plain")


def test_sqlite_opens_outside_protected_roots_are_allowed(profile: Path, tmp_path: Path) -> None:
    install_tripwire((profile,))
    sqlite3.connect(":memory:").close()
    sqlite3.connect("file::memory:", uri=True).close()
    staged = tmp_path / "staged.vscdb"
    sqlite3.connect(f"file:{staged.as_posix()}?mode=rwc", uri=True).close()
    assert staged.is_file()


def test_detect_mode_blocks_reads(profile: Path) -> None:
    install_tripwire((profile,), mode="detect_only")
    with pytest.raises(RuntimeError, match="detect mode"):
        (profile / "User/settings.json").read_text(encoding="utf-8")


def test_apply_window_allows_only_planned_targets(profile: Path) -> None:
    install_tripwire((profile,))
    settings = profile / "User/settings.json"
    snippet = profile / "User/snippets/python.json"
    with allow_profile_writes(profile, [settings, snippet]):
        write_text_atomic(settings, '{"b": 2}')
        write_text_atomic(snippet, "{}")
        settings.unlink()
        with pytest.raises(RuntimeError, match="tripwire"):
            (profile / "User/other.json").write_text("x")
        with pytest.raises(RuntimeError, match="tripwire"):
            (profile / "User/settings.json.bak").write_text("x")
    assert snippet.read_text(encoding="utf-8") == "{}"
    assert not list(profile.rglob("*.partial-*"))
    with pytest.raises(RuntimeError, match="tripwire"):
        snippet.write_text("again")


def test_apply_window_protects_its_profile_root(profile: Path, tmp_path: Path) -> None:
    install_tripwire(())
    settings = profile / "User/settings.json"
    with allow_profile_writes(profile, [settings]):
        write_text_atomic(settings, "[]")
        with pytest.raises(RuntimeError, match="tripwire"):
            (profile / "User/other.json").write_text("x")
    (profile / "User/other.json").write_text("x")


def test_apply_window_is_inert_without_tripwire(profile: Path) -> None:
    with allow_profile_writes(profile, []):
        (profile / "User/other.json").write_text("x")


def test_failed_atomic_write_leaves_no_partial_file(
    profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_replace(src: object, dst: object) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        write_text_atomic(profile / "User/settings.json", '{"new": true}')
    assert (profile / "User/settings.json").read_text(encoding="utf-8") == "{}"
    assert not list(profile.rglob("*.partial-*"))
