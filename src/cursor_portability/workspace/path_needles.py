from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from cursor_portability.planning.uri import path_to_file_uri


def prefix_needle_variants(prefix: str, source_os: str) -> dict[str, str]:
    """Labels for the four encoding forms checked during a path census."""
    normalized = prefix.replace("\\", "/")
    file_uri = path_to_file_uri(Path(normalized), source_os)
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
