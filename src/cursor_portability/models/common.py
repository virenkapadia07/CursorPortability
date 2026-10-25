"""Deterministic JSON serialization."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any


def to_canonical_json(obj: Any) -> str:
    """Serialize to sorted-keys JSON with stable formatting."""

    def default(o: Any) -> Any:
        if is_dataclass(o) and not isinstance(o, type):
            return asdict(o)
        if isinstance(o, set):
            return sorted(o)
        raise TypeError(f"not JSON serializable: {type(o)!r}")

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=default)
