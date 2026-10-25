from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Protocol

from cursor_portability.models.enums import DetectionStatus, EvidenceLevel


@dataclass(frozen=True)
class DetectionResult:
    status: DetectionStatus
    adapter_id: str
    adapter_version: int
    message: str


@dataclass(frozen=True)
class TableInventory:
    name: str
    row_count: int
    key_shapes: tuple[str, ...]


@dataclass(frozen=True)
class Inventory:
    tables: tuple[TableInventory, ...]
    fingerprint_digest: str


class SchemaAdapter(Protocol):
    adapter_id: str
    adapter_version: int
    evidence: EvidenceLevel

    def detect(self, conn: sqlite3.Connection) -> DetectionResult: ...
    def inventory(self, conn: sqlite3.Connection, fingerprint_digest: str) -> Inventory: ...
