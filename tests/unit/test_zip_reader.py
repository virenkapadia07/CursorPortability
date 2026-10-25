import json
import stat
import warnings
import zipfile
from pathlib import Path

import pytest
from tests.fixtures.db import make_kv_db

from cursor_portability.bundle.manifest import parse_manifest
from cursor_portability.bundle.reader import ZipBundle
from cursor_portability.errors import AppError, ErrorCode
from cursor_portability.security.scrub import any_credential_keys_remain
from cursor_portability.storage.paths import BundlePath
from cursor_portability.storage.sqlite_conn import open_readonly_db


def _symlink_entry() -> zipfile.ZipInfo:
    info = zipfile.ZipInfo("user-data/link")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


@pytest.mark.parametrize(
    "entries",
    [
        [("../evil.txt", b"x")],
        [("dir\\file.txt", b"x")],
        [("a.txt", b"x"), ("a.txt", b"y")],
        [("A.txt", b"x"), ("a.txt", b"y")],
        [(_symlink_entry(), b"/etc/passwd")],
        [("big.bin", b"\0" * (2 * 1024 * 1024))],
    ],
    ids=["traversal", "backslash", "duplicate", "case-collision", "symlink", "ratio"],
)
def test_rejects_unsafe_archives(tmp_path: Path, entries) -> None:
    path = tmp_path / "bad.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in entries:
                zf.writestr(name, data)
    with pytest.raises(AppError) as err, ZipBundle(path):
        pass
    assert err.value.code == ErrorCode.ARCHIVE_REJECTED


def test_small_compressible_entry_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "ok.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("zeros.bin", b"\0" * 4096)
    with ZipBundle(path) as bundle:
        assert bundle.total_uncompressed == 4096
        dest = tmp_path / "zeros.bin"
        bundle.extract_member("zeros.bin", dest)
        assert dest.read_bytes() == b"\0" * 4096


def test_manifest_rejects_traversal_path() -> None:
    text = json.dumps(
        {"bundle_format_major": 1, "files": [{"path": "../x", "sha256": "0" * 64, "size": 1}]}
    )
    with pytest.raises(AppError) as err:
        parse_manifest(text)
    assert err.value.code == ErrorCode.ARCHIVE_REJECTED


def test_credential_check_scans_every_key(tmp_path: Path) -> None:
    db = tmp_path / "big.sqlite"
    make_kv_db(db, [(f"k{i}", b"v") for i in range(10_050)] + [("late.auth.token", b"x")])
    with open_readonly_db(BundlePath(db)) as conn:
        assert any_credential_keys_remain(conn)
