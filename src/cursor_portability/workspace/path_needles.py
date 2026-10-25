from __future__ import annotations

import json
from urllib.parse import quote


def _source_file_uri(normalized: str, source_os: str) -> str:
    """File URI for a path on the source machine; never resolved against this filesystem."""
    if source_os == "win32":
        if normalized.startswith("//"):
            host, _, rest = normalized[2:].partition("/")
            return f"file://{host}/{quote(rest, safe='/')}"
        if normalized[1:2] == ":":
            return f"file:///{quote(normalized[0].lower() + normalized[1:], safe='/')}"
    return f"file://{quote(normalized, safe='/')}"


def prefix_needle_variants(prefix: str, source_os: str) -> dict[str, str]:
    """Labels for the four encoding forms checked during a path census."""
    normalized = prefix.replace("\\", "/")
    file_uri = _source_file_uri(normalized, source_os)
    return {
        "raw": normalized,
        "file_uri": file_uri,
        "percent_encoded": quote(normalized, safe="/"),
        "json_escaped": json.dumps(normalized)[1:-1],
    }


def value_contains_any(text: str, needles: dict[str, str]) -> list[str]:
    hits: list[str] = []
    for label, needle in needles.items():
        if needle and needle in text:
            hits.append(label)
    return hits
