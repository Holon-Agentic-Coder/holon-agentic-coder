from __future__ import annotations

import contextlib
import errno
import io
import logging
import os
import random
import sys
import time
from collections.abc import Iterator
from typing import Any, TextIO

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_UNSUPPORTED_FLOCK_ERRNOS = (errno.ENOSYS, getattr(errno, "EOPNOTSUPP", 45))
_RETRYABLE_FLOCK_ERRNOS = (errno.EAGAIN, errno.EWOULDBLOCK)
_BACKOFF_MULTIPLIER = 2
_JITTER_MIN = 0.5
_JITTER_MAX = 1.5


class LedgerLock:
    def __init__(
        self,
        filepath: str,
        timeout: float = 10.0,
        initial_delay: float = 0.02,
        max_delay: float = 0.5,
    ) -> None:
        self.filepath = filepath
        self.timeout = timeout
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self._file: TextIO | None = None
        self._fd: int | None = None
        self._locked: bool = False

    @staticmethod
    def _supports_posix_flock() -> bool:
        return sys.platform != "win32" and fcntl is not None and hasattr(fcntl, "flock")

    def _sleep_with_backoff(self, delay: float) -> float:
        jitter = random.uniform(_JITTER_MIN, _JITTER_MAX)
        sleep_time = min(delay * jitter, self.max_delay)
        time.sleep(sleep_time)
        return min(delay * _BACKOFF_MULTIPLIER, self.max_delay)

    def _acquire_exclusive_lock(self, fd: int) -> None:
        start_time = time.monotonic()
        delay = self.initial_delay
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._locked = True
                return
            except (BlockingIOError, OSError) as exc:
                if isinstance(exc, OSError) and exc.errno in _UNSUPPORTED_FLOCK_ERRNOS:
                    logger.warning(
                        "Advisory file locking not supported on filesystem for %s; "
                        "falling back to uncoordinated append",
                        self.filepath,
                    )
                    return
                if (
                    isinstance(exc, OSError)
                    and exc.errno not in _RETRYABLE_FLOCK_ERRNOS
                    and not isinstance(exc, BlockingIOError)
                ):
                    if self._file is not None:
                        self._file.close()
                    raise
                if (time.monotonic() - start_time) >= self.timeout:
                    if self._file is not None:
                        self._file.close()
                    raise TimeoutError(f"Timed out waiting for exclusive lock on ledger file: {self.filepath}") from exc
                delay = self._sleep_with_backoff(delay)

    def __enter__(self) -> TextIO:
        dirname = os.path.dirname(os.path.abspath(self.filepath))
        if dirname:
            os.makedirs(dirname, exist_ok=True)

        self._file = open(self.filepath, "a", encoding="utf-8")

        if not self._supports_posix_flock():
            logger.warning(
                "Advisory file locking (fcntl.flock) unavailable on platform %s; "
                "falling back to uncoordinated append for %s",
                sys.platform,
                self.filepath,
            )
            return self._file

        try:
            self._fd = self._file.fileno()
        except (AttributeError, io.UnsupportedOperation, OSError):
            self._fd = None

        if not isinstance(self._fd, int):
            return self._file

        self._acquire_exclusive_lock(self._fd)
        return self._file

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._file is not None and not self._file.closed:
            try:
                self._file.flush()
                if self._fd is not None:
                    with contextlib.suppress(OSError):
                        os.fsync(self._fd)
                if self._locked and self._fd is not None:
                    with contextlib.suppress(OSError):
                        fcntl.flock(self._fd, fcntl.LOCK_UN)
                    self._locked = False
            finally:
                self._file.close()


class LedgerRewriteError(RuntimeError):
    pass


def _read_ledger_bytes(path: str) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return b""


def _first_divergence(before: bytes, after: bytes) -> int:
    limit = min(len(before), len(after))
    return next((i for i in range(limit) if before[i] != after[i]), limit)


def assert_ledger_prefix(path: str, before: bytes) -> None:
    after = _read_ledger_bytes(path)
    if not after.startswith(before):
        offset = _first_divergence(before, after)
        raise LedgerRewriteError(f"Ledger {path} was rewritten, not appended: first divergence at byte {offset}")


@contextlib.contextmanager
def suppress_ledger_failures() -> Iterator[None]:
    try:
        yield
    except LedgerRewriteError:
        raise
    except Exception as exc:
        logger.debug("Best-effort ledger write failed: %s", type(exc).__name__)
