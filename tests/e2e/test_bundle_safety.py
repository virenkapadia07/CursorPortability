import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from tests.fixtures.db import make_kv_db

from cursor_portability.bundle.restore import restore_bundle
from cursor_portability.bundle.verify import VerifyReport, verify_bundle
from cursor_portability.cli.main import main
from cursor_portability.errors import AppError, ErrorCode
from cursor_portability.models.enums import ValidationOutcome
from cursor_portability.platform.env import current_environment
from cursor_portability.services.backup import create_backup

GLOBAL_DB = "user-data/User/globalStorage/state.sqlite"


def _yes(_: Path) -> bool:
    return True


def _never_ask(path: Path) -> bool:
    raise AssertionError(f"must refuse before asking about {path}")


def _rewrite_zip(src: Path, dest: Path, replace: dict[str, bytes]) -> None:
    """Swap member contents and fix up manifest hashes so only content checks can fail."""
    with zipfile.ZipFile(src) as zf:
        entries = {n: zf.read(n) for n in zf.namelist()}
    manifest = json.loads(entries["manifest.json"])
    for name, data in replace.items():
        entries[name] = data
        for fe in manifest["files"]:
            if fe["path"] == name:
                fe["sha256"] = hashlib.sha256(data).hexdigest()
                fe["size"] = len(data)
    entries["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


@pytest.fixture
def zip_bundle(synthetic_profile, cursor_not_running, tmp_path) -> Path:
    report = create_backup(current_environment(), tmp_path / "bundle", as_zip=True)
    return Path(report.bundle_path)


def test_zip_verify_detects_credential_keys(zip_bundle, tmp_path) -> None:
    leaked = tmp_path / "leaked.sqlite"
    make_kv_db(leaked, [("hello.key", b"world"), ("auth.token", b"secret")])
    tampered = tmp_path / "tampered.zip"
    _rewrite_zip(zip_bundle, tampered, {GLOBAL_DB: leaked.read_bytes()})
    report = verify_bundle(tampered)
    assert report.outcome == ValidationOutcome.FAIL
    assert any("credential keys remain" in e for e in report.errors), report.errors


def test_zip_verify_detects_corrupt_database(zip_bundle, tmp_path) -> None:
    tampered = tmp_path / "tampered.zip"
    _rewrite_zip(zip_bundle, tampered, {GLOBAL_DB: b"not a database" * 512})
    report = verify_bundle(tampered)
    assert report.outcome == ValidationOutcome.FAIL
    assert any(GLOBAL_DB in e for e in report.errors), report.errors


def test_zip_verify_detects_unmanifested_member(zip_bundle, tmp_path) -> None:
    tampered = tmp_path / "tampered.zip"
    _rewrite_zip(zip_bundle, tampered, {"user-data/extra.txt": b"x"})
    report = verify_bundle(tampered)
    assert report.outcome == ValidationOutcome.FAIL
    assert "unmanifested files: 1" in report.errors


def test_restore_rejects_bomb_before_extracting(tmp_path) -> None:
    bomb = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", b"{}")
        zf.writestr("big.bin", b"\0" * (4 * 1024 * 1024))
    with pytest.raises(AppError) as err:
        restore_bundle(bomb, tmp_path / "restored")
    assert err.value.code == ErrorCode.ARCHIVE_REJECTED
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bomb.zip"]


def test_restore_checks_disk_space_before_extracting(zip_bundle, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("cursor_portability.bundle.restore.ensure_space", lambda _p, _n: False)
    with pytest.raises(AppError) as err:
        restore_bundle(zip_bundle, tmp_path / "restored")
    assert err.value.code == ErrorCode.INSUFFICIENT_DISK_SPACE
    assert not any(p.name.startswith("restored") for p in tmp_path.iterdir())


def _fail_verify(_path: Path) -> VerifyReport:
    return VerifyReport(ValidationOutcome.FAIL, ("forced",), 0)


@pytest.mark.parametrize("as_zip", [False, True])
def test_failed_verify_leaves_no_artifact(
    synthetic_profile, cursor_not_running, tmp_path, monkeypatch, as_zip
) -> None:
    monkeypatch.setattr("cursor_portability.services.backup.verify_bundle", _fail_verify)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    with pytest.raises(AppError) as err:
        create_backup(current_environment(), out_dir / "bundle", as_zip=as_zip)
    assert err.value.code == ErrorCode.BACKUP_INVALID
    assert list(out_dir.iterdir()) == []


def test_failed_overwrite_keeps_previous_bundle(
    synthetic_profile, cursor_not_running, tmp_path, monkeypatch
) -> None:
    out = tmp_path / "bundle"
    create_backup(current_environment(), out)
    before = (out / "manifest.json").read_bytes()
    monkeypatch.setattr("cursor_portability.services.backup.verify_bundle", _fail_verify)
    with pytest.raises(AppError):
        create_backup(current_environment(), out, overwrite=True, confirm_overwrite=_yes)
    assert (out / "manifest.json").read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bundle", "home"]


def test_overwrite_replaces_bundle_without_leftovers(
    synthetic_profile, cursor_not_running, tmp_path
) -> None:
    out = tmp_path / "bundle.zip"
    create_backup(current_environment(), out, as_zip=True)
    create_backup(current_environment(), out, as_zip=True, overwrite=True, confirm_overwrite=_yes)
    assert verify_bundle(out).outcome == ValidationOutcome.PASS
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bundle.zip", "home"]


def test_overwrite_refuses_non_bundle(synthetic_profile, cursor_not_running, tmp_path) -> None:
    precious = tmp_path / "documents"
    precious.mkdir()
    (precious / "notes.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(AppError) as err:
        create_backup(current_environment(), precious, overwrite=True, confirm_overwrite=_never_ask)
    assert err.value.code == ErrorCode.OUTPUT_EXISTS
    assert (precious / "notes.txt").read_text(encoding="utf-8") == "keep"


def test_overwrite_without_confirmation_is_refused(
    synthetic_profile, cursor_not_running, tmp_path
) -> None:
    out = tmp_path / "bundle"
    create_backup(current_environment(), out)
    before = (out / "manifest.json").read_bytes()
    with pytest.raises(AppError, match="overwrite not confirmed"):
        create_backup(current_environment(), out, overwrite=True)
    assert (out / "manifest.json").read_bytes() == before


class _FakeStdin(io.StringIO):
    def __init__(self, text: str, tty: bool) -> None:
        super().__init__(text)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.mark.parametrize(
    ("extra_args", "typed", "tty", "replaced"),
    [
        (["--yes"], "", False, True),
        ([], "yes\n", True, True),
        ([], "y\n", True, False),
        ([], "", False, False),
    ],
)
def test_cli_overwrite_asks_before_replacing(
    synthetic_profile,
    cursor_not_running,
    tmp_path,
    monkeypatch,
    capsys,
    extra_args: list[str],
    typed: str,
    tty: bool,
    replaced: bool,
) -> None:
    out = tmp_path / "bundle"
    assert main(["backup", "create", "--output", str(out)]) == 0
    before = (out / "manifest.json").read_bytes()
    capsys.readouterr()
    monkeypatch.setattr(sys, "stdin", _FakeStdin(typed, tty))

    code = main(["backup", "create", "--output", str(out), "--overwrite", *extra_args])

    assert (code == 0) is replaced
    assert ((out / "manifest.json").read_bytes() != before) is replaced
    err = capsys.readouterr().err
    assert ("Type 'yes' to confirm" in err) is (tty and not extra_args)
    if not tty and not extra_args:
        assert "run in a terminal or add --yes" in err


def test_zip_output_name_collision_is_detected(
    synthetic_profile, cursor_not_running, tmp_path
) -> None:
    existing = tmp_path / "bundle.zip"
    existing.write_bytes(b"existing")
    with pytest.raises(AppError) as err:
        create_backup(current_environment(), tmp_path / "bundle", as_zip=True)
    assert err.value.code == ErrorCode.OUTPUT_EXISTS
    assert existing.read_bytes() == b"existing"
