"""Discovery and type mapping of the claude runner's credential.

The claude runner never consults ``HOLON_AGENT_KEY``. An explicit ``CLAUDE_CODE_OAUTH_TOKEN`` or
``ANTHROPIC_API_KEY`` wins; otherwise the value is read from the secrets file, and the value's format decides
which in-container variable carries it. The value is never logged: warnings name the path and the mode only.
"""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Mapping

OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
API_KEY_ENV = "ANTHROPIC_API_KEY"
CLAUDE_CREDENTIAL_ENV_NAMES: tuple[str, ...] = (OAUTH_TOKEN_ENV, API_KEY_ENV)
OAUTH_TOKEN_PREFIX = "sk-ant-oat"
SECRETS_FILE_DISPLAY = "~/.holon/secrets/claude-oauth-token"


def secrets_file_path() -> str:
    """Return the target-native path of the claude credential file under the current home."""
    return os.path.join(os.path.expanduser("~"), ".holon", "secrets", "claude-oauth-token")


def classify_credential(value: str) -> str:
    """Name the in-container variable that must carry ``value``, decided by its format."""
    return OAUTH_TOKEN_ENV if value.startswith(OAUTH_TOKEN_PREFIX) else API_KEY_ENV


def _warn_if_loose(path: str) -> None:
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return
    if mode & 0o077:
        print(
            f"Warning: {path} has mode {mode:04o}; expected 0600. The file is left unchanged.",
            file=sys.stderr,
        )


def read_secrets_file() -> str | None:
    """Read the trimmed credential from the secrets file, or None when it is missing, unreadable or empty.

    The file is never modified. A mode with group or other bits set draws a warning naming the path and mode.
    """
    path = secrets_file_path()
    try:
        with open(path, encoding="utf-8") as handle:
            value = handle.read().strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not value:
        return None
    _warn_if_loose(path)
    return value


def resolve_claude_credential(env: Mapping[str, str] | None = None) -> tuple[str, str] | None:
    """Resolve the claude credential as a ``(variable name, value)`` pair.

    An explicit environment value wins (the subscription token first), else the secrets file is read.
    ``HOLON_AGENT_KEY`` is never consulted.

    Args:
        env: The environment to inspect; defaults to ``os.environ``.

    Returns:
        The pair, or None when no credential is available.
    """
    source = os.environ if env is None else env
    for name in CLAUDE_CREDENTIAL_ENV_NAMES:
        value = (source.get(name) or "").strip()
        if value:
            return name, value
    from_file = read_secrets_file()
    return (classify_credential(from_file), from_file) if from_file else None
