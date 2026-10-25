from __future__ import annotations

import json
import math
from typing import Any


class StrictJsonError(ValueError):
    pass


_MAX_DEPTH = 64
_MAX_STRING = 10 * 1024 * 1024


def loads_strict(text: str, max_size: int = 2 * 1024 * 1024) -> Any:
    if len(text) > max_size:
        raise StrictJsonError("json too large")
    return json.loads(text, parse_constant=_reject_constants, object_pairs_hook=_reject_duplicates)


def _reject_constants(const: str) -> None:
    raise StrictJsonError(f"invalid constant: {const}")


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise StrictJsonError("duplicate key")
        out[k] = _check_value(v, 1)
    return out


def _check_value(v: Any, depth: int) -> Any:
    if depth > _MAX_DEPTH:
        raise StrictJsonError("max depth exceeded")
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        raise StrictJsonError("non-finite float")
    if isinstance(v, str) and len(v) > _MAX_STRING:
        raise StrictJsonError("string too long")
    if isinstance(v, dict):
        return {k: _check_value(x, depth + 1) for k, x in v.items()}
    if isinstance(v, list):
        return [_check_value(x, depth + 1) for x in v]
    return v


def dumps_canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)
