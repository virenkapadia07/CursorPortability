import pytest

from cursor_portability.errors import AppError
from cursor_portability.security.archive_validation import validate_archive_member_name


def test_rejects_traversal() -> None:
    with pytest.raises(AppError):
        validate_archive_member_name("../etc/passwd")


def test_accepts_relative() -> None:
    validate_archive_member_name("user-data/User/settings.json")
