from __future__ import annotations

SETTINGS = "User/settings.json"
KEYBINDINGS = "User/keybindings.json"
SNIPPETS_PREFIX = "User/snippets/"
SNIPPET_SUFFIXES = (".json", ".code-snippets")


def is_allowed_snippet_name(name: str) -> bool:
    if not name or name in (".", "..") or any(c in name for c in "/\\:\0"):
        return False
    return name.endswith(SNIPPET_SUFFIXES)


def is_allowed_target(rel: str) -> bool:
    if rel in (SETTINGS, KEYBINDINGS):
        return True
    return rel.startswith(SNIPPETS_PREFIX) and is_allowed_snippet_name(
        rel.removeprefix(SNIPPETS_PREFIX)
    )


def source_member_for(rel: str) -> str:
    return f"user-data/{rel}"
