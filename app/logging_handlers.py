"""Logging handlers used by the application.

Provides a handler that mirrors ERROR-level logs (and above)
into the application's DB using Controller.log_message().
"""

from __future__ import annotations

import logging
from logging import LogRecord
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .core import Controller

logger = logging.getLogger(__name__)


class DBLogHandler(logging.Handler):
    """Persist log records to the DB via Controller.log_message().

    Attach this handler to the root logger (or specific loggers)
    after the Controller has been created. By default, it only
    handles ERROR and CRITICAL records; adjust the level as needed.
    """

    def __init__(self, controller: Controller, level: int = logging.ERROR) -> None:
        super().__init__(level=level)
        self.controller = controller

    def emit(self, record: LogRecord) -> None:
        try:
            # Use the configured formatter so exception tracebacks are included
            message = self.format(record)
            log_type = (
                "error"
                if record.levelno >= logging.ERROR
                else ("warning" if record.levelno >= logging.WARNING else "info")
            )
            self.controller.log_message(message, log_type)
        except Exception as e:  # pragma: no cover - avoid recursion on logging failures
            try:
                # Best-effort: don't re-emit ERROR to avoid loops
                logger.warning("DBLogHandler emit failed: %s", e)
            except Exception:
                logger.exception("Failed to log exception in DBLogHandler emit", exc_info=True)
