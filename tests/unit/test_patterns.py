from cursor_portability.schemas.keyshape import key_shape
from cursor_portability.security.credential_patterns import is_credential_key


def test_credential_key_detection() -> None:
    assert is_credential_key("secret://foo")
    assert is_credential_key("cursorAuth/accessToken")
    assert not is_credential_key("workbench.panel.aichat")


def test_key_shape_redacts() -> None:
    s = key_shape("bubbleId:550e8400-e29b-41d4-a716-446655440000")
    assert "550e8400" not in s
    assert "<uuid>" in s
