"""The OS secret store that holds the claude runner's credential.

The credential is one entry, service ``holon`` and account ``claude-oauth-token``, in the macOS keychain (through
``security``) or the Linux Secret Service (through ``secret-tool``). The token reaches a platform tool only on standard
input, never on a command line, and no tool output is ever printed or logged. ``run_keychain_tool`` is the only
function that starts either tool. This module imports the standard library only, so the in-container entrypoint can
import it without reaching the host flow engine.
"""

from __future__ import annotations

import enum
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

SERVICE = "holon"
ACCOUNT = "claude-oauth-token"
LABEL = "Holon claude credential"
LOOKUP_TIMEOUT_SECONDS = 10.0
STORE_TIMEOUT_SECONDS = 15.0
# What an operator types to store the credential. Both prompt for the token, so it is neither a command-line argument
# nor in the shell history.
STORE_COMMANDS: dict[str, str] = {
    "darwin": f"security add-generic-password -U -s {SERVICE} -a {ACCOUNT} -w",
    "linux": f'secret-tool store --label="{LABEL}" service {SERVICE} account {ACCOUNT}',
}
_STORE_NAMES: dict[str, str] = {"darwin": "the macOS keychain", "linux": "the Secret Service keyring"}
_LOOKUP_ARGV: dict[str, tuple[str, ...]] = {
    "darwin": ("security", "find-generic-password", "-s", SERVICE, "-a", ACCOUNT, "-w"),
    "linux": ("secret-tool", "lookup", "service", SERVICE, "account", ACCOUNT),
}
# `security` exits with this code when the item does not exist (errSecItemNotFound).
_MACOS_ITEM_NOT_FOUND = 44
# A token outside this set is never composed into the `security -i` command line read from standard input.
_STORABLE_TOKEN_RE = re.compile(r"[A-Za-z0-9._~+/=-]+")


class LookupStatus(enum.Enum):
    """The result of asking the OS secret store for the claude credential."""

    FOUND = "found"
    MISSING = "missing"  # the store answered and has no entry
    UNAVAILABLE = "unavailable"  # no usable store: tool missing, time-out, locked keychain, no secret service


@dataclass(frozen=True)
class KeychainLookup:
    """A lookup result; ``token`` is empty unless the status is ``FOUND`` and never appears in the ``repr``."""

    status: LookupStatus
    token: str = field(default="", repr=False)


def current_platform() -> str:
    """Return ``"darwin"``, ``"linux"`` or ``"unsupported"`` for the running operating system."""
    if sys.platform == "darwin":
        return "darwin"
    return "linux" if sys.platform.startswith("linux") else "unsupported"


def run_keychain_tool(argv: list[str], stdin_text: str | None = None) -> subprocess.CompletedProcess[str] | None:
    """Start ``security`` or ``secret-tool``; the only function that does.

    Args:
        argv: The argument list, run without a shell. It must never hold the token.
        stdin_text: The text the tool reads from standard input (the only way a token reaches it). Without it,
            standard input is closed, so the tool can never wait for the terminal.

    Returns:
        The completed process, or None when the binary is missing, the call times out or the operating system refuses
        it. The exception, the output and the input are never formatted or logged.
    """
    options: dict[str, Any] = (
        {"stdin": subprocess.DEVNULL, "timeout": LOOKUP_TIMEOUT_SECONDS}
        if stdin_text is None
        else {"input": stdin_text, "timeout": STORE_TIMEOUT_SECONDS}
    )
    try:
        return subprocess.run(argv, capture_output=True, text=True, check=False, **options)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _answers_no_entry(platform: str, proc: subprocess.CompletedProcess[str]) -> bool:
    """Return True when a lookup without a token on standard output means "the store has no entry"."""
    if proc.returncode == 0:
        return True
    if platform == "darwin":
        return proc.returncode == _MACOS_ITEM_NOT_FOUND
    return proc.returncode == 1 and not (proc.stderr or "").strip()


def lookup_token() -> KeychainLookup:
    """Look the claude credential up in the OS secret store of the current platform.

    Returns:
        ``FOUND`` with the trimmed token, ``MISSING`` when the store answered and has no entry, or ``UNAVAILABLE``
        when there is no usable store (on an unsupported platform no tool is started).
    """
    platform = current_platform()
    argv = _LOOKUP_ARGV.get(platform)
    if argv is None:
        return KeychainLookup(LookupStatus.UNAVAILABLE)
    proc = run_keychain_tool(list(argv))
    if proc is None:
        return KeychainLookup(LookupStatus.UNAVAILABLE)
    token = (proc.stdout or "").strip() if proc.returncode == 0 else ""
    if token:
        return KeychainLookup(LookupStatus.FOUND, token)
    return KeychainLookup(LookupStatus.MISSING if _answers_no_entry(platform, proc) else LookupStatus.UNAVAILABLE)


def _run_store(platform: str, token: str) -> subprocess.CompletedProcess[str] | None:
    """Hand ``token`` to the platform's store command on standard input; None when nothing could be started."""
    if platform == "darwin":
        # `security -i` reads the command itself from standard input, so the token is on no command line.
        return run_keychain_tool(["security", "-i"], f"add-generic-password -U -s {SERVICE} -a {ACCOUNT} -w {token}\n")
    if platform == "linux":
        argv = ["secret-tool", "store", f"--label={LABEL}", "service", SERVICE, "account", ACCOUNT]
        return run_keychain_tool(argv, token)
    return None


def is_storable_token(token: str) -> bool:
    """Return True when ``token`` is not empty and holds only ASCII letters, digits and ``._~+/=-``."""
    return _STORABLE_TOKEN_RE.fullmatch(token) is not None


def store_token(token: str) -> bool:
    """Store ``token`` as the claude credential and prove it by reading it back.

    Args:
        token: The credential. A value ``is_storable_token`` refuses is never handed to a tool.

    Returns:
        True only when a lookup after the write returns the identical token; the store command's exit code alone is
        never trusted.
    """
    if not is_storable_token(token):
        return False
    proc = _run_store(current_platform(), token)
    if proc is None or proc.returncode != 0:
        return False
    stored = lookup_token()
    return stored.status is LookupStatus.FOUND and stored.token == token


def store_name() -> str:
    """Name the secret store of the current platform, for messages."""
    return _STORE_NAMES.get(current_platform(), "the OS secret store")


def store_command() -> str:
    """Return the operator's store one-liner for the current platform, or an empty string on an unsupported one."""
    return STORE_COMMANDS.get(current_platform(), "")


def store_commands_text() -> str:
    """Return both store one-liners with their platform names, for readers whose host platform is not known."""
    return f"macOS: {STORE_COMMANDS['darwin']}; Linux: {STORE_COMMANDS['linux']}"
