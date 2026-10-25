import pytest

from cursor_portability.workspace.path_needles import prefix_needle_variants


@pytest.mark.parametrize(
    ("prefix", "source_os", "uri"),
    [
        ("/home/u/Projects", "linux", "file:///home/u/Projects"),
        ("/home/u/My Projects", "linux", "file:///home/u/My%20Projects"),
        ("C:\\Users\\u\\Projects", "win32", "file:///c%3A/Users/u/Projects"),
        ("\\\\server\\share\\Projects", "win32", "file://server/share/Projects"),
    ],
)
def test_file_uri_needle_is_built_from_the_source_path_as_given(
    prefix: str, source_os: str, uri: str
) -> None:
    assert prefix_needle_variants(prefix, source_os)["file_uri"] == uri
