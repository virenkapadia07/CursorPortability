from __future__ import annotations

from dataclasses import dataclass

from cursor_portability import __version__
from cursor_portability.schemas.registry import ADAPTERS


@dataclass(frozen=True)
class CompatibilityReport:
    tool_version: str
    adapters: tuple[dict[str, object], ...]
    phase: str


def run_compatibility() -> CompatibilityReport:
    return CompatibilityReport(
        __version__,
        tuple(
            {
                "id": adapter.adapter_id,
                "version": adapter.adapter_version,
                "evidence": adapter.evidence.value,
                "read": True,
                "migrate": False,
            }
            for adapter in ADAPTERS
        ),
        "0-1",
    )
