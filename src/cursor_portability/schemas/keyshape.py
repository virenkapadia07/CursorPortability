from __future__ import annotations

import re

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_HEX = re.compile(r"\b[0-9a-fA-F]{16,}\b")
_NUM = re.compile(r"\b\d+\b")
_PATH = re.compile(r"(/|\\)[\w ./\-]+")
_PLACEHOLDER = re.compile(r"(<uuid>|<path>|<hex>|<n>)")
_NAME_TEXT = re.compile(r"[A-Za-z0-9_$~-]{0,48}")


def key_shape(key: str) -> str:
    s = key
    s = _UUID.sub("<uuid>", s)
    s = _PATH.sub("<path>", s)
    s = _HEX.sub("<hex>", s)
    s = _NUM.sub("<n>", s)
    return s


def _shareable_segment(segment: str) -> str:
    text = "".join(_PLACEHOLDER.split(segment)[::2])
    if not _NAME_TEXT.fullmatch(text):
        return "<text>"
    digits = sum(ch.isdigit() for ch in text)
    if digits >= 4 or (digits >= 2 and len(text) >= 16):
        return "<id>"
    return segment


def shareable_key_shape(key: str) -> str:
    """`key_shape` for output that leaves the machine.

    Dot/colon segments that are not name-like (spaces, other punctuation, digit-heavy random
    IDs) are generalised, and everything after the first `::` (branch names in PR-link keys)
    is dropped. Name-like free text in a single segment cannot be told apart and is kept.
    """
    head, sep, _tail = key_shape(key).partition("::")
    parts = re.split(r"([.:])", head)
    shaped = "".join(p if p in (".", ":") else _shareable_segment(p) for p in parts)
    return f"{shaped}::<text>" if sep else shaped
