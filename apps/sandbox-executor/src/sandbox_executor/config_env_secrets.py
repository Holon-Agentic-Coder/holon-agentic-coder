"""Secrets referenced by file in the layered configuration, and the warnings that guard the layer files.

A layer names a credential by the path of its token file (``HOLON_AGENT_OAUTH_TOKEN_FILE``); the file is read on the
host into the value variable of the same layer (``HOLON_AGENT_OAUTH_TOKEN``), which reaches a role container only
through the 0600 docker env-file, where the image's role dispatcher maps it to the vendor variable. Every warning
names a path, a mode or a key, never a value. The only subprocess is a local, read-only ``git ls-files``.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable, Mapping

from sandbox_executor import claude_credential

# (file key, value key, in-container variable): the file key names a token file, whose single token becomes the value
# key in the same layer; the role dispatcher exports it under the in-container variable.
SECRET_FILE_MAPPINGS: tuple[tuple[str, str, str], ...] = (
    ("HOLON_AGENT_OAUTH_TOKEN_FILE", claude_credential.HOLON_OAUTH_TOKEN_ENV, claude_credential.OAUTH_TOKEN_ENV),
)
_GIT_TIMEOUT_SECONDS = 10.0


def expand_secret_files(layer_values: Mapping[str, str], origin: str) -> tuple[dict[str, str], list[str]]:
    """Read every referenced token file of one layer into the value key of that same layer.

    A value key the layer defines itself is kept. An empty file key (``KEY=``) references nothing.

    Args:
        layer_values: The accepted keys of the layer.
        origin: The layer file, named in warnings.

    Returns:
        The layer values with the derived value keys added, and the warnings (path, mode or key only).
    """
    values = dict(layer_values)
    warnings: list[str] = []
    for file_key, value_key, _container_key in SECRET_FILE_MAPPINGS:
        reference = values.get(file_key, "")
        if not reference or value_key in values:
            continue
        path = os.path.expanduser(reference)
        token, warning = claude_credential.read_token_file(path)
        if warning:
            warnings.append(warning)
        if token is None:
            if not warning:
                warnings.append(f"Warning: {origin}: {file_key} names {path}, which is missing, unreadable or empty.")
            continue
        warnings.extend(permission_warnings(path))
        values[value_key] = token
    return values, warnings


def permission_warnings(path: str) -> list[str]:
    """The warning for a layer or token file that is group- or world-accessible; the file is never changed."""
    warning = claude_credential.loose_mode_warning(path)
    return [warning] if warning else []


def tracked_secret_warning(project_path: str, secret_keys: Iterable[str]) -> list[str]:
    """Warn when a project ``.env`` that defines secret keys is tracked by git.

    Args:
        project_path: The project layer file.
        secret_keys: The secret keys the file defines.

    Returns:
        One warning naming the path and the keys, or none when there is no secret key, the file is untracked or git
        cannot tell.
    """
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
