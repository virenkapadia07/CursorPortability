from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from cursor_portability.models.enums import CategoryAction, DataCategory, EvidenceLevel


@dataclass(frozen=True)
class PolicyRule:
    root_token: str  # user-data | cursor-home
    glob: str
    category: DataCategory
    action: CategoryAction
    evidence: EvidenceLevel


POLICY_VERSION = 1

# fmt: off
POLICY_RULES: tuple[PolicyRule, ...] = (
    PolicyRule("user-data", "User/settings.json", DataCategory.USER_SETTINGS, CategoryAction.INCLUDE, EvidenceLevel.UPSTREAM_INFERRED),
    PolicyRule("user-data", "User/keybindings.json", DataCategory.KEYBINDINGS, CategoryAction.INCLUDE, EvidenceLevel.UPSTREAM_INFERRED),
    PolicyRule("user-data", "User/snippets/**", DataCategory.SNIPPETS, CategoryAction.INCLUDE, EvidenceLevel.UPSTREAM_INFERRED),
    PolicyRule("user-data", "User/globalStorage/state.vscdb", DataCategory.COMPOSER_DB, CategoryAction.INCLUDE, EvidenceLevel.UPSTREAM_INFERRED),
    PolicyRule("user-data", "User/globalStorage/conversation-search.db", DataCategory.UNKNOWN, CategoryAction.INVENTORY_ONLY, EvidenceLevel.COMMUNITY_REPORTED),
    PolicyRule("user-data", "User/workspaceStorage/**/state.vscdb", DataCategory.COMPOSER_DB, CategoryAction.INCLUDE, EvidenceLevel.UPSTREAM_INFERRED),
    PolicyRule("user-data", "User/workspaceStorage/**/workspace.json", DataCategory.WORKSPACE_METADATA, CategoryAction.INCLUDE, EvidenceLevel.UPSTREAM_INFERRED),
    PolicyRule("user-data", "User/History/**", DataCategory.UNKNOWN, CategoryAction.EXCLUDE, EvidenceLevel.UPSTREAM_INFERRED),
    PolicyRule("user-data", "Backups/**", DataCategory.UNKNOWN, CategoryAction.EXCLUDE, EvidenceLevel.UPSTREAM_INFERRED),
    PolicyRule("user-data", "logs/**", DataCategory.LOGS, CategoryAction.EXCLUDE, EvidenceLevel.UPSTREAM_INFERRED),
    PolicyRule("user-data", "Cookies", DataCategory.AUTH_MATERIAL, CategoryAction.EXCLUDE, EvidenceLevel.UPSTREAM_INFERRED),
    PolicyRule("user-data", "machineid", DataCategory.AUTH_MATERIAL, CategoryAction.EXCLUDE, EvidenceLevel.UPSTREAM_INFERRED),
    PolicyRule("cursor-home", "extensions/extensions.json", DataCategory.EXTENSIONS_LIST, CategoryAction.INCLUDE, EvidenceLevel.UPSTREAM_INFERRED),
    PolicyRule("cursor-home", "mcp.json", DataCategory.MCP_CONFIG, CategoryAction.OPT_IN, EvidenceLevel.UPSTREAM_INFERRED),
    PolicyRule("cursor-home", "chats/**/store.db", DataCategory.STORE_DB, CategoryAction.INVENTORY_ONLY, EvidenceLevel.COMMUNITY_REPORTED),
    PolicyRule("cursor-home", "projects/**/mcp-auth.json", DataCategory.AUTH_MATERIAL, CategoryAction.EXCLUDE, EvidenceLevel.UPSTREAM_INFERRED),
    PolicyRule("cursor-home", "statsig-cache.json", DataCategory.AUTH_MATERIAL, CategoryAction.EXCLUDE, EvidenceLevel.UPSTREAM_INFERRED),
    PolicyRule("cursor-home", "rules/**", DataCategory.USER_RULES, CategoryAction.INCLUDE, EvidenceLevel.UPSTREAM_INFERRED),
)
# fmt: on


def classify_relative_path(root_token: str, rel_path: str) -> PolicyRule | None:
    rel = rel_path.replace("\\", "/")
    for rule in POLICY_RULES:
        if rule.root_token != root_token:
            continue
        if fnmatch.fnmatch(rel, rule.glob):
            return rule
    return None
