from __future__ import annotations

import os
import stat
import sys
from collections.abc import Mapping, MutableMapping

from sandbox_executor import claude_keychain, local_llm

OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
API_KEY_ENV = "ANTHROPIC_API_KEY"
CLAUDE_CREDENTIAL_ENV_NAMES: tuple[str, ...] = (OAUTH_TOKEN_ENV, API_KEY_ENV)
HOLON_OAUTH_TOKEN_ENV = "HOLON_AGENT_OAUTH_TOKEN"
OAUTH_TOKEN_PREFIX = "sk-ant-oat"

_GROUP_OR_OTHER_MODE_MASK = 0o077
_WARNED_FORMAT: set[str] = set()


def classify_credential(value: str) -> str:
    return OAUTH_TOKEN_ENV if value.startswith(OAUTH_TOKEN_PREFIX) else API_KEY_ENV


def loose_mode_warning(path: str) -> str | None:
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return None
    if mode & _GROUP_OR_OTHER_MODE_MASK:
        return f"Warning: {path} has mode {mode:04o}; expected 0600. The file is left unchanged."
    return None


def read_token_file(path: str) -> tuple[str | None, str | None]:
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


def _explicit_env_credential(source: Mapping[str, str]) -> tuple[str, str, str] | None:
    for name in CLAUDE_CREDENTIAL_ENV_NAMES:
        value = (source.get(name) or "").strip()
        if value:
            return name, classify_credential(value), value
    return None


def resolve_claude_credential(env: Mapping[str, str] | None = None) -> tuple[str, str] | None:
    resolved = resolve_claude_credential_source(env)
    return (resolved[1], resolved[2]) if resolved else None


def resolve_claude_credential_source(env: Mapping[str, str] | None = None) -> tuple[str, str, str] | None:
    return resolve_claude_credential_with_lookup(env)[0]


def resolve_claude_credential_with_lookup(
    env: Mapping[str, str] | None = None,
) -> tuple[tuple[str, str, str] | None, claude_keychain.KeychainLookup | None]:
    source = os.environ if env is None else env
    explicit = _explicit_env_credential(source)
    if explicit is not None:
        return explicit, None
    layered = (source.get(HOLON_OAUTH_TOKEN_ENV) or "").strip()
    if layered:
        _warn_if_not_oauth(layered)
        return (HOLON_OAUTH_TOKEN_ENV, OAUTH_TOKEN_ENV, layered), None
    stored = claude_keychain.lookup_token()
    if stored.status is not claude_keychain.LookupStatus.FOUND:
        return None, stored
    return ("keychain", classify_credential(stored.token), stored.token), stored


def _warn_if_not_oauth(value: str) -> None:
    if value.startswith(OAUTH_TOKEN_PREFIX) or HOLON_OAUTH_TOKEN_ENV in _WARNED_FORMAT:
        return
    _WARNED_FORMAT.add(HOLON_OAUTH_TOKEN_ENV)
    print(
        "Warning: HOLON_AGENT_OAUTH_TOKEN is not a subscription OAuth token; it is forwarded as "
        "CLAUDE_CODE_OAUTH_TOKEN all the same. Set ANTHROPIC_API_KEY for an API key instead.",
        file=sys.stderr,
    )


def pin_claude_credential(env_to_forward: MutableMapping[str, str], agent_id: str) -> None:
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
