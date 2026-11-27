from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable, Mapping

from sandbox_executor import claude_credential, claude_token_migration

# why: env names stay literals (not claude_credential constants) as .env.example renders this table; scans flag them
SECRET_FILE_MAPPINGS: tuple[tuple[str, str, str], ...] = (
    ("HOLON_AGENT_OAUTH_TOKEN_FILE", "HOLON_AGENT_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"),
)
PROJECT_REFUSED_KEYS: tuple[str, ...] = (
    "HOLON_ANTIGRAVITY_SESSION_DIR",
    "HOLON_AGENT_SETTINGS",
    "HOLON_AGENT_CONFIG",
    "HOLON_TOKEN_REDUCE",
    "HOLON_PASSIVE_MONITORING",
    "HOLON_PROXY_URL",
    "HOLON_PROXY_PORT",
    "HOLON_MITM_USER",
    "HOLON_MITM_WEB_PORT",
    "HOLON_MITM_WEB_PASSWORD",
    "HOLON_COHERENCE_IMAGE",
    "HOLON_LOCAL_BASE_URL",
    "HOLON_HOST_LOCAL_HOSTS",
    "HOLON_ROOT_CA_PATH",
    "HOLON_CA_BUNDLE_PATH",
    "HOLON_SECRET_BUNDLE_PATH",
    "GITHUB_TOKEN",
    "HOLON_AGENT_OAUTH_TOKEN",
    "HOLON_AGENT_KEY",
)
_GIT_TIMEOUT_SECONDS = 10.0


def _project_refused_key_names() -> tuple[str, ...]:
    file_keys = tuple(file_key for file_key, _value_key, _container_key in SECRET_FILE_MAPPINGS)
    return (*file_keys, *PROJECT_REFUSED_KEYS)


def _read_mapped_token_file(file_key: str, reference: str, origin: str) -> tuple[str | None, list[str]]:
    path = os.path.expanduser(reference)
    if claude_token_migration.is_legacy_token_file(path):
        return None, [
            f"Warning: {origin}: ignoring {file_key}: {path} is the retired plaintext claude token file, which is "
            "no longer read. Remove the reference; the claude credential is kept in the OS keychain."
        ]
    token, warning = claude_credential.read_token_file(path)
    warnings = [warning] if warning else []
    if token is None:
        if not warning:
            warnings.append(f"Warning: {origin}: {file_key} names {path}, which is missing, unreadable or empty.")
        return None, warnings
    warnings.extend(permission_warnings(path))
    return token, warnings


def expand_secret_files(layer_values: Mapping[str, str], origin: str) -> tuple[dict[str, str], list[str]]:
    values = dict(layer_values)
    warnings: list[str] = []
    for file_key, value_key, _container_key in SECRET_FILE_MAPPINGS:
        reference = values.get(file_key, "")
        if not reference or value_key in values:
            continue
        token, file_warnings = _read_mapped_token_file(file_key, reference, origin)
        warnings.extend(file_warnings)
        if token is not None:
            values[value_key] = token
    return values, warnings


def drop_host_references(layer_values: Mapping[str, str], origin: str) -> tuple[dict[str, str], list[str]]:
    values = dict(layer_values)
    warnings: list[str] = []
    for key in _project_refused_key_names():
        if values.pop(key, ""):
            warnings.append(
                f"Warning: {origin}: ignoring {key}: it may only be set in ~/.holon/.env or the process environment."
            )
    return values, warnings


def permission_warnings(path: str) -> list[str]:
    warning = claude_credential.loose_mode_warning(path)
    return [warning] if warning else []


def tracked_secret_warning(project_path: str, secret_keys: Iterable[str]) -> list[str]:
    keys = sorted(set(secret_keys))
    if not keys:
        return []
    root, name = os.path.split(os.path.abspath(project_path))
    try:
        completed = subprocess.run(
            ["git", "-C", root, "ls-files", "--error-unmatch", "--", name],
            capture_output=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    return [f"Warning: {project_path} is tracked by git and holds secret keys ({', '.join(keys)}); untrack it."]
