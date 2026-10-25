from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cursor_portability.models.enums import DetectionStatus
from cursor_portability.schemas.adapters.cursor_composer_headers import COMPOSER_HEADERS_TABLE
from cursor_portability.schemas.keyshape import key_shape, shareable_key_shape
from cursor_portability.schemas.registry import select_adapter
from cursor_portability.storage.paths import StagedPath
from cursor_portability.storage.sqlite_conn import open_readonly_db, safe_table_name
from cursor_portability.workspace.path_needles import prefix_needle_variants, value_contains_any

_KV_TABLES = frozenset({"ItemTable", "cursorDiskKV"})
_CHAT_KEY_HINTS = ("composer", "conversation", "chat", "aichat")

# Field report bounds: rows parsed per group, nesting followed, distinct fields kept.
_FIELD_SAMPLE_ROWS = 500
_FIELD_MAX_DEPTH = 16
_FIELD_MAX_REPORTED = 40
_FIELD_MAX_PARSE_CHARS = 8_000_000
_FIELD_NAME_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]{0,47}")
# Chat-link report: rows parsed per group.
_LINK_MAX_ROWS = 2000
_COMPOSER_DATA_PREFIX = "composerData:"


@dataclass(frozen=True)
class PathHitSummary:
    table: str
    column: str
    encoding_hits: dict[str, int]
    key_patterns: dict[str, int] | None = None
    # Per key shape (KV tables) or per column: which JSON fields hold the prefix.
    fields: dict[str, dict[str, Any]] | None = None


def _field_name(key: str) -> str:
    """Object key as reported: schema-like names kept; paths, IDs and free text generalised."""
    if not _FIELD_NAME_RE.fullmatch(key):
        return "<key>"
    if sum(ch.isdigit() for ch in key) >= 4:
        return "<id>"
    return key


def _maybe_json(text: str) -> Any | None:
    if text.lstrip()[:1] not in ("{", "["):
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _leaf_kind(text: str, needles: dict[str, str]) -> str | None:
    if not value_contains_any(text, needles):
        return None
    if text.startswith(needles["file_uri"]):
        return "file_uri"
    if any(text.startswith(n) for label, n in needles.items() if label != "file_uri" and n):
        return "path"
    return "embedded"


def _hit_fields(value: Any, needles: dict[str, str], where: str, out: set[str], depth: int) -> None:
    if depth > _FIELD_MAX_DEPTH:
        if value_contains_any(json.dumps(value), needles):
            out.add(f"{where} (deeper)")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            name = _field_name(str(key))
            if value_contains_any(str(key), needles):
                out.add(f"{where}.<key> (in key)" if where else "<key> (in key)")
            _hit_fields(item, needles, f"{where}.{name}" if where else name, out, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _hit_fields(item, needles, f"{where}[]", out, depth + 1)
    elif isinstance(value, str):
        kind = _leaf_kind(value, needles)
        if kind is None:
            return
        nested = _maybe_json(value) if len(value) <= _FIELD_MAX_PARSE_CHARS else None
        if nested is not None:
            _hit_fields(nested, needles, f"{where}<json>", out, depth + 1)
        else:
            out.add(f"{where or '<value>'} ({kind})")


@dataclass
class _FieldTally:
    sampled_rows: int = 0
    fields: Counter[str] = field(default_factory=Counter)

    def add(self, text: str, needles: dict[str, str]) -> None:
        if self.sampled_rows >= _FIELD_SAMPLE_ROWS:
            return
        self.sampled_rows += 1
        found: set[str] = set()
        if len(text) > _FIELD_MAX_PARSE_CHARS:
            found.add("<value> (not parsed: too large)")
        else:
            parsed = _maybe_json(text)
            _hit_fields(text if parsed is None else parsed, needles, "", found, 0)
        self.fields.update(found)

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "sampled_rows": self.sampled_rows,
            "fields": dict(self.fields.most_common(_FIELD_MAX_REPORTED)),
        }
        if len(self.fields) > _FIELD_MAX_REPORTED:
            out["fields_not_shown"] = len(self.fields) - _FIELD_MAX_REPORTED
        return out


@dataclass(frozen=True)
class SqliteCensus:
    adapter: str | None
    path_hits: tuple[PathHitSummary, ...]
    composer_workspace_counts: dict[str, int]
    item_key_shapes: tuple[str, ...]
    chat_like_keys_present: bool


def _decode_cell(cell: object) -> str:
    if cell is None:
        return ""
    if isinstance(cell, bytes):
        return cell.decode("utf-8", errors="ignore")
    return str(cell)


def scan_sqlite_file(
    db_path: Path,
    old_prefix: str,
    source_os: str,
    field_report: bool = False,
    shareable: bool = False,
) -> SqliteCensus:
    needles = prefix_needle_variants(old_prefix, source_os)
    shape_of = shareable_key_shape if shareable else key_shape
    path_hits: list[PathHitSummary] = []
    composer_counts: dict[str, int] = {}
    key_shapes: set[str] = set()
    chat_like = False
    adapter_label: str | None = None

    with open_readonly_db(StagedPath(db_path)) as conn:
        det, _ = select_adapter(conn)
        if det.status == DetectionStatus.PASS:
            adapter_label = f"{det.adapter_id}@{det.adapter_version}"
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            if not safe_table_name(table):
                continue
            cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            col_names = [c[1] for c in cols]
            is_kv = table in _KV_TABLES and "key" in col_names and "value" in col_names
            for col_name, _col_type in ((c[1], c[2]) for c in cols):
                if col_name == "key" and is_kv:
                    continue
                enc_counts: dict[str, int] = {k: 0 for k in needles}
                pattern_counts: dict[str, int] | None = (
                    {} if is_kv and col_name == "value" else None
                )
                tallies: dict[str, _FieldTally] = {}
                if is_kv and col_name == "value":
                    cur = conn.execute(f'SELECT key, value FROM "{table}"')
                    for key_cell, val_cell in cur:
                        key_s = _decode_cell(key_cell)
                        val_s = _decode_cell(val_cell)
                        shape = shape_of(key_s)
                        key_shapes.add(shape)
                        if any(h in key_s.casefold() for h in _CHAT_KEY_HINTS):
                            chat_like = True
                        labels = value_contains_any(val_s, needles)
                        for label in labels:
                            enc_counts[label] += 1
                        if labels:
                            assert pattern_counts is not None
                            pattern_counts[shape] = pattern_counts.get(shape, 0) + 1
                        if labels and field_report:
                            tallies.setdefault(shape, _FieldTally()).add(val_s, needles)
                else:
                    cur = conn.execute(f'SELECT "{col_name}" FROM "{table}"')
                    for (cell,) in cur:
                        val_s = _decode_cell(cell)
                        labels = value_contains_any(val_s, needles)
                        for label in labels:
                            enc_counts[label] += 1
                        if labels and field_report:
                            tallies.setdefault(col_name, _FieldTally()).add(val_s, needles)
                if any(enc_counts.values()):
                    path_hits.append(
                        PathHitSummary(
                            table,
                            col_name,
                            enc_counts,
                            pattern_counts if pattern_counts else None,
                            {g: t.summary() for g, t in sorted(tallies.items())} or None,
                        )
                    )
        if COMPOSER_HEADERS_TABLE in tables and safe_table_name(COMPOSER_HEADERS_TABLE):
            for ws_id, count in conn.execute(
                f'SELECT workspaceId, COUNT(*) FROM "{COMPOSER_HEADERS_TABLE}" GROUP BY workspaceId'
            ):
                if ws_id is not None:
                    composer_counts[str(ws_id)] = int(count)

    return SqliteCensus(
        adapter_label,
        tuple(path_hits),
        composer_counts,
        tuple(sorted(key_shapes)),
        chat_like,
    )


def scan_storage_json_text(text: str, old_prefix: str, source_os: str) -> dict[str, int]:
    needles = prefix_needle_variants(old_prefix, source_os)
    counts = {k: 0 for k in needles}
    for label in value_contains_any(text, needles):
        counts[label] += 1
    return counts


def storage_json_fields(text: str, old_prefix: str, source_os: str) -> dict[str, Any]:
    needles = prefix_needle_variants(old_prefix, source_os)
    tally = _FieldTally()
    if value_contains_any(text, needles):
        tally.add(text, needles)
    return tally.summary()


def _leaves(value: Any, where: str, depth: int = 0) -> Iterator[tuple[str, Any]]:
    """(field path, scalar) for every leaf; nested JSON strings are not followed."""
    if depth > _FIELD_MAX_DEPTH:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            name = _field_name(str(key))
            yield from _leaves(item, f"{where}.{name}" if where else name, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _leaves(item, f"{where}[]", depth + 1)
    else:
        yield where or "<value>", value


@dataclass
class _LinkTally:
    rows: int = 0
    not_json_object: int = 0
    equals_workspace_id: Counter[str] = field(default_factory=Counter)
    equals_folder: Counter[str] = field(default_factory=Counter)
    workspace_identifier_fields: Counter[str] = field(default_factory=Counter)

    def add(
        self, text: str, workspace_id: str | None, folder: tuple[str, str | None] | None
    ) -> None:
        if self.rows >= _LINK_MAX_ROWS:
            return
        self.rows += 1
        parsed = _maybe_json(text) if len(text) <= _FIELD_MAX_PARSE_CHARS else None
        if not isinstance(parsed, dict):
            self.not_json_object += 1
            return
        id_fields: set[str] = set()
        folder_fields: set[str] = set()
        for where, leaf in _leaves(parsed, ""):
            if not isinstance(leaf, str):
                continue
            if workspace_id and leaf == workspace_id:
                id_fields.add(where)
            if folder and leaf == folder[0]:
                folder_fields.add(f"{where} (= folder uri)")
            elif folder and folder[1] and leaf == folder[1]:
                folder_fields.add(f"{where} (= folder path)")
        self.equals_workspace_id.update(id_fields)
        self.equals_folder.update(folder_fields)
        ident = parsed.get("workspaceIdentifier")
        if isinstance(ident, dict):
            self.workspace_identifier_fields.update(
                {where for where, _ in _leaves(ident, "workspaceIdentifier")}
            )
        else:
            self.workspace_identifier_fields["workspaceIdentifier (absent)"] += 1

    def summary(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "not_json_object": self.not_json_object,
            "fields_equal_workspace_id": dict(
                self.equals_workspace_id.most_common(_FIELD_MAX_REPORTED)
            ),
            "fields_equal_folder": dict(self.equals_folder.most_common(_FIELD_MAX_REPORTED)),
            "workspace_identifier_fields": dict(
                self.workspace_identifier_fields.most_common(_FIELD_MAX_REPORTED)
            ),
        }


def chat_link_report(
    db_path: Path,
    folders: dict[str, tuple[str, str | None]],
    mapped_ids: set[str],
) -> dict[str, Any]:
    """Which fields of chat headers and chat records repeat the chat's workspace ID or folder.

    `folders` maps workspace ID to (folder URI, folder path). Rows are grouped by whether the
    `composerHeaders.workspaceId` column names a mapped (migrating) workspace, another known
    workspace, or no known workspace folder. Only field names and counts are returned.
    """

    def group(workspace_id: str | None) -> str:
        if workspace_id in mapped_ids:
            return "mapped_workspaces"
        if workspace_id in folders:
            return "other_workspaces"
        return "no_workspace_folder"

    headers: dict[str, _LinkTally] = {}
    records: dict[str, _LinkTally] = {}
    with open_readonly_db(StagedPath(db_path)) as conn:
        det, _ = select_adapter(conn)
        if det.status != DetectionStatus.PASS or det.adapter_id != "cursor_composer_headers":
            return {}
        owner: dict[str, str | None] = {}
        for composer_id, ws_cell, value in conn.execute(
            f'SELECT composerId, workspaceId, value FROM "{COMPOSER_HEADERS_TABLE}"'
        ):
            ws = None if ws_cell is None else str(ws_cell)
            owner[str(composer_id)] = ws
            headers.setdefault(group(ws), _LinkTally()).add(
                _decode_cell(value), ws, folders.get(ws) if ws else None
            )
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "cursorDiskKV" in tables:
            for key_cell, value in conn.execute(
                "SELECT key, value FROM cursorDiskKV WHERE key LIKE ?",
                (_COMPOSER_DATA_PREFIX + "%",),
            ):
                key_s = _decode_cell(key_cell)
                if not key_s.startswith(_COMPOSER_DATA_PREFIX):
                    continue
                composer_id = key_s.removeprefix(_COMPOSER_DATA_PREFIX)
                if composer_id in owner:
                    ws = owner[composer_id]
                    label = group(ws)
                else:
                    ws, label = None, "no_header_row"
                records.setdefault(label, _LinkTally()).add(
                    _decode_cell(value), ws, folders.get(ws) if ws else None
                )
    return {
        "composer_headers": {g: t.summary() for g, t in sorted(headers.items())},
        "composer_data": {g: t.summary() for g, t in sorted(records.items())},
    }


def redact_path_hits(
    hits: tuple[PathHitSummary, ...],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for h in hits:
        row: dict[str, Any] = {
            "table": h.table,
            "column": h.column,
            "encoding_hits": h.encoding_hits,
        }
        if h.key_patterns is not None:
            row["key_patterns"] = h.key_patterns
        if h.fields is not None:
            row["fields"] = h.fields
        out.append(row)
    return out


def merge_path_hits(scans: list[tuple[PathHitSummary, ...]]) -> list[dict[str, Any]]:
    """Sum per-table hit counts and field tallies across several databases (workspace DBs)."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for hits in scans:
        for h in hits:
            row = merged.setdefault(
                (h.table, h.column),
                {"table": h.table, "column": h.column, "databases": 0, "encoding_hits": Counter()},
            )
            row["databases"] += 1
            row["encoding_hits"].update(h.encoding_hits)
            if h.key_patterns:
                row.setdefault("key_patterns", Counter()).update(h.key_patterns)
            for group, summary in (h.fields or {}).items():
                acc = row.setdefault("fields", {}).setdefault(
                    group, {"sampled_rows": 0, "fields": Counter()}
                )
                acc["sampled_rows"] += summary["sampled_rows"]
                acc["fields"].update(summary["fields"])
    out: list[dict[str, Any]] = []
    for row in merged.values():
        row["encoding_hits"] = dict(row["encoding_hits"])
        if "key_patterns" in row:
            row["key_patterns"] = dict(row["key_patterns"].most_common())
        for acc in row.get("fields", {}).values():
            acc["fields"] = dict(acc["fields"].most_common(_FIELD_MAX_REPORTED))
        out.append(row)
    return sorted(out, key=lambda r: (r["table"], r["column"]))
