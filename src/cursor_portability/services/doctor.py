from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cursor_portability.models.enums import ValidationOutcome
from cursor_portability.platform.process_probe import effective_running, probe_cursor_running
from cursor_portability.services.runtime import probe_sqlite, runtime_info
from cursor_portability.storage.diskspace import available_bytes


@dataclass(frozen=True)
class DoctorReport:
    outcome: ValidationOutcome
    runtime: dict[str, str]
    sqlite: dict[str, object]
    cursor_state: str
    free_bytes_home: int
    notes: tuple[str, ...]


def run_doctor(home: Path, user_data: Path | None) -> DoctorReport:
    notes: list[str] = []
    sqlite = probe_sqlite()
    if not sqlite.vacuum_into:
        notes.append("SQLite VACUUM INTO unavailable; backup may fail")
        outcome = ValidationOutcome.PARTIAL
    elif not sqlite.defensive:
        notes.append("SQLite DEFENSIVE mode unavailable")
        outcome = ValidationOutcome.PARTIAL
    else:
        outcome = ValidationOutcome.PASS
    state = probe_cursor_running(user_data)
    if effective_running(state):
        notes.append("Cursor appears running or state unknown")
    return DoctorReport(
        outcome=outcome,
        runtime=runtime_info(),
        sqlite={
            "version": sqlite.version,
            "vacuum_into": sqlite.vacuum_into,
            "defensive": sqlite.defensive,
            "json1": sqlite.json1,
        },
        cursor_state=state.value,
        free_bytes_home=available_bytes(home),
        notes=tuple(notes),
    )
