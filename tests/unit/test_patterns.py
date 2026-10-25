import pytest

from cursor_portability.schemas.keyshape import key_shape, shareable_key_shape
from cursor_portability.security.credential_patterns import is_credential_key


def test_credential_key_detection() -> None:
    assert is_credential_key("secret://foo")
    assert is_credential_key("cursorAuth/accessToken")
    assert not is_credential_key("workbench.panel.aichat")


def test_key_shape_redacts() -> None:
    s = key_shape("bubbleId:550e8400-e29b-41d4-a716-446655440000")
    assert "550e8400" not in s
    assert "<uuid>" in s


@pytest.mark.parametrize(
    ("key", "shape"),
    [
        (
            "branchMetadata.prUrl.v1::github.com/acme/widgets::feat-ABC-12-secret",
            "branchMetadata.prUrl.v1::<text>",
        ),
        (
            "bubbleId:550e8400-e29b-41d4-a716-446655440000:toolu_012XUaYjwdhGVaKNRpbnSEJe",
            "bubbleId:<uuid>:<id>",
        ),
        (
            "bubbleId:550e8400-e29b-41d4-a716-446655440000:tool_639c4764-a1b2",
            "bubbleId:<uuid>:<id>",
        ),
        ("workbench.view.extension.My Notes", "workbench.view.extension.<text>"),
        ("aiCodeTracking.dailyStats.v1.2026.10-25", "aiCodeTracking.dailyStats.v1.<n>.<n>-<n>"),
        ("terminal.integrated.environmentVariableCollectionsV2", None),
        ("anysphere.cursor-agent-host", None),
        ("__$__targetStorageMarker", None),
        ("~remote.forwardedPortsContainer", None),
        (
            "ofsContent:550e8400-e29b-41d4-a716-446655440000:file:/x/y",
            "ofsContent:<uuid>:file:<path>",
        ),
    ],
)
def test_shareable_key_shape(key: str, shape: str | None) -> None:
    assert shareable_key_shape(key) == (key if shape is None else shape)
