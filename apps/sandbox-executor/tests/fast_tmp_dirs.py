"""Constant-time temporary directories for the test suite.

pytest's numbered directories (``tmp_path`` and ``tmp_path_factory.mktemp``) come from
``_pytest.pathlib.make_numbered_dir``, which lists the whole root with ``os.scandir`` on every call to find the next
free number. Every test adds entries to its xdist worker's base temp, so the last test of a worker scans ten thousand
entries several times and the suite's cost grows quadratically with the tests per worker.

``NumberedDirAllocator`` replaces that helper for the session (``install_fast_numbered_dirs``): it scans a
``(root, prefix)`` pair once, then counts up. ``os.mkdir`` is the atomic claim, and a name that already exists (a
foreign writer, a case-insensitive file system) triggers a rescan and a retry, so a name is never reused.
``new_private_test_dir`` gives each test one fresh private directory under ``<base temp>/isolated`` for the
autouse fixtures that only need an empty directory, so they add no entry to the base temp itself.
"""

import os
import threading
from collections.abc import Callable
from pathlib import Path

import _pytest.tmpdir
from _pytest.pathlib import find_suffixes, parse_num

#: Attempts before giving up on a prefix, as pytest's own ``make_numbered_dir`` does.
MAX_ATTEMPTS = 10

#: Sub-directory of the worker base temp that holds the per-test private directories.
PRIVATE_ROOT_NAME = "isolated"


def next_free_number(root: Path, prefix: str) -> int:
    """One past the highest numeric suffix of the entries of ``root`` named ``<prefix><n>``, by pytest's own rule."""
    return max(map(parse_num, find_suffixes(root, prefix)), default=-1) + 1


class NumberedDirAllocator:
    """Drop-in for ``make_numbered_dir(root, prefix, mode)`` that scans each ``(root, prefix)`` pair only once."""

    def __init__(self) -> None:
        """Start with no known pair; the first call for a pair seeds its counter with one scan."""
        self._next: dict[tuple[Path, str], int] = {}
        self._lock = threading.Lock()

    def __call__(self, root: Path, prefix: str, mode: int = 0o700) -> Path:
        """Create and return ``root/<prefix><n>`` for an ``n`` no directory of ``root`` has used.

        Raises:
            OSError: ``MAX_ATTEMPTS`` names in a row already existed, or the directory cannot be created.
        """
        root = Path(root)
        key = (root, prefix)
        with self._lock:
            number = self._next.get(key)
            if number is None:
                number = next_free_number(root, prefix)
            for _attempt in range(MAX_ATTEMPTS):
                path = root / f"{prefix}{number}"
                try:
                    os.mkdir(path, mode)
                except FileExistsError:
                    number = max(number + 1, next_free_number(root, prefix))
                    continue
                self._next[key] = number + 1
                return path
        raise OSError(f"could not create numbered dir with prefix {prefix} in {root} after {MAX_ATTEMPTS} tries")


def install_fast_numbered_dirs() -> Callable[[], None]:
    """Make pytest's ``tmp_path`` and ``tmp_path_factory.mktemp`` use a fresh ``NumberedDirAllocator``.

    Only the reference ``_pytest.tmpdir`` calls is replaced, so the base temp's own creation and retention stay
    pytest's. Fails with ``AttributeError`` if a pytest upgrade removed the hook point.

    Returns:
        A function that puts pytest's original helper back.
    """
    original = _pytest.tmpdir.make_numbered_dir
    _pytest.tmpdir.make_numbered_dir = NumberedDirAllocator()

    def restore() -> None:
        _pytest.tmpdir.make_numbered_dir = original

    return restore


class PrivateTestDirs:
    """Creates one new, empty, private (``0o700``) directory per call under ``<base temp>/isolated``."""

    def __init__(self, allocate: NumberedDirAllocator | None = None) -> None:
        """Use ``allocate`` (a new allocator by default) for the ``t<n>`` names."""
        self._allocate = allocate or NumberedDirAllocator()
        self._roots: set[Path] = set()

    def new(self, base_temp: Path) -> Path:
        """Return a new directory ``<base_temp>/isolated/t<n>`` that no earlier call or earlier run has used."""
        root = Path(base_temp) / PRIVATE_ROOT_NAME
        if root not in self._roots:
            root.mkdir(mode=0o700, exist_ok=True)
            self._roots.add(root)
        return self._allocate(root, "t")


_private_test_dirs = PrivateTestDirs()


def new_private_test_dir(base_temp: Path) -> Path:
    """Return a new private directory for one test under ``base_temp``, in constant time."""
    return _private_test_dirs.new(base_temp)
