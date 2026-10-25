from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import ParseResult, unquote, urlparse

_REMOTE_SCHEMES = frozenset(
    {
        "vscode-remote",
        "vscode-remote-ssh",
        "vscode-remote-wsl",
        "ssh-remote",
        "wsl",
    }
)


class UriKind(StrEnum):
    FILE = "file"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ParsedUri:
    kind: UriKind
    original: str
    # Native path string for file URIs (POSIX on Unix, drive/UNC on Windows).
    native_path: str | None = None
    message: str | None = None


def parse_workspace_uri(uri: str) -> ParsedUri:
    uri = uri.strip()
    if not uri:
        return ParsedUri(UriKind.UNSUPPORTED, uri, message="empty uri")
    if uri.endswith(".code-workspace") or "workspace" in uri and not uri.startswith("file:"):
        return ParsedUri(UriKind.UNSUPPORTED, uri, message="workspace file uri not supported")
    parsed = urlparse(uri)
    scheme = (parsed.scheme or "").lower()
    if scheme in _REMOTE_SCHEMES or scheme.startswith("vscode-remote"):
        return ParsedUri(UriKind.UNSUPPORTED, uri, message="remote workspace uri")
    if scheme != "file":
        return ParsedUri(UriKind.UNSUPPORTED, uri, message=f"unsupported scheme: {scheme or '(none)'}")
    return _parse_file_uri(uri, parsed)


def _parse_file_uri(original: str, parsed: ParseResult) -> ParsedUri:
    path = unquote(parsed.path or "")
    if not path:
        return ParsedUri(UriKind.UNSUPPORTED, original, message="empty file path")
    host = (parsed.netloc or "").lower()
    if host and host not in ("", "localhost"):
        # UNC: file://server/share/...
        if path.startswith("/"):
            native = f"\\\\{host}{path.replace('/', '\\')}"
        else:
            native = f"\\\\{host}\\{path}"
        return ParsedUri(UriKind.FILE, original, native_path=_normalize_windows(native))
    # file:///c%3A/foo or file:///tmp/foo
    if re.match(r"^/[a-zA-Z]:", path):
        native = path[1:].replace("/", "\\")
        return ParsedUri(UriKind.FILE, original, native_path=_normalize_windows(native))
    if path.startswith("//"):
        native = "\\\\" + path[2:].replace("/", "\\")
        return ParsedUri(UriKind.FILE, original, native_path=_normalize_windows(native))
    return ParsedUri(UriKind.FILE, original, native_path=path)


def _normalize_windows(path: str) -> str:
    return str(PureWindowsPath(path))


def path_to_file_uri(path: Path, dest_os: str) -> str:
    """Build a file URI as Cursor on dest_os would store it in workspace.json."""
    resolved = path.expanduser().resolve()
    if dest_os == "win32":
        s = str(resolved)
        if s.startswith("\\\\"):
            # UNC \\server\share\path
            parts = s[2:].split("\\", 1)
            if len(parts) == 2:
                server, rest = parts
                posix = "/" + rest.replace("\\", "/")
                from urllib.parse import quote

                return f"file://{quote(server, safe='')}{quote(posix, safe='/')}"
        drive = s[0:2] if len(s) >= 2 and s[1] == ":" else ""
        if drive:
            rest = s[2:].replace("\\", "/")
            from urllib.parse import quote

            return f"file:///{quote(drive + rest, safe='/')}"
    # POSIX
    from urllib.parse import quote

    posix = resolved.as_posix()
    return f"file://{quote(posix, safe='/')}"


def path_key_from_native(native: str, source_os: str) -> tuple[str, ...]:
    """Path components for comparison; case-fold when source_os is case-insensitive."""
    if source_os == "win32":
        parts = PureWindowsPath(native).parts
        return tuple(p.casefold() for p in parts)
    parts = PurePosixPath(native.replace("\\", "/")).parts
    if source_os == "darwin":
        return tuple(p.casefold() for p in parts)
    return tuple(parts)
