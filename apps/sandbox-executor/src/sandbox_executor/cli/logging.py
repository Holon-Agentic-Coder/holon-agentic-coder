import logging
import sys

PACKAGE_LOGGER_NAME = "sandbox_executor"


class _CurrentStderrHandler(logging.StreamHandler):
    # why: pytest capsys and redirect_stderr swap sys.stderr after logging is configured.
    @property
    def stream(self):  # type: ignore[override]
        return sys.stderr

    @stream.setter
    def stream(self, _value) -> None:
        pass


def configure_cli_logging() -> None:
    package_logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    if any(isinstance(handler, _CurrentStderrHandler) for handler in package_logger.handlers):
        return
    handler = _CurrentStderrHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    package_logger.addHandler(handler)
    if package_logger.level == logging.NOTSET:
        package_logger.setLevel(logging.INFO)
