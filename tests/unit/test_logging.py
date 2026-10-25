import logging
from pathlib import Path

from cursor_portability.cli.main import main
from cursor_portability.platform.env import current_environment
from cursor_portability.reporting.log import RedactionFilter


def test_cli_configures_home_redaction_for_log_records(fake_home: Path) -> None:
    assert main(["compatibility"]) == 0
    project = str(current_environment().home / "project")
    record = logging.LogRecord(
        "cursor_portability.test", logging.WARNING, __file__, 1, "opened %s", (project,), None
    )
    assert RedactionFilter().filter(record)
    assert record.getMessage() == "opened ~/project"
