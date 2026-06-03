import logging
from abc import ABC, abstractmethod
from typing import Any


class ActivityLogger(ABC):
    """Unified Activity logging interface. Keeps service layer decoupled from Temporal."""

    @abstractmethod
    def info(self, message: str, **attrs: Any) -> None: ...

    @abstractmethod
    def warn(self, message: str, **attrs: Any) -> None: ...

    @abstractmethod
    def error(self, message: str, **attrs: Any) -> None: ...


class TemporalActivityLogger(ActivityLogger):
    """Bridges to Temporal activity context logger. Must be used within an activity context."""

    def info(self, message: str, **attrs: Any) -> None:
        from temporalio import activity
        activity.logger.info(message, extra=attrs)

    def warn(self, message: str, **attrs: Any) -> None:
        from temporalio import activity
        activity.logger.warning(message, extra=attrs)

    def error(self, message: str, **attrs: Any) -> None:
        from temporalio import activity
        activity.logger.error(message, extra=attrs)


class ConsoleActivityLogger(ActivityLogger):
    """Bridges to standard library logging. Used for local runs and tests."""

    def __init__(self) -> None:
        self._logger = logging.getLogger("shannon.activity")

    def info(self, message: str, **attrs: Any) -> None:
        self._logger.info(message, extra=attrs)

    def warn(self, message: str, **attrs: Any) -> None:
        self._logger.warning(message, extra=attrs)

    def error(self, message: str, **attrs: Any) -> None:
        self._logger.error(message, extra=attrs)


def create_activity_logger() -> ActivityLogger:
    """Factory: returns TemporalActivityLogger inside activity context, ConsoleActivityLogger otherwise."""
    try:
        from temporalio import activity
        activity.info()
        return TemporalActivityLogger()
    except Exception:
        return ConsoleActivityLogger()
