"""An in-memory stand-in for the OS secret store, so that no test reaches a real ``security`` or ``secret-tool``.

``install`` replaces the single subprocess seam ``claude_keychain.run_keychain_tool`` and the platform switch. The fake
emulates the four command lines of the contract (lookup and store, on macOS and on Linux) with their exit codes and
raises ``AssertionError`` for any other argument list.
"""

import shlex
import subprocess

import pytest
from sandbox_executor import claude_keychain

#: The real seam and platform switch, captured before any fixture replaces them; only their own tests call them, with
#: `subprocess.run` replaced.
REAL_RUN_KEYCHAIN_TOOL = claude_keychain.run_keychain_tool
REAL_CURRENT_PLATFORM = claude_keychain.current_platform

_LOOKUPS = {
    "darwin": ["security", "find-generic-password", "-s", "holon", "-a", "claude-oauth-token", "-w"],
    "linux": ["secret-tool", "lookup", "service", "holon", "account", "claude-oauth-token"],
}
_STORES = {
    "darwin": ["security", "-i"],
    "linux": [
        "secret-tool",
        "store",
        "--label=Holon claude credential",
        "service",
        "holon",
        "account",
        "claude-oauth-token",
    ],
}
_MACOS_STORE_LINE = ["add-generic-password", "-U", "-s", "holon", "-a", "claude-oauth-token", "-w"]
_MISSING_EXIT = {"darwin": 44, "linux": 1}


class FakeKeychain:
    """One optional entry in memory, plus switches for the failures a real store can show.

    Attributes:
        entry: The stored value, or None for an empty store.
        platform: ``"linux"`` (default), ``"darwin"`` or ``"unsupported"``.
        available: False makes every call behave like a missing tool or a time-out (the seam returns None).
        write_fails: The store command exits non-zero and stores nothing.
        write_is_silent_noop: The store command exits 0 and stores nothing.
        lookup_override: ``(exit code, stdout, stderr)`` returned by every lookup instead of the emulated answer.
        calls: Every call as ``(argument list, standard input)``.
    """

    def __init__(self) -> None:
        self.entry: str | None = None
        self.platform = "linux"
        self.available = True
        self.write_fails = False
        self.write_is_silent_noop = False
        self.lookup_override: tuple[int, str, str] | None = None
        self.calls: list[tuple[list[str], str | None]] = []

    @property
    def argvs(self) -> list[list[str]]:
        """The recorded argument lists, without the standard input."""
        return [argv for argv, _stdin in self.calls]

    @property
    def store_calls(self) -> list[tuple[list[str], str | None]]:
        """The recorded store commands."""
        return [call for call in self.calls if call[0] == _STORES.get(self.platform)]

    def run(self, argv: list[str], stdin_text: str | None = None) -> subprocess.CompletedProcess[str] | None:
        """Emulate ``claude_keychain.run_keychain_tool`` for the fake's platform."""
        argv = list(argv)
        self.calls.append((argv, stdin_text))
        if argv == _LOOKUPS.get(self.platform):
            assert stdin_text is None, "a lookup must not receive standard input"
            return self._lookup(argv) if self.available else None
        if argv == _STORES.get(self.platform):
            assert stdin_text is not None, "a store must receive the value on standard input"
            return self._store(argv, stdin_text) if self.available else None
        # The standard input is left out of the message: it may hold the value.
        raise AssertionError(f"unexpected keychain command on {self.platform}: {argv}")

    def _lookup(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        if self.lookup_override is not None:
            return subprocess.CompletedProcess(argv, *self.lookup_override)
        if self.entry is None:
            stderr = "security: The specified item could not be found in the keychain.\n"
            exit_code = _MISSING_EXIT[self.platform]
            return subprocess.CompletedProcess(argv, exit_code, "", stderr if self.platform == "darwin" else "")
        return subprocess.CompletedProcess(argv, 0, self.entry + "\n", "")

    def _store(self, argv: list[str], stdin_text: str) -> subprocess.CompletedProcess[str]:
        value = stdin_text
        if self.platform == "darwin":
            assert stdin_text.endswith("\n") and stdin_text.count("\n") == 1, "security -i reads exactly one line"
            *command, value = shlex.split(stdin_text)
            assert command == _MACOS_STORE_LINE, "unexpected command line for security -i"
        if self.write_fails:
            return subprocess.CompletedProcess(argv, 1, "", "the store refused the write\n")
        if not self.write_is_silent_noop:
            self.entry = value
        return subprocess.CompletedProcess(argv, 0, "", "")


def install(monkeypatch: pytest.MonkeyPatch) -> FakeKeychain:
    """Replace the keychain seam and the platform switch with a new fake, and return it."""
    fake = FakeKeychain()
    monkeypatch.setattr(claude_keychain, "run_keychain_tool", fake.run)
    monkeypatch.setattr(claude_keychain, "current_platform", lambda: fake.platform)
    return fake
