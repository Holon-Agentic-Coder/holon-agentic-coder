"""LedgerLock and atomic append primitives ensuring append-only persistence invariants."""

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


def _get_ledger_module() -> Any:
    import sandbox_executor.ledger as _l

    return _l


class LedgerLock:
    """Reusable advisory file locking context manager for ledger mutation safety.

    Guarantees atomic append semantics across concurrent processes and threads
    using POSIX advisory locking (fcntl.flock with LOCK_EX | LOCK_NB) with bounded
    exponential backoff, randomised jitter, and configurable timeout.

    Flushes and fsyncs the file descriptor on lock release, ensuring durability.
    Gracefully falls back on platforms or filesystems without flock support.
    """

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

    def __enter__(self) -> TextIO:
        dirname = os.path.dirname(os.path.abspath(self.filepath))
        if dirname:
            os.makedirs(dirname, exist_ok=True)

        _l = _get_ledger_module()
        _fcntl = getattr(_l, "fcntl", fcntl)
        use_posix_lock = sys.platform != "win32" and _fcntl is not None and hasattr(_fcntl, "flock")
        self._file = open(self.filepath, "a", encoding="utf-8")

        if not use_posix_lock:
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

        start_time = time.monotonic()
        delay = self.initial_delay
        while True:
            try:
                _fcntl.flock(self._fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                self._locked = True
                break
            except (BlockingIOError, OSError) as e:
                if isinstance(e, OSError) and e.errno in (errno.ENOSYS, getattr(errno, "EOPNOTSUPP", 45)):
                    logger.warning(
                        "Advisory file locking not supported on filesystem for %s; "
                        "falling back to uncoordinated append",
                        self.filepath,
                    )
                    break
                if (
                    isinstance(e, OSError)
                    and e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK)
                    and not isinstance(e, BlockingIOError)
                ):
                    self._file.close()
                    raise
                if (time.monotonic() - start_time) >= self.timeout:
                    self._file.close()
                    raise TimeoutError(f"Timed out waiting for exclusive lock on ledger file: {self.filepath}") from e
                jitter = random.uniform(0.5, 1.5)
                sleep_time = min(delay * jitter, self.max_delay)
                time.sleep(sleep_time)
                delay = min(delay * 2, self.max_delay)

        return self._file

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._file is not None and not self._file.closed:
            try:
                self._file.flush()
                _l = _get_ledger_module()
                _os = getattr(_l, "os", os)
                _fcntl = getattr(_l, "fcntl", fcntl)
                if self._fd is not None:
                    with contextlib.suppress(OSError):
                        _os.fsync(self._fd)
                if self._locked and self._fd is not None:
                    with contextlib.suppress(OSError):
                        _fcntl.flock(self._fd, _fcntl.LOCK_UN)
                    self._locked = False
            finally:
                self._file.close()


class LedgerRewriteError(RuntimeError):
    """Raised when a ledger write changed bytes that were already in the file instead of only appending."""


def _read_ledger_bytes(path: str) -> bytes:
    """Return the bytes of a ledger file, treating a missing file as empty."""
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return b""


def _first_divergence(before: bytes, after: bytes) -> int:
    """Return the offset of the first byte at which ``after`` stops matching ``before``."""
    limit = min(len(before), len(after))
    return next((i for i in range(limit) if before[i] != after[i]), limit)


def assert_ledger_prefix(path: str, before: bytes) -> None:
    """Fail closed unless the ledger's pre-write bytes are a byte prefix of its current bytes.

    Args:
        path: Path to the ledger file.
        before: Bytes of the ledger captured before the write.

    Raises:
        LedgerRewriteError: When the write rewrote, reordered or truncated existing bytes.
    """
    after = _read_ledger_bytes(path)
    if not after.startswith(before):
        offset = _first_divergence(before, after)
        raise LedgerRewriteError(f"Ledger {path} was rewritten, not appended: first divergence at byte {offset}")


@contextlib.contextmanager
def atomic_ledger_append(
    filepath: str,
    timeout: float = 10.0,
    initial_delay: float = 0.02,
    max_delay: float = 0.5,
) -> Iterator[TextIO]:
    """Atomic context manager for appending records to a ledger file with advisory locking.

    The bytes present when the lock is taken must remain a byte prefix of the file once the caller's block has
    written; otherwise ``LedgerRewriteError`` is raised.
    """
    with LedgerLock(
        filepath=filepath,
        timeout=timeout,
        initial_delay=initial_delay,
        max_delay=max_delay,
    ) as f:
        before = _read_ledger_bytes(filepath)
        yield f
        f.flush()
        _l = _get_ledger_module()
        assert_fn = getattr(_l, "assert_ledger_prefix", assert_ledger_prefix)
        assert_fn(filepath, before)


@contextlib.contextmanager
def suppress_ledger_failures() -> Iterator[None]:
    """Suppress a failed best-effort ledger write, except ``LedgerRewriteError``, which always propagates.

    Use it in place of ``contextlib.suppress(Exception)`` around a ledger write, so a rewritten ledger stops the stage
    instead of being committed and pushed.
    """
    try:
        yield
    except LedgerRewriteError:
        raise
    except Exception as exc:
        logger.debug("Best-effort ledger write failed: %s", type(exc).__name__)
