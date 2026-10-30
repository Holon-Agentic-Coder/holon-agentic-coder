"""Suite-wide guard that blocks unmocked ``gh`` write commands.

A test that reaches the host ``gh`` with a write verb can comment on, review, edit or merge a real pull request
whenever the operator's environment points ``gh`` at a repository (for example ``GH_REPO``). Read-only calls stay
allowed because many tests exercise the ``gh`` fallback paths without a network.

The guard wraps ``subprocess.Popen``, which ``subprocess.run``, ``check_output``, ``call`` and ``check_call`` all
construct. A test that patches one of those entry points replaces the guarded call with its own mock, so it never
reaches the guard.
"""

import os
import shlex
import subprocess
from collections.abc import Sequence
from typing import Any

# Write verbs per ``gh`` command group.
GH_WRITE_VERBS: dict[str, frozenset[str]] = {
    "pr": frozenset({"comment", "review", "create", "edit", "merge", "close", "ready", "reopen"}),
    "issue": frozenset({"create", "comment", "edit", "close", "reopen", "delete"}),
    "repo": frozenset({"edit", "create", "delete", "rename", "archive"}),
}
# ``gh`` options that take a separate value, so the value is not mistaken for a command.
_VALUE_OPTIONS = frozenset({"-R", "--repo", "--hostname"})
# ``gh api`` options whose presence implies a POST unless the method is given explicitly.
_API_BODY_OPTIONS = ("-f", "-F", "--field", "--raw-field", "--input")


class GhWriteBlockedError(RuntimeError):
    """Raised instead of running an unmocked ``gh`` write command."""


def _argv(args: Any, shell: bool) -> list[str]:
    if isinstance(args, (str, bytes, os.PathLike)):
        text = os.fsdecode(args)
        return shlex.split(text) if shell else [text]
    return [os.fsdecode(a) for a in args]


def _api_method(rest: Sequence[str]) -> str:
    method: str | None = None
    implied_post = False
    for i, tok in enumerate(rest):
        if tok in ("-X", "--method") and i + 1 < len(rest):
            method = rest[i + 1]
        elif tok.startswith("--method="):
            method = tok.split("=", 1)[1]
        elif tok.startswith("-X") and len(tok) > 2:
            method = tok[2:]
        elif tok.startswith(_API_BODY_OPTIONS):
            # Covers ``-f k=v``, ``-fk=v``, ``--field=k=v``, ``--raw-field`` and ``--input``.
            implied_post = True
    if method:
        return method.upper()
    return "POST" if implied_post else "GET"


def _describe(argv: Sequence[str]) -> str:
    """Render ``argv`` on one short line: a review body can run to hundreds of lines."""
    text = shlex.join(argv).splitlines()[0]
    return text if len(text) <= 160 else f"{text[:157]}..."


def gh_write_command(args: Any, shell: bool = False) -> str | None:  # noqa: C901
    """Return a description of ``args`` when it is a ``gh`` write command, otherwise None."""
    try:
        argv = _argv(args, shell)
    except (TypeError, ValueError):
        return None
    if not argv or os.path.basename(argv[0]) != "gh":
        return None
    rest = argv[1:]
    positionals: list[str] = []
    skip = False
    for tok in rest:
        if skip:
            skip = False
            continue
        if tok in _VALUE_OPTIONS:
            skip = True
            continue
        if tok.startswith("-"):
            continue
        positionals.append(tok)
        if len(positionals) == 2:
            break
    if not positionals:
        return None
    group = positionals[0]
    if group == "api":
        method = _api_method(rest)
        return None if method == "GET" else f"gh api {method}: {_describe(argv)}"
    if len(positionals) == 2 and positionals[1] in GH_WRITE_VERBS.get(group, frozenset()):
        return f"gh {group} {positionals[1]}: {_describe(argv)}"
    return None


def guarded_popen(blocked: list[str]) -> type[subprocess.Popen]:
    """Return a ``Popen`` subclass that records and refuses ``gh`` write commands into ``blocked``."""

    class _GuardedPopen(subprocess.Popen):  # type: ignore[type-arg]
        def __init__(self, args: Any, *a: Any, **kw: Any) -> None:
            shell = bool(kw.get("shell", a[7] if len(a) > 7 else False))
            write = gh_write_command(args, shell=shell)
            if write is not None:
                blocked.append(write)
                raise GhWriteBlockedError(f"Unmocked gh write command blocked by the test suite: {write}")
            super().__init__(args, *a, **kw)

    return _GuardedPopen
