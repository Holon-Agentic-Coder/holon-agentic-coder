"""One ``make`` run per distinct argv, for the Makefile tests that read the same output in the same environment.

Several tests of ``tests/test_makefile.py`` start ``make`` with the same arguments in the same default mock
environment and only read its output (``make help``, ``make -n build-images``). :class:`SharedMakeRuns` runs each
argv once, on first use inside a test (after the autouse guards), and hands every later test the same result.

A result is a frozen :class:`MakeResult` of text and an exit code, so no test can change what another reads. The
cache belongs to one environment kind: a test that builds its own mock environment (a missing or unusable tool) runs
``make`` itself and never uses it.
"""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class MakeResult:
    returncode: int
    stdout: str
    stderr: str


class SharedMakeRuns:
    """``make`` results of one environment kind, keyed by the full argv."""

    def __init__(self, env_kind: str, run: Callable[[list[str]], subprocess.CompletedProcess[str]]) -> None:
        self.env_kind = env_kind
        self._run = run
        self._results: dict[tuple[str, ...], MakeResult] = {}

    def __call__(self, args: list[str]) -> MakeResult:
        """The result of ``make <args>`` in this environment kind, running make only the first time it is asked."""
        key = tuple(args)
        if key not in self._results:
            completed = self._run(list(key))
            self._results[key] = MakeResult(completed.returncode, completed.stdout, completed.stderr)
        return self._results[key]
