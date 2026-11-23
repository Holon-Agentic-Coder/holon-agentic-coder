"""Routing of forwarded container variables off the docker argv: the 0600 `--env-file` or the docker client env."""

from __future__ import annotations

from sandbox_executor.redaction import carries_url_password, is_secret_env_name, redact_command


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


def is_env_file_item(key: str, value: str) -> bool:
    """Decide whether a forwarded container variable travels in the 0600 `--env-file` rather than on the argv.

    Every `HOLON_*` value a docker env-file can carry goes there whatever its name or shape, so no setting fed a
    credential reaches `ps`, /proc/<pid>/cmdline or the `Executing:` echo. A `HOLON_*` value the env-file cannot
    carry (a line break or a non-UTF-8 byte) is a passthrough item instead (see `is_passthrough_env_item`). Any
    other name keeps the credential rule of `is_secret_env_item`.

    Args:
        key: The variable name.
        value: The variable value.

    Returns:
        True when the item must be handed over through the `--env-file`.
    """
    if key.startswith("HOLON_"):
        return _fits_env_file(value)
    return is_secret_env_item(key, value)


def is_passthrough_env_item(key: str, value: str) -> bool:
    """Tell whether a `HOLON_*` value goes on the argv as a bare `-e NAME`, its value set in the docker client env.

    Docker copies a bare `-e NAME` from its own environment, so a value no env-file line can carry still never
    appears on the argv or in the `Executing:` echo.

    Args:
        key: The variable name.
        value: The variable value.

    Returns:
        True for a `HOLON_*` value with a line break or a non-UTF-8 byte.
    """
    return key.startswith("HOLON_") and not _fits_env_file(value)


def _fits_env_file(value: str) -> bool:
    """Tell whether a docker env-file line can carry ``value`` verbatim: no line break and valid UTF-8."""
    if "\n" in value or "\r" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True
