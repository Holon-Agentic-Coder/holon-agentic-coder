"""Discovery and type mapping of the claude runner's credential.

The claude runner never consults ``HOLON_AGENT_KEY``. An explicit ``CLAUDE_CODE_OAUTH_TOKEN`` or
``ANTHROPIC_API_KEY`` wins; then ``HOLON_AGENT_OAUTH_TOKEN`` (filled from the layered configuration, see
``config_env``); otherwise the value is looked up in the OS keychain (``claude_keychain``), and the value's format
decides which in-container variable carries it. No stored credential is read from a plaintext file: the retired
token file is only ever opened by its one-time import (``claude_token_migration``). The value is never logged: warnings
name the path, the mode or the variable only.
"""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Mapping, MutableMapping

from sandbox_executor import claude_keychain, local_llm

OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
API_KEY_ENV = "ANTHROPIC_API_KEY"
CLAUDE_CREDENTIAL_ENV_NAMES: tuple[str, ...] = (OAUTH_TOKEN_ENV, API_KEY_ENV)
# The layered-configuration route: the host forwards the token under this name in the 0600 env-file and the image's
# role dispatcher maps it to CLAUDE_CODE_OAUTH_TOKEN, so exactly one copy of the credential is forwarded.
HOLON_OAUTH_TOKEN_ENV = "HOLON_AGENT_OAUTH_TOKEN"
OAUTH_TOKEN_PREFIX = "sk-ant-oat"
SECRETS_FILE_DISPLAY = "~/.holon/secrets/claude-oauth-token"


def classify_credential(value: str) -> str:
    """Name the in-container variable that must carry ``value``, decided by its format."""
    return OAUTH_TOKEN_ENV if value.startswith(OAUTH_TOKEN_PREFIX) else API_KEY_ENV


_WARNED_FORMAT: set[str] = set()


def loose_mode_warning(path: str) -> str | None:
    """The warning for a file whose mode has group or other bits set, or None; it names the path and mode only."""
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return None
    if mode & 0o077:
        return f"Warning: {path} has mode {mode:04o}; expected 0600. The file is left unchanged."
    return None


def read_token_file(path: str) -> tuple[str | None, str | None]:
    """Read a single credential token from ``path``.

    Args:
        path: The token file.

    Returns:
        ``(token, warning)``: the trimmed token, or None when the file is missing, unreadable, empty or holds more
        than one token; the warning is set only in the last case and names the path, never the content.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            value = handle.read().strip()
    except (OSError, UnicodeDecodeError):
        return None, None
    if not value:
        return None, None
    if len(value.split()) != 1:
        return None, (
            f"Warning: {path} must hold a single credential token with no spaces or extra lines; the file is ignored."
        )
    return value, None


def resolve_claude_credential(env: Mapping[str, str] | None = None) -> tuple[str, str] | None:
    """Resolve the claude credential as a ``(variable name, value)`` pair.

    An explicit environment value wins (the subscription token first), else the OS keychain is asked. Wherever the
    value came from, its format decides the variable that carries it, so callers must drop both claude variable names
    from the forwarded env before setting the pair.
    ``HOLON_AGENT_KEY`` is never consulted.

    Args:
        env: The environment to inspect; defaults to ``os.environ``.

    Returns:
        The pair, or None when no credential is available.
    """
    resolved = resolve_claude_credential_source(env)
    return (resolved[1], resolved[2]) if resolved else None


def resolve_claude_credential_source(env: Mapping[str, str] | None = None) -> tuple[str, str, str] | None:
    """Resolve the claude credential as ``(source, variable name, value)``.

    The source is the environment variable the value came from (``CLAUDE_CODE_OAUTH_TOKEN``, ``ANTHROPIC_API_KEY``
    or ``HOLON_AGENT_OAUTH_TOKEN``) or ``"keychain"`` for the OS keychain entry, in that order of precedence. The
    keychain is asked only when no variable holds a value; an empty store and an unusable one both give None.

    Args:
        env: The environment to inspect; defaults to ``os.environ``.

    Returns:
        The triple, or None when no credential is available.
    """
    source = os.environ if env is None else env
    for name in CLAUDE_CREDENTIAL_ENV_NAMES:
        value = (source.get(name) or "").strip()
        if value:
            return name, classify_credential(value), value
    layered = (source.get(HOLON_OAUTH_TOKEN_ENV) or "").strip()
    if layered:
        _warn_if_not_oauth(layered)
        return HOLON_OAUTH_TOKEN_ENV, OAUTH_TOKEN_ENV, layered
    stored = claude_keychain.lookup_token()
    if stored.status is not claude_keychain.LookupStatus.FOUND:
        return None
    return "keychain", classify_credential(stored.token), stored.token


def _warn_if_not_oauth(value: str) -> None:
    """Warn once, naming the variable and never the value, when the layered token is not a subscription token."""
    if value.startswith(OAUTH_TOKEN_PREFIX) or HOLON_OAUTH_TOKEN_ENV in _WARNED_FORMAT:
        return
    _WARNED_FORMAT.add(HOLON_OAUTH_TOKEN_ENV)
    # The variable names are spelled out: a static scan reads the constants' names as sensitive data.
    print(
        "Warning: HOLON_AGENT_OAUTH_TOKEN is not a subscription OAuth token; it is forwarded as "
        "CLAUDE_CODE_OAUTH_TOKEN all the same. Set ANTHROPIC_API_KEY for an API key instead.",
        file=sys.stderr,
    )


def pin_claude_credential(env_to_forward: MutableMapping[str, str], agent_id: str) -> None:
    """Leave exactly one copy of the resolved claude credential in a container env, under the name it must travel as.

    For the claude runner, ``HOLON_AGENT_KEY`` is dropped and, when a credential resolves, every claude credential
    name is replaced: a layered ``HOLON_AGENT_OAUTH_TOKEN`` travels as itself (the image's role dispatcher maps it to
    ``CLAUDE_CODE_OAUTH_TOKEN``), any other credential under the name its format decides. Any other runner never
    receives ``HOLON_AGENT_OAUTH_TOKEN``. Shared by the role-container launch and the pre-flight probe.

    Args:
        env_to_forward: The container env, updated in place.
        agent_id: The runner or agent id the container hosts.
    """
    if local_llm.normalize_agent_id(agent_id) != "claude":
        env_to_forward.pop(HOLON_OAUTH_TOKEN_ENV, None)
        return
    env_to_forward.pop("HOLON_AGENT_KEY", None)
    resolved = resolve_claude_credential_source()
    if not resolved:
        return
    for name in (*CLAUDE_CREDENTIAL_ENV_NAMES, HOLON_OAUTH_TOKEN_ENV):
        env_to_forward.pop(name, None)
    source, name, value = resolved
    env_to_forward[HOLON_OAUTH_TOKEN_ENV if source == HOLON_OAUTH_TOKEN_ENV else name] = value
