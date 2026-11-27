from __future__ import annotations

from contextlib import ExitStack
from typing import Any
from unittest.mock import DEFAULT, patch


# why: patching a flow re-export intercepts nothing; one mock shared by every lookup site keeps call counts meaningful.
class LookupSitesPatch:
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
        self._stack = ExitStack()
        first, *others = self._targets
        shared = self._stack.enter_context(patch(first, self._new, **self._kwargs))
        for target in others:
            self._stack.enter_context(patch(target, shared))
        return shared

    def stop(self) -> None:
        if self._stack is not None:
            self._stack.close()
            self._stack = None

    def __enter__(self) -> Any:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


def patch_lookup_sites(*args: Any, **kwargs: Any) -> LookupSitesPatch:
    return LookupSitesPatch(*args, **kwargs)
