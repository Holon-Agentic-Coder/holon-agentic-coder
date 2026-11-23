"""Owner-only credential env-files handed to `docker run --env-file`, so no secret travels on the argv."""

import contextlib
import logging
import os
import tempfile
import time

from sandbox_executor.redaction import carries_url_password, is_secret_env_name, redact_command

logger = logging.getLogger(__name__)

# Credential env-files live in an owner-only directory, apart from ~/.holon/tmp itself, which also
# holds directories that are bind-mounted into the container.
ENV_FILE_PREFIX = "holon-env-"
_ENV_FILE_STALE_SECONDS = 24 * 60 * 60


def env_file_dir() -> str:
    """Return the directory for temporary credential env-files, creating it owner-only (0700).

    Returns:
        The absolute path of `~/.holon/tmp/env-files`. Looser permission bits on an existing
        directory are tightened back to 0700.
    """
    path = os.path.expanduser(os.path.join("~", ".holon", "tmp", "env-files"))
    os.makedirs(path, mode=0o700, exist_ok=True)
    if os.stat(path).st_mode & 0o077:
        os.chmod(path, 0o700)
    return path


def sweep_stale_env_files(directory: str, max_age_seconds: float = _ENV_FILE_STALE_SECONDS) -> None:
    """Best-effort removal of credential env-files left behind by a killed run.

    Args:
        directory: The env-file directory to sweep.
        max_age_seconds: Files whose modification time is older than this are removed.
    """
    cutoff = time.time() - max_age_seconds
    try:
        entries = os.listdir(directory)
    except OSError:
        return
    for name in entries:
        if not name.startswith(ENV_FILE_PREFIX):
            continue
        path = os.path.join(directory, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                logger.debug("Removed stale credential env-file %s", path)
        except OSError as exc:
            logger.debug("Could not sweep env-file %s: %s", path, exc)


def write_env_file(env: dict[str, str]) -> str:
    """Write credential variables to a new 0600 docker `--env-file`, one `KEY=VALUE` line each.

    Docker reads the file on the client side and applies no quoting, so a value containing a line
    break would be cut short or smuggle in a second variable; such values are rejected.

    Args:
        env: The variables to write, which are emitted in sorted key order.

    Returns:
        The path of the new file. The caller owns it and must remove it.

    Raises:
        ValueError: If a value contains a line break. The message names the key, never the value.
    """
    for key, value in env.items():
        if "\n" in value or "\r" in value:
            raise ValueError(f"the value of {key} contains a line break and cannot be passed to the container")
    fd, path = tempfile.mkstemp(prefix=ENV_FILE_PREFIX, dir=env_file_dir())
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.writelines(f"{key}={value}\n" for key, value in sorted(env.items()))
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)
        raise
    return path


def is_secret_env_item(key: str, value: str) -> bool:
    """Decide whether a container variable must travel in the 0600 `--env-file`, not on the argv.

    Args:
        key: The variable name.
        value: The variable value.

    Returns:
        True for a credential-bearing name, a `KEY=VALUE` item the command redactor would mask, or a
        value with a URL password (a proxy URL in any scheme, or none), whatever its name.
    """
    item = f"{key}={value}"
    return is_secret_env_name(key) or redact_command([item]) != item or carries_url_password(value)
