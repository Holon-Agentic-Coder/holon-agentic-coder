"""Patch a name at every flow submodule that looks it up, with one shared mock.

Since flow.py became a package, a name such as ``run_git`` is looked up in each submodule that imports it, so a patch of
``sandbox_executor.flow.run_git`` (the re-export) intercepts nothing. A test whose code path crosses several of those
submodules patches each one; sharing a single mock keeps call counts and recorded calls meaning what they meant before.
"""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any
from unittest.mock import DEFAULT, patch


class LookupSitesPatch:
    """Context manager (with ``start``/``stop``) that patches each dotted target with the same object."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        targets = [arg for arg in args if isinstance(arg, str)]
        rest = args[len(targets) :]
        if not targets or len(rest) > 1 or any(isinstance(arg, str) for arg in rest):
            raise TypeError("patch_lookup_sites(target, ..., [new], **kwargs)")
        self._targets = targets
        self._new = rest[0] if rest else kwargs.pop("new", DEFAULT)
        self._kwargs = kwargs
        self._stack: ExitStack | None = None

    def start(self) -> Any:
        """Patch every target and return the shared mock (or ``new``)."""
        self._stack = ExitStack()
        first, *others = self._targets
        shared = self._stack.enter_context(patch(first, self._new, **self._kwargs))
        for target in others:
            self._stack.enter_context(patch(target, shared))
        return shared

    def stop(self) -> None:
        """Undo every patch."""
        if self._stack is not None:
            self._stack.close()
            self._stack = None

    def __enter__(self) -> Any:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


def patch_lookup_sites(*args: Any, **kwargs: Any) -> LookupSitesPatch:
    """Patch each dotted target with one shared mock, used like ``unittest.mock.patch``.

    The leading string arguments are the targets; an optional positional argument after them is ``new``, as for
    ``patch``. Keyword arguments (``return_value``, ``side_effect`` and so on) configure the shared mock.
    """
    return LookupSitesPatch(*args, **kwargs)
