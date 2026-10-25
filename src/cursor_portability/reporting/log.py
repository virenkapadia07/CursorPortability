"""Logging with home-directory redaction."""

from __future__ import annotations

import logging


class RedactionFilter(logging.Filter):
    _home: str | None = None

    @classmethod
    def set_home(cls, home: str | None) -> None:
        cls._home = home

    def filter(self, record: logging.LogRecord) -> bool:
        if self._home and len(self._home) > 1:
            record.msg = str(record.msg).replace(self._home, "~")
            if record.args:
                record.args = tuple(
                    str(a).replace(self._home, "~") if isinstance(a, str) else a
                    for a in record.args
                )
        return True


def configure_logging(home: str | None = None, level: int = logging.WARNING) -> None:
    RedactionFilter.set_home(home)
    root = logging.getLogger("cursor_portability")
    root.setLevel(level)
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.addFilter(RedactionFilter())
        handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"cursor_portability.{name}")
