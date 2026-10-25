from __future__ import annotations

import sys
from typing import Any

from cursor_portability.models.common import to_canonical_json


def emit_json(data: Any) -> None:
    sys.stdout.write(to_canonical_json(data) + "\n")


def emit_text(line: str) -> None:
    sys.stdout.write(line + "\n")


def emit_error(line: str) -> None:
    sys.stderr.write(line + "\n")
