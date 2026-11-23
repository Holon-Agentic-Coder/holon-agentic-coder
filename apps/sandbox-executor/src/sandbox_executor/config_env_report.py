"""The ``holon doctor`` configuration report and the ``.env.example`` text, rendered from the layered configuration.

A secret value is never rendered: its row shows ``set (<layer>)``. Like every other doctor message (configuration
values are withheld unless known to be safe), a non-secret value is shown only when it is plainly harmless: a finite
decimal or ``unlimited`` for a numeric key, otherwise a short value without a URL authority, ``@``, a token-like run or
a non-finite number, which the command redactor leaves unchanged. Any other value shows as
``set (<layer>; value withheld)``. The rendering reads only the configuration it is given, never ``os.environ``.
"""

from __future__ import annotations

import math
import re
from typing import Any

from sandbox_executor.config_env import FILE_LAYERS, LAYERS, RESOLVED_MARKER, is_secret_key
from sandbox_executor.config_env_keys import DOCUMENTED_KEYS
from sandbox_executor.config_env_model import EffectiveConfig, ResolvedSetting
from sandbox_executor.config_env_secrets import SECRET_FILE_MAPPINGS
from sandbox_executor.redaction import carries_url_password, redact_command

REPORT_HEADING = "Configuration (HOLON_* settings):"
_NUMERIC_SUFFIXES = ("_SECONDS", "_MINUTES", "_TIMEOUT", "_TOKENS", "_WINDOW", "_BYTES", "_PORT")
_NUMBER_RE = re.compile(r"\d{1,12}(?:\.\d{1,6})?")
_PLAIN_VALUE_RE = re.compile(r"[A-Za-z0-9/~][A-Za-z0-9._,:+/~-]{0,79}")
_TOKEN_RUN_RE = re.compile(r"[A-Za-z0-9]{20}")


def _plain_value(key: str, value: str) -> bool:
    """True when ``value`` may be printed: it cannot carry a credential, a URL or a non-finite number."""
    if key.endswith(_NUMERIC_SUFFIXES):
        return bool(_NUMBER_RE.fullmatch(value)) or value.lower() == "unlimited"
    if not _PLAIN_VALUE_RE.fullmatch(value) or "//" in value or _TOKEN_RUN_RE.search(value):
        return False
    try:
        if not math.isfinite(float(value)):
            return False
    except ValueError:
        pass
    item = f"{key}={value}"
    return redact_command([item]) == item and not carries_url_password(value)


def _display(setting: ResolvedSetting, secret: bool) -> tuple[str | None, str]:
    """``(value shown, display text)`` of one setting."""
    if setting.value is None:
        return None, f"unset ({setting.source})"
    if secret:
        return None, f"set ({setting.source})"
    if not _plain_value(setting.key, setting.value):
        return None, f"set ({setting.source}; value withheld)"
    return setting.value, setting.value


def setting_rows(config: EffectiveConfig) -> list[dict[str, Any]]:
    """One row per effective setting, sorted by key; ``value`` is None for a secret, withheld or unset setting."""
    rows = []
    for key in sorted(config.settings):
        setting = config.settings[key]
        secret = setting.secret or is_secret_key(key)
        value, display = _display(setting, secret)
        rows.append(
            {
                "key": key,
                "value": value,
                "display": display,
                "source": setting.source,
                "shadowed": list(setting.shadowed),
                "secret": secret,
            }
        )
    return rows


def _provenance(row: dict[str, Any], inherited: bool) -> str:
    source = "process (inherited)" if inherited and row["source"] == "process" else row["source"]
    return f"{source}; shadows {', '.join(row['shadowed'])}" if row["shadowed"] else source


def format_config_report(config: EffectiveConfig) -> list[str]:
    """The text report: a heading, one ``KEY = value   [source; shadows ...]`` line per row, then the warnings."""
    lines = [REPORT_HEADING]
    lines.extend(
        f"  {row['key']} = {row['display']}   [{_provenance(row, config.inherited)}]" for row in setting_rows(config)
    )
    if config.inherited:
        lines.append(f"  (inherited: {RESOLVED_MARKER}=1, so no layer file was read)")
    lines.extend(f"WARNING: {warning.removeprefix('Warning: ')}" for warning in config.warnings)
    return lines


def configuration_payload(config: EffectiveConfig) -> dict[str, Any]:
    """The ``--json`` form: ``settings`` rows, ``warnings`` and whether the resolution was inherited."""
    return {"settings": setting_rows(config), "warnings": list(config.warnings), "inherited": config.inherited}


def env_example_text() -> str:
    """The commented ``.env.example`` that ``holon init`` scaffolds; every line is a comment and holds no value."""
    lines = [
        "# Holon layered configuration. Copy to .env (git-ignored) or ~/.holon/.env, both mode 0600.",
        f"# Precedence, broadest first: {', '.join(LAYERS)}. The file layers are: {', '.join(FILE_LAYERS)}.",
        "# Format: KEY=VALUE, optional 'single' or \"double\" quotes, # comments. Nothing is expanded or executed.",
        "# KEY= (empty) means not set in this layer: the broader layer's value, or the built-in default, applies.",
        "# Only HOLON_* keys and GITHUB_TOKEN are accepted.",
        "#",
        "# Recommended claude credential reference (the token file itself stays mode 0600):",
        "# HOLON_AGENT_OAUTH_TOKEN_FILE=~/.holon/secrets/claude-oauth-token",
    ]
    for file_key, value_key, container_key in SECRET_FILE_MAPPINGS:
        lines.append(f"# {file_key} is read into {value_key}, which the container maps to {container_key}.")
    lines.append("#")
    for key, default, purpose in DOCUMENTED_KEYS:
        secret = " Secret." if is_secret_key(key) else ""
        lines.append(f"# {purpose} Default: {default}.{secret}".replace("`", ""))
        lines.append(f"# {key}=")
    return "\n".join(lines) + "\n"
