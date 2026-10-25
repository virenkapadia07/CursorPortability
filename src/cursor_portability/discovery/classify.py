from __future__ import annotations

from dataclasses import dataclass

from cursor_portability.discovery.policy_v1 import PolicyRule, classify_relative_path
from cursor_portability.models.enums import CategoryAction, DataCategory


@dataclass(frozen=True)
class ClassifiedPath:
    root_token: str
    relative_path: str
    category: DataCategory
    action: CategoryAction
    rule: PolicyRule | None


def classify_path(root_token: str, relative_path: str) -> ClassifiedPath:
    rule = classify_relative_path(root_token, relative_path)
    if rule is None:
        return ClassifiedPath(
            root_token=root_token,
            relative_path=relative_path,
            category=DataCategory.UNKNOWN,
            action=CategoryAction.INVENTORY_ONLY,
            rule=None,
        )
    return ClassifiedPath(
        root_token=root_token,
        relative_path=relative_path,
        category=rule.category,
        action=rule.action,
        rule=rule,
    )
