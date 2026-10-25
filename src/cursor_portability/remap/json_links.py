"""Rewrites of the fields that tie chats and the Agents window to a workspace.

Field names are from the workspaces census of one real profile: every chat header and chat
record of a folder workspace repeats the header's `workspaceId` column in
`workspaceIdentifier.id` and the workspace folder in `workspaceIdentifier.uri`, and the same
`{id, uri}` pair appears in `agentLocation.environment` / `agentLocationHistory[]`.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from cursor_portability.schemas.adapters.cursor_composer_headers import COMPOSER_HEADERS_TABLE
from cursor_portability.schemas.keyshape import key_shape
from cursor_portability.security.credential_patterns import is_credential_key
from cursor_portability.security.json_strict import loads_strict

COMPOSER_DATA_PREFIX = "composerData:"
AGENT_STATE_KEYS = frozenset(
    {"glass.localAgentProjects.v1", "workspaceMetadata.entries", "repositoryTracker.paths"}
)
AGENT_STATE_KEY_SHAPES = frozenset({"cursor<path>", "cursor<path><uuid><path>"})
_MAX_JSON_CHARS = 64 * 1024 * 1024


class ChatLinkMismatch(ValueError):
    """A stored workspace reference does not have the shape the plan relies on."""


@dataclass(frozen=True)
class FolderLink:
    """One moved workspace: Ubuntu id/folder to Mac id/folder (paths POSIX, URIs `file://`)."""

    from_id: str
    to_id: str
    from_path: str
    to_path: str
    from_uri: str
    to_uri: str

    def map_location(self, value: str) -> str | None:
        """The Mac form of `value` when it is the Ubuntu folder or inside it, else None."""
        for old, new in ((self.from_path, self.to_path), (self.from_uri, self.to_uri)):
            if value == old or value.startswith(old + "/"):
                return new + value[len(old) :]
        return None


@dataclass
class LinkRewriteCounts:
    chat_headers: int = 0
    chat_records: int = 0
    agent_state: Counter[str] = field(default_factory=Counter)

    def as_dict(self) -> dict[str, Any]:
        return {
            "chat_headers": self.chat_headers,
            "chat_records": self.chat_records,
            "agent_state": dict(sorted(self.agent_state.items())),
        }


def _rewrite_uri(uri: Any, link: FolderLink, where: str) -> None:
    if not isinstance(uri, dict) or uri.get("path") != link.from_path:
        raise ChatLinkMismatch(f"{where}.uri is not the folder of workspace {link.from_id}")
    for name, old, new in (
        ("path", link.from_path, link.to_path),
        ("fsPath", link.from_path, link.to_path),
        ("external", link.from_uri, link.to_uri),
    ):
        if name in uri:
            if uri[name] != old:
                raise ChatLinkMismatch(
                    f"{where}.uri.{name} does not match workspace {link.from_id}"
                )
            uri[name] = new


def _rewrite_workspace_ref(ref: Any, link: FolderLink, where: str) -> bool:
    """An `{id, uri}` workspace reference; rewritten only when it names the moved workspace."""
    if not isinstance(ref, dict) or ref.get("id") != link.from_id:
        return False
    if "configPath" in ref:
        raise ChatLinkMismatch(f"{where} of workspace {link.from_id} is a multi-root workspace")
    ref["id"] = link.to_id
    if "uri" in ref:
        _rewrite_uri(ref["uri"], link, where)
    return True


def rewrite_chat(doc: Any, link: FolderLink) -> bool:
    """Point a chat header or chat record of `link.from_id` at the Mac workspace."""
    if not isinstance(doc, dict):
        raise ChatLinkMismatch(f"chat of workspace {link.from_id} is not a JSON object")
    changed = False
    if "workspaceIdentifier" in doc:
        if not _rewrite_workspace_ref(doc["workspaceIdentifier"], link, "workspaceIdentifier"):
            raise ChatLinkMismatch(
                f"workspaceIdentifier.id of a chat of workspace {link.from_id} is another workspace"
            )
        changed = True
    agent = doc.get("agentLocation")
    if isinstance(agent, dict):
        changed |= _rewrite_workspace_ref(
            agent.get("environment"), link, "agentLocation.environment"
        )
    history = doc.get("agentLocationHistory")
    for entry in history if isinstance(history, list) else ():
        location = entry.get("location") if isinstance(entry, dict) else None
        if isinstance(location, dict):
            changed |= _rewrite_workspace_ref(
                location.get("environment"), link, "agentLocationHistory[].location.environment"
            )
    repos = doc.get("trackedGitRepos")
    for repo in repos if isinstance(repos, list) else ():
        if isinstance(repo, dict) and isinstance(repo.get("repoPath"), str):
            mapped = link.map_location(repo["repoPath"])
            if mapped is not None:
                repo["repoPath"] = mapped
                changed = True
    return changed


def _rewrite_strings(value: Any, ids: dict[str, str], links: tuple[FolderLink, ...]) -> int:
    """Rewrite, in place, string values equal to a moved workspace id or inside a moved folder."""
    changed = 0
    if isinstance(value, dict):
        items: list[tuple[Any, Any]] = list(value.items())
    elif isinstance(value, list):
        items = list(enumerate(value))
    else:
        return 0
    for slot, item in items:
        if isinstance(item, str):
            mapped = ids.get(item)
            for link in links if mapped is None else ():
                mapped = link.map_location(item)
                if mapped is not None:
                    break
            if mapped is not None:
                value[slot] = mapped
                changed += 1
        else:
            changed += _rewrite_strings(item, ids, links)
    return changed


def rewrite_agent_state(doc: Any, links: tuple[FolderLink, ...]) -> bool:
    ids = {link.from_id: link.to_id for link in links}
    if isinstance(doc, str):
        raise ChatLinkMismatch("agent state value is a bare string")
    return _rewrite_strings(doc, ids, links) > 0


def is_agent_state_key(key: str) -> bool:
    if is_credential_key(key):
        return False
    return key in AGENT_STATE_KEYS or key_shape(key) in AGENT_STATE_KEY_SHAPES


def _dump(doc: Any) -> str:
    text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return json.dumps(doc, separators=(",", ":"), allow_nan=False)
    return text


def _rewrite_cell(cell: object, rewrite: Callable[[Any], bool], what: str) -> str | bytes | None:
    """New cell value (same storage type) when `rewrite` changed the JSON document, else None."""
    if cell is None:
        return None
    try:
        text = cell.decode("utf-8") if isinstance(cell, bytes) else str(cell)
        doc = loads_strict(text, max_size=_MAX_JSON_CHARS)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ChatLinkMismatch(f"{what} is not JSON") from exc
    if not rewrite(doc):
        return None
    out = _dump(doc)
    return out.encode("utf-8") if isinstance(cell, bytes) else out


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def rewrite_links(
    conn: sqlite3.Connection, links: tuple[FolderLink, ...], write: bool
) -> LinkRewriteCounts:
    """Rewrite (or, with `write=False`, count) the chat and Agents-window references to the
    Ubuntu workspaces in `links`. Must run before the `composerHeaders.workspaceId` update."""
    counts = LinkRewriteCounts()
    tables = _tables(conn)
    has_records = "cursorDiskKV" in tables
    for link in links:
        rewrite = partial(rewrite_chat, link=link)
        headers = conn.execute(
            f'SELECT composerId, value FROM "{COMPOSER_HEADERS_TABLE}" WHERE workspaceId = ?',
            (link.from_id,),
        ).fetchall()
        for composer_id, cell in headers:
            what = f"chat header of workspace {link.from_id}"
            new = _rewrite_cell(cell, rewrite, what)
            if new is not None:
                counts.chat_headers += 1
                if write:
                    conn.execute(
                        f'UPDATE "{COMPOSER_HEADERS_TABLE}" SET value = ? WHERE composerId = ?',
                        (new, composer_id),
                    )
            if not has_records:
                continue
            record_key = f"{COMPOSER_DATA_PREFIX}{composer_id}"
            row = conn.execute(
                "SELECT value FROM cursorDiskKV WHERE key = ?", (record_key,)
            ).fetchone()
            if row is None:
                continue
            what = f"chat record of workspace {link.from_id}"
            new = _rewrite_cell(row[0], rewrite, what)
            if new is not None:
                counts.chat_records += 1
                if write:
                    conn.execute(
                        "UPDATE cursorDiskKV SET value = ? WHERE key = ?", (new, record_key)
                    )
    if "ItemTable" not in tables or not links:
        return counts
    needles = {n for link in links for n in (link.from_id, link.from_path, link.from_uri)}
    keys = [k for (k,) in conn.execute("SELECT key FROM ItemTable") if isinstance(k, str)]
    for key in sorted(k for k in keys if is_agent_state_key(k)):
        row = conn.execute("SELECT value FROM ItemTable WHERE key = ?", (key,)).fetchone()
        if row is None or row[0] is None:
            continue
        raw = row[0]
        text = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
        if not any(n in text for n in needles):
            continue
        new = _rewrite_cell(
            raw, lambda doc: rewrite_agent_state(doc, links), f"agent state {key_shape(key)}"
        )
        if new is not None:
            counts.agent_state[key_shape(key)] += 1
            if write:
                conn.execute("UPDATE ItemTable SET value = ? WHERE key = ?", (new, key))
    return counts


def _points_at(cell: object, link: FolderLink) -> bool:
    try:
        text = cell.decode("utf-8") if isinstance(cell, bytes) else str(cell)
        doc = loads_strict(text, max_size=_MAX_JSON_CHARS)
    except (UnicodeDecodeError, ValueError):
        return False
    if not isinstance(doc, dict) or "workspaceIdentifier" not in doc:
        return isinstance(doc, dict)
    ident = doc["workspaceIdentifier"]
    if not isinstance(ident, dict) or ident.get("id") != link.to_id:
        return False
    uri = ident.get("uri")
    return not isinstance(uri, dict) or uri.get("path") in (None, link.to_path)


def link_mismatches(conn: sqlite3.Connection, links: tuple[FolderLink, ...]) -> list[str]:
    """Chats on each Mac workspace whose stored workspace reference is not that workspace."""
    errors: list[str] = []
    has_records = "cursorDiskKV" in _tables(conn)
    for to_id, link in sorted({link.to_id: link for link in links}.items()):
        bad = 0
        headers = conn.execute(
            f'SELECT composerId, value FROM "{COMPOSER_HEADERS_TABLE}" WHERE workspaceId = ?',
            (to_id,),
        ).fetchall()
        for composer_id, cell in headers:
            cells = [cell]
            if has_records:
                row = conn.execute(
                    "SELECT value FROM cursorDiskKV WHERE key = ?",
                    (f"{COMPOSER_DATA_PREFIX}{composer_id}",),
                ).fetchone()
                if row is not None:
                    cells.append(row[0])
            bad += sum(1 for c in cells if c is not None and not _points_at(c, link))
        if bad:
            errors.append(f"mac id {to_id}: {bad} chat rows name another workspace")
    return errors
