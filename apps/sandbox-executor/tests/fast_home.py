"""An isolated ``HOME`` on a memory-backed filesystem, for run-path matrices that create and remove many directories.

The run path creates its pi-agent session dir with ``mkdtemp`` under ``HOME/.holon/tmp``, fills it and removes it with
``rmtree``, once per case. On an overlay filesystem (a container's root) each ``mkdir`` costs about a millisecond and
a few thousand cases spend most of their time there; on ``tmpfs`` the same production calls are about 500 times
cheaper. :func:`fast_home` only chooses where the isolated ``HOME`` lives: every case still runs the production
``mkdtemp`` and ``rmtree`` under it, and every path check is computed from the ``HOME`` it yields.

The directory is fresh, empty and unique per call (``mkdtemp``), so nothing is shared between tests or xdist workers,
and it is removed when the context exits. Without a writable ``tmpfs`` at ``root`` with room to spare (macOS, a host
without ``/dev/shm``), it falls back to ``fallback``, or the system temp dir when that is ``None``.
"""

import contextlib
import os
import shutil
import tempfile
from collections.abc import Iterator

MEMORY_ROOT = "/dev/shm"
MOUNT_TABLE = "/proc/self/mounts"
# Free space a memory-backed root must have; one isolated HOME holds a few small files at a time.
MIN_FREE_BYTES = 16 * 1024 * 1024


def memory_backed(root: str, mounts: str = MOUNT_TABLE) -> bool:
    """True when ``root`` is a ``tmpfs`` mount point in the mount table, writable and with ``MIN_FREE_BYTES`` free."""
    try:
        with open(mounts, encoding="utf-8") as handle:
            entries = [line.split() for line in handle]
        free = os.statvfs(root)
    except OSError:
        return False
    is_tmpfs = any(len(fields) >= 3 and fields[1] == root and fields[2] == "tmpfs" for fields in entries)
    return is_tmpfs and os.access(root, os.W_OK) and free.f_bavail * free.f_frsize >= MIN_FREE_BYTES


@contextlib.contextmanager
def fast_home(fallback: str | None = None, root: str = MEMORY_ROOT, mounts: str = MOUNT_TABLE) -> Iterator[str]:
    """Yield a fresh, empty directory under ``root`` if memory-backed, else under ``fallback``; removed on exit."""
    parent = root if memory_backed(root, mounts) else fallback
    home = tempfile.mkdtemp(prefix="holon-test-home-", dir=parent)
    try:
        yield home
    finally:
        shutil.rmtree(home, ignore_errors=True)
