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
STORE_COMMANDS: dict[str, str] = {
    "darwin": f"security add-generic-password -U -s {SERVICE} -a {ACCOUNT} -w",
    "linux": f'secret-tool store --label="{LABEL}" service {SERVICE} account {ACCOUNT}',
}
_STORE_NAMES: dict[str, str] = {"darwin": "the macOS keychain", "linux": "the Secret Service keyring"}
_LOOKUP_ARGV: dict[str, tuple[str, ...]] = {
    "darwin": ("security", "find-generic-password", "-s", SERVICE, "-a", ACCOUNT, "-w"),
    "linux": ("secret-tool", "lookup", "service", SERVICE, "account", ACCOUNT),
}
_MACOS_ITEM_NOT_FOUND = 44
# why: a token outside this set is never composed into the security -i command line read from stdin
_STORABLE_TOKEN_RE = re.compile(r"[A-Za-z0-9._~+/=-]+")


class LookupStatus(enum.Enum):
    FOUND = "found"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class KeychainLookup:
    status: LookupStatus
    token: str = field(default="", repr=False)


def current_platform() -> str:
    if sys.platform == "darwin":
        return "darwin"
    return "linux" if sys.platform.startswith("linux") else "unsupported"


def run_keychain_tool(argv: list[str], stdin_text: str | None = None) -> subprocess.CompletedProcess[str] | None:
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
    if proc.returncode == 0:
        return True
    if platform == "darwin":
        return proc.returncode == _MACOS_ITEM_NOT_FOUND
    return proc.returncode == 1 and not (proc.stderr or "").strip()


def lookup_token() -> KeychainLookup:
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


def _macos_interactive_store_input(token: str) -> str:
    return f"add-generic-password -U -s {SERVICE} -a {ACCOUNT} -w {token}\n"


def _run_store(platform: str, token: str) -> subprocess.CompletedProcess[str] | None:
    if platform == "darwin":
        return run_keychain_tool(["security", "-i"], _macos_interactive_store_input(token))
    if platform == "linux":
        argv = ["secret-tool", "store", f"--label={LABEL}", "service", SERVICE, "account", ACCOUNT]
        return run_keychain_tool(argv, token)
    return None


def is_storable_token(token: str) -> bool:
    return _STORABLE_TOKEN_RE.fullmatch(token) is not None


def store_token(token: str) -> bool:
    if not is_storable_token(token):
        return False
    proc = _run_store(current_platform(), token)
    if proc is None or proc.returncode != 0:
        return False
    stored = lookup_token()
    return stored.status is LookupStatus.FOUND and stored.token == token


def store_name() -> str:
    return _STORE_NAMES.get(current_platform(), "the OS secret store")


def store_command() -> str:
    return STORE_COMMANDS.get(current_platform(), "")


def store_commands_text() -> str:
    return f"macOS: {STORE_COMMANDS['darwin']}; Linux: {STORE_COMMANDS['linux']}"
