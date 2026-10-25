from __future__ import annotations

import re

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_HEX = re.compile(r"\b[0-9a-fA-F]{16,}\b")
_NUM = re.compile(r"\b\d+\b")
_PATH = re.compile(r"(/|\\)[\w ./\-]+")


def key_shape(key: str) -> str:
    s = key
    s = _UUID.sub("<uuid>", s)
    s = _PATH.sub("<path>", s)
    s = _HEX.sub("<hex>", s)
    s = _NUM.sub("<n>", s)
    return s
