"""CLI logging configuration for Holon."""

import logging
import sys

# Root of Holon's own logger hierarchy; the CLI routes its INFO records to stderr (see configure_cli_logging).
PACKAGE_LOGGER_NAME = "sandbox_executor"


class _CurrentStderrHandler(logging.StreamHandler):
    """Stream handler that always writes to the current ``sys.stderr`` rather than the one bound at creation."""

    @property
    def stream(self):  # type: ignore[override]
        return sys.stderr

    @stream.setter
    def stream(self, _value) -> None:
        pass


def configure_cli_logging() -> None:
    """Send Holon's own INFO-and-above log records to stderr as plain messages.

    Only the ``sandbox_executor`` logger hierarchy is configured, so third-party libraries keep their own levels and
    stdout stays untouched. Idempotent: a second call adds no further handler.
    """
    package_logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    if any(isinstance(handler, _CurrentStderrHandler) for handler in package_logger.handlers):
        return
    handler = _CurrentStderrHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    package_logger.addHandler(handler)
    if package_logger.level == logging.NOTSET:
        package_logger.setLevel(logging.INFO)
