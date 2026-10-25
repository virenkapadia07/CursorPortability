from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

_MAX_SIZE = 2 * 1024 * 1024
_LITERAL_STOP = frozenset(' \t\r\n{}[],:"/')


class JsoncError(ValueError):
    pass


@dataclass(frozen=True)
class _Token:
    kind: str  # one of { } [ ] , : or "str" / "lit"
    start: int
    end: int


@dataclass(frozen=True)
class JsoncDocument:
    text: str
    value: Any
    close_index: int  # index of top-level closing } or ]
    tokens: tuple[_Token, ...] = field(repr=False, default=())


def parse_jsonc(text: str, max_size: int = _MAX_SIZE) -> JsoncDocument:
    if len(text) > max_size:
        raise JsoncError("jsonc too large")
    tokens = _tokenize(text)
    if not tokens or tokens[0].kind not in "{[":
        raise JsoncError("top-level value must be an object or array")

    def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in pairs:
            if k in out:
                raise JsoncError("duplicate key")
            out[k] = v
        return out

    try:
        value = json.loads(
            _strict_json(text, tokens), parse_constant=_reject, object_pairs_hook=_pairs
        )
    except json.JSONDecodeError as exc:
        raise JsoncError(str(exc)) from exc
    return JsoncDocument(text, value, tokens[-1].start, tuple(tokens))


def _reject(const: str) -> None:
    raise JsoncError(f"invalid constant: {const}")


def _tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    n = len(text)
    i = 0
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif text.startswith("//", i):
            while i < n and text[i] not in "\r\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise JsoncError("unterminated block comment")
            i = end + 2
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            if j >= n:
                raise JsoncError("unterminated string")
            tokens.append(_Token("str", i, j + 1))
            i = j + 1
        elif c in "{}[],:":
            tokens.append(_Token(c, i, i + 1))
            i += 1
        else:
            j = i
            while j < n and text[j] not in _LITERAL_STOP:
                j += 1
            if j == i:
                raise JsoncError(f"unexpected character at offset {i}")
            tokens.append(_Token("lit", i, j))
            i = j
    return tokens


def _strict_json(text: str, tokens: list[_Token]) -> str:
    """Rebuild the document without comments or trailing commas."""
    parts: list[str] = []
    for idx, tok in enumerate(tokens):
        if tok.kind == "," and idx + 1 < len(tokens) and tokens[idx + 1].kind in "}]":
            continue
        parts.append(text[tok.start : tok.end])
    return " ".join(parts)


def _value_end(tokens: tuple[_Token, ...], idx: int) -> int:
    if tokens[idx].kind not in "{[":
        return idx
    depth = 0
    for j in range(idx, len(tokens)):
        kind = tokens[j].kind
        if kind in "{[":
            depth += 1
        elif kind in "}]":
            depth -= 1
            if depth == 0:
                return j
    raise JsoncError("unbalanced brackets")


def _member_value_spans(doc: JsoncDocument) -> dict[str, tuple[int, int]]:
    tokens = doc.tokens
    spans: dict[str, tuple[int, int]] = {}
    idx = 1
    close = len(tokens) - 1
    while idx < close:
        tok = tokens[idx]
        if tok.kind == ",":
            idx += 1
            continue
        if tok.kind != "str" or tokens[idx + 1].kind != ":":
            raise JsoncError("unexpected token in object")
        key = json.loads(doc.text[tok.start : tok.end])
        end = _value_end(tokens, idx + 2)
        spans[key] = (tokens[idx + 2].start, tokens[end].end)
        idx = end + 1
    return spans


def _line_start(text: str, pos: int) -> int:
    return max(text.rfind("\n", 0, pos), text.rfind("\r", 0, pos)) + 1


def _line_end(text: str, pos: int) -> int:
    ends = [i for i in (text.find("\n", pos), text.find("\r", pos)) if i >= 0]
    return min(ends) if ends else len(text)


def _leading_whitespace(text: str, pos: int) -> str:
    start = _line_start(text, pos)
    line = text[start:pos]
    return line if not line.strip() else line[: len(line) - len(line.lstrip())]


def _item_indent(doc: JsoncDocument) -> str:
    tokens = doc.tokens
    first = tokens[1]
    if first.kind not in "}]":
        start = _line_start(doc.text, first.start)
        if start < first.start and not doc.text[start : first.start].strip():
            return doc.text[start : first.start]
    return _leading_whitespace(doc.text, tokens[0].start) + "    "


def _insert_items(doc: JsoncDocument, rendered: list[str]) -> str:
    if not rendered:
        return doc.text
    text = doc.text
    tokens = doc.tokens
    indent = _item_indent(doc)
    lines = "".join(
        ("," if idx else "") + "\n" + indent + item for idx, item in enumerate(rendered)
    )
    open_tok, last, close = tokens[0], tokens[-2], tokens[-1]
    if last is open_tok:
        gap = text[open_tok.end : close.start]
        if not gap.strip():
            base = _leading_whitespace(text, open_tok.start)
            return text[: open_tok.end] + lines + "\n" + base + text[close.start :]
        return text[: open_tok.end] + lines + text[open_tok.end :]
    sep = "" if last.kind == "," else ","
    pos = last.end
    line_end = _line_end(text, pos)
    rest = text[pos:line_end].strip()
    if line_end < close.start and (not rest or rest.startswith("//")):
        pos = line_end
    return text[: last.end] + sep + text[last.end : pos] + lines + text[pos:]


def insert_object_entries(doc: JsoncDocument, new_entries: dict[str, Any]) -> str:
    if not isinstance(doc.value, dict):
        raise JsoncError("expected object")
    rendered = [
        f"{json.dumps(key, ensure_ascii=False)}: {json.dumps(val, ensure_ascii=False)}"
        for key, val in new_entries.items()
    ]
    return _insert_items(doc, rendered)


def insert_array_entries(doc: JsoncDocument, new_items: list[Any]) -> str:
    if not isinstance(doc.value, list):
        raise JsoncError("expected array")
    return _insert_items(doc, [json.dumps(item, ensure_ascii=False) for item in new_items])


def replace_object_values(doc: JsoncDocument, replacements: dict[str, Any]) -> str:
    if not isinstance(doc.value, dict):
        raise JsoncError("expected object")
    spans = _member_value_spans(doc)
    missing = [key for key in replacements if key not in spans]
    if missing:
        raise JsoncError("replacement key not present in object")
    text = doc.text
    for key in sorted(replacements, key=lambda k: spans[k][0], reverse=True):
        start, end = spans[key]
        text = text[:start] + json.dumps(replacements[key], ensure_ascii=False) + text[end:]
    return text
