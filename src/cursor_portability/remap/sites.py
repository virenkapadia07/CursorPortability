from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from cursor_portability.schemas.adapters.cursor_composer_headers import COMPOSER_HEADERS_TABLE
from cursor_portability.workspace.path_needles import prefix_needle_variants

RECENT_PATHS_KEY = "history.recentlyOpenedPathsList"


@dataclass(frozen=True)
class WorkspaceIdUpdate:
    from_id: str
    to_id: str
    expected_rows: int
    existing_rows: int = 0


@dataclass(frozen=True)
class DbUpdatePlan:
    adapter_label: str
    workspace_id_updates: tuple[WorkspaceIdUpdate, ...]
    recent_paths_rewrite: bool


def plan_db_updates(
    composer_counts: dict[str, int],
    pairs: list[tuple[str, str]],
    adapter_label: str,
    include_recent_paths: bool,
) -> DbUpdatePlan:
    updates: list[WorkspaceIdUpdate] = []
    for ubuntu_id, mac_id in pairs:
        expected = composer_counts.get(ubuntu_id, 0)
        if expected > 0:
            updates.append(
                WorkspaceIdUpdate(ubuntu_id, mac_id, expected, composer_counts.get(mac_id, 0))
            )
    return DbUpdatePlan(adapter_label, tuple(updates), include_recent_paths)


def apply_db_updates(
    conn: sqlite3.Connection,
    plan: DbUpdatePlan,
    old_prefix: str,
    new_prefix: str,
    source_os: str,
) -> None:
    for upd in plan.workspace_id_updates:
        cur = conn.execute(
            f'UPDATE "{COMPOSER_HEADERS_TABLE}" SET workspaceId = ? WHERE workspaceId = ?',
            (upd.to_id, upd.from_id),
        )
        if cur.rowcount != upd.expected_rows:
            raise ValueError(
                f"composerHeaders update rowcount {cur.rowcount} != {upd.expected_rows}"
            )
    if plan.recent_paths_rewrite:
        row = conn.execute(
            "SELECT value FROM ItemTable WHERE key = ?", (RECENT_PATHS_KEY,)
        ).fetchone()
        if row is None:
            return
        raw = row[0]
        text = raw.decode("utf-8", errors="strict") if isinstance(raw, bytes) else str(raw)
        needles = prefix_needle_variants(old_prefix, source_os)
        new_text = text
        for needle in needles.values():
            if needle:
                new_text = new_text.replace(needle, new_prefix.replace("\\", "/"))
        if new_text != text:
            conn.execute(
                "UPDATE ItemTable SET value = ? WHERE key = ?",
                (new_text.encode("utf-8"), RECENT_PATHS_KEY),
            )


def recent_paths_key_present(conn: sqlite3.Connection, old_prefix: str, source_os: str) -> bool:
    row = conn.execute("SELECT value FROM ItemTable WHERE key = ?", (RECENT_PATHS_KEY,)).fetchone()
    if row is None:
        return False
    val = row[0]
    text = val.decode("utf-8", errors="ignore") if isinstance(val, bytes) else str(val)
    needles = prefix_needle_variants(old_prefix, source_os)
    return any(n in text for n in needles.values() if n)
