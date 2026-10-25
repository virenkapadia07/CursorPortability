from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cursor_portability.models.enums import DetectionStatus
from cursor_portability.schemas.adapters.cursor_composer_headers import COMPOSER_HEADERS_TABLE
from cursor_portability.schemas.keyshape import key_shape
from cursor_portability.schemas.registry import select_adapter
from cursor_portability.storage.paths import StagedPath
from cursor_portability.storage.sqlite_conn import open_readonly_db, safe_table_name
from cursor_portability.workspace.path_needles import prefix_needle_variants, value_contains_any

_KV_TABLES = frozenset({"ItemTable", "cursorDiskKV"})
_CHAT_KEY_HINTS = ("composer", "conversation", "chat", "aichat")


@dataclass(frozen=True)
class PathHitSummary:
    table: str
    column: str
    encoding_hits: dict[str, int]
    key_patterns: dict[str, int] | None = None


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
) -> SqliteCensus:
    needles = prefix_needle_variants(old_prefix, source_os)
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
                if is_kv and col_name == "value":
                    cur = conn.execute(f'SELECT key, value FROM "{table}"')
                    for key_cell, val_cell in cur:
                        key_s = _decode_cell(key_cell)
                        val_s = _decode_cell(val_cell)
                        shape = key_shape(key_s)
                        key_shapes.add(shape)
                        if any(h in key_s.casefold() for h in _CHAT_KEY_HINTS):
                            chat_like = True
                        for label in value_contains_any(val_s, needles):
                            enc_counts[label] += 1
                            assert pattern_counts is not None
                            pattern_counts[shape] = pattern_counts.get(shape, 0) + 1
                else:
                    cur = conn.execute(f'SELECT "{col_name}" FROM "{table}"')
                    for (cell,) in cur:
                        val_s = _decode_cell(cell)
                        for label in value_contains_any(val_s, needles):
                            enc_counts[label] += 1
                if any(enc_counts.values()):
                    path_hits.append(
                        PathHitSummary(
                            table,
                            col_name,
                            enc_counts,
                            pattern_counts if pattern_counts else None,
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
        out.append(row)
    return out
