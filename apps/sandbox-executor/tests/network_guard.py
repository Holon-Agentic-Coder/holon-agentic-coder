"""Suite-wide guard that blocks git network commands against real remotes and inet socket connections.

A test that runs ``git fetch``, ``pull``, ``push`` or ``ls-remote`` against a real remote passes on a host with
network access and fails in the review-loop container, which has no ssh binary and an ssh-URL origin. The guard makes
the same test fail on the host. A remote that is a filesystem path or a ``file://`` URL (a local bare remote built in
``tmp_path``) stays allowed, as do ``AF_UNIX`` sockets and loopback connections.

Like ``tests/gh_write_guard.py`` it wraps ``subprocess.Popen``, so a test that patches ``subprocess.run`` or a helper
replaces the guarded call with its own mock and never reaches the guard.
"""

import ipaddress
import os
import re
import shlex
import socket
import subprocess
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

NETWORK_SUBCOMMANDS = frozenset({"fetch", "pull", "push", "ls-remote"})
# Global git options that take a separate value, so the value is not mistaken for the subcommand.
_GLOBAL_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"})
# Subcommand options that take a separate value, so the value is not mistaken for the remote.
_SUBCOMMAND_VALUE_OPTIONS = frozenset(
    {"--receive-pack", "--exec", "--upload-pack", "-o", "--push-option", "--depth", "--deepen", "--shallow-since"}
    | {"--shallow-exclude", "--refmap", "-j", "--jobs", "--server-option", "--negotiation-tip", "--recurse-submodules"}
)
_SCP_LIKE = re.compile(r"^(?:[\w.+-]+@)?[\w.-]+:(?!//)")
# The unpatched ``Popen``, for the guard's own ``git ls-remote --get-url`` probe (no network, no recursion).
_REAL_POPEN = subprocess.Popen
_LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})


class NetworkBlockedError(RuntimeError):
    """Raised instead of running a git network command against a real remote or opening an inet connection."""


def _argv(args: Any, shell: bool) -> list[str]:
    if isinstance(args, (str, bytes, os.PathLike)):
        text = os.fsdecode(args)
        return shlex.split(text) if shell else [text]
    return [os.fsdecode(a) for a in args]


def split_git_argv(argv: Sequence[str]) -> tuple[str | None, list[str], str | None]:
    """Return ``(subcommand, arguments after it, -C directory)`` for a ``git`` argv, skipping global options."""
    directory: str | None = None
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok in _GLOBAL_VALUE_OPTIONS and i + 1 < len(argv):
            directory = argv[i + 1] if tok == "-C" else directory
            i += 2
        elif tok.startswith("-"):
            i += 1
        else:
            return tok, list(argv[i + 1 :]), directory
    return None, [], directory


def remote_argument(subcommand: str, rest: Sequence[str]) -> str:
    """The remote a network subcommand targets: its first positional argument, otherwise ``origin``."""
    skip = False
    for tok in rest:
        if skip:
            skip = False
        elif tok == "--":
            continue
        elif tok in _SUBCOMMAND_VALUE_OPTIONS:
            skip = True
        elif not tok.startswith("-"):
            return tok
    return "origin"


def is_local_url(url: str) -> bool:
    """True for a filesystem path, a ``file://`` URL or a URL to a loopback host; anything else is not."""
    if url.startswith("file://"):
        return True
    if "://" in url:
        return is_loopback_host(urlsplit(url).hostname or "-")
    return url.startswith(("/", ".", "~")) or not _SCP_LIKE.match(url)


def _resolved_url(remote: str, directory: str | None, env: Any) -> str:
    """Resolve a remote name (``insteadOf`` applied) to its URL; a literal URL or path is returned as is."""
    if "/" in remote or ":" in remote or os.path.isdir(remote):
        return remote
    probe = _REAL_POPEN(
        ["git", "ls-remote", "--get-url", remote],
        cwd=directory,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    out, _ = probe.communicate()
    return out.strip() or remote if probe.returncode == 0 else remote


def git_network_violation(args: Any, shell: bool = False, cwd: Any = None, env: Any = None) -> str | None:
    """Return a description of ``args`` when it is a git network command against a non-local remote, else None."""
    try:
        argv = _argv(args, shell)
    except (TypeError, ValueError):
        return None
    if not argv or os.path.basename(argv[0]) != "git":
        return None
    subcommand, rest, directory = split_git_argv(argv)
    if subcommand not in NETWORK_SUBCOMMANDS:
        return None
    base = os.path.join(os.fsdecode(cwd), directory) if cwd and directory else directory or (cwd and os.fsdecode(cwd))
    url = _resolved_url(remote_argument(subcommand, rest), base or None, env)
    if is_local_url(url):
        return None
    return f"git {subcommand} against non-local remote {url!r}"


def is_loopback_host(host: Any) -> bool:
    """True for the loopback and unspecified addresses and names, and for an empty or absent host."""
    if host in (None, "", b""):
        return True
    text = os.fsdecode(host).strip("[]").lower()
    if text in _LOOPBACK_NAMES:
        return True
    try:
        address = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def guarded_popen(blocked: list[str]) -> type[subprocess.Popen]:  # type: ignore[type-arg]
    """Return a ``Popen`` subclass of the current ``Popen`` that records and refuses git network commands."""
    base = subprocess.Popen

    class _NetworkGuardedPopen(base):  # type: ignore[valid-type, misc]
        def __init__(self, args: Any, *a: Any, **kw: Any) -> None:
            shell = bool(kw.get("shell", a[7] if len(a) > 7 else False))
            violation = git_network_violation(args, shell=shell, cwd=kw.get("cwd"), env=kw.get("env"))
            if violation is not None:
                blocked.append(violation)
                raise NetworkBlockedError(f"Git network command blocked by the test suite: {violation}")
            super().__init__(args, *a, **kw)

    return _NetworkGuardedPopen


def guarded_socket_calls(blocked: list[str]) -> dict[str, Any]:
    """Return replacements for the ``socket`` functions that reach a non-loopback inet host, keyed by attribute."""
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def refuse(what: str) -> None:
        blocked.append(what)
        raise NetworkBlockedError(f"Network access blocked by the test suite: {what}")

    def check_connect(sock: socket.socket, address: Any) -> None:
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not is_loopback_host(address[0]):
            refuse(f"connect to {address!r}")

    def connect(self: socket.socket, address: Any) -> Any:
        check_connect(self, address)
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> Any:
        check_connect(self, address)
        return real_connect_ex(self, address)

    def getaddrinfo(host: Any, *a: Any, **kw: Any) -> Any:
        if not is_loopback_host(host):
            refuse(f"name lookup of {host!r}")
        return real_getaddrinfo(host, *a, **kw)

    return {"socket.connect": connect, "socket.connect_ex": connect_ex, "getaddrinfo": getaddrinfo}
