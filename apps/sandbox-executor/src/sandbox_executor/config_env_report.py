from __future__ import annotations

import hashlib
import math
import os
import re
from collections import OrderedDict
from typing import Any

from sandbox_executor import claude_keychain
from sandbox_executor.config_env import FILE_LAYERS, LAYERS, RESOLVED_MARKER, is_secret_key
from sandbox_executor.config_env_keys import DOCUMENTED_KEYS
from sandbox_executor.config_env_model import EffectiveConfig, ResolvedSetting
from sandbox_executor.config_env_secrets import PROJECT_REFUSED_KEYS, SECRET_FILE_MAPPINGS
from sandbox_executor.redaction import COMMAND_ECHO_ENV_NAMES, carries_url_password, redact_command

REPORT_HEADING = "Configuration (HOLON_* settings):"
_NUMERIC_SUFFIXES = ("_SECONDS", "_MINUTES", "_TIMEOUT", "_TOKENS", "_WINDOW", "_BYTES", "_PORT")
_NUMBER_RE = re.compile(r"\d{1,12}(?:\.\d{1,6})?")
_PLAIN_VALUE_RE = re.compile(r"[A-Za-z0-9/~][A-Za-z0-9._,:+/~-]{0,79}")
_TOKEN_RUN_RE = re.compile(r"[A-Za-z0-9]{20}")
_REDACTION_CACHE_MAXSIZE = 512
_REDACTION_CACHE: OrderedDict[bytes, bool] = OrderedDict()


# why: the redaction cache is keyed on a SHA-256 digest so plaintext credentials are never retained in memory.
def _plain_value_cache_key(key: str, value: str) -> bytes:
    hasher = hashlib.sha256()
    for part in (key, value, *(os.environ.get(name, "") for name in COMMAND_ECHO_ENV_NAMES)):
        encoded = part.encode("utf-8", errors="surrogatepass")
        hasher.update(len(encoded).to_bytes(8, "big"))
        hasher.update(encoded)
    return hasher.digest()


def _plain_value_redaction_safe(key: str, value: str) -> bool:
    digest = _plain_value_cache_key(key, value)
    cached = _REDACTION_CACHE.get(digest)
    if cached is not None:
        _REDACTION_CACHE.move_to_end(digest)
        return cached
    item = f"{key}={value}"
    safe = redact_command([item]) == item and not carries_url_password(value)
    _REDACTION_CACHE[digest] = safe
    if len(_REDACTION_CACHE) > _REDACTION_CACHE_MAXSIZE:
        _REDACTION_CACHE.popitem(last=False)
    return safe


def _plain_value(key: str, value: str) -> bool:
    if key.endswith(_NUMERIC_SUFFIXES):
        return bool(_NUMBER_RE.fullmatch(value)) or value.lower() == "unlimited"
    if not _PLAIN_VALUE_RE.fullmatch(value) or "//" in value or _TOKEN_RUN_RE.search(value):
        return False
    try:
        if not math.isfinite(float(value)):
            return False
    except ValueError:
        pass
    return _plain_value_redaction_safe(key, value)


def _display(setting: ResolvedSetting, masked: bool) -> tuple[str | None, str]:
    if setting.value is None:
        return None, f"unset ({setting.source})"
    if masked:
        return None, f"set ({setting.source})"
    if not _plain_value(setting.key, setting.value):
        return None, f"set ({setting.source}; value withheld)"
    return setting.value, setting.value


def setting_rows(config: EffectiveConfig) -> list[dict[str, Any]]:
    rows = []
    for key in sorted(config.settings):
        setting = config.settings[key]
        masked = setting.masked or is_secret_key(key)
        value, display = _display(setting, masked)
        rows.append(
            {
                "key": key,
                "value": value,
                "display": display,
                "source": setting.source,
                "shadowed": list(setting.shadowed),
                "secret": masked,
            }
        )
    return rows


def _provenance(row: dict[str, Any], inherited: bool) -> str:
    source = "process (inherited)" if inherited and row["source"] == "process" else row["source"]
    return f"{source}; shadows {', '.join(row['shadowed'])}" if row["shadowed"] else source


def format_config_report(config: EffectiveConfig, rows: list[dict[str, Any]] | None = None) -> list[str]:
    resolved_rows = setting_rows(config) if rows is None else rows
    lines = [REPORT_HEADING]
    lines.extend(f"  {row['key']} = {row['display']}   [{_provenance(row, config.inherited)}]" for row in resolved_rows)
    if config.inherited:
        lines.append(f"  (inherited: {RESOLVED_MARKER}=1, so no layer file was read)")
    lines.extend(f"WARNING: {warning.removeprefix('Warning: ')}" for warning in config.warnings)
    return lines


def configuration_payload(config: EffectiveConfig, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    resolved_rows = setting_rows(config) if rows is None else rows
    return {"settings": resolved_rows, "warnings": list(config.warnings), "inherited": config.inherited}


def env_example_text() -> str:
    lines = [
        "# Holon layered configuration. Copy to .env (git-ignored) or ~/.holon/.env, both mode 0600.",
        f"# Precedence, broadest first: {', '.join(LAYERS)}. The file layers are: {', '.join(FILE_LAYERS)}.",
        "# Format: KEY=VALUE, optional 'single' or \"double\" quotes, # comments. Nothing is expanded or executed.",
        "# KEY= (empty) means not set in this layer: the broader layer's value, or the built-in default, applies.",
        "# Only HOLON_* keys and GITHUB_TOKEN are accepted.",
        "# A project .env may not set token-file references or: " + ", ".join(PROJECT_REFUSED_KEYS) + ".",
        "#",
        "# The claude credential is kept in the OS keychain, not in a file. Store it once (the command prompts):",
        f"#   macOS: {claude_keychain.STORE_COMMANDS['darwin']}",
        f"#   Linux: {claude_keychain.STORE_COMMANDS['linux']}",
    ]
    for file_name, value_name, container_name in SECRET_FILE_MAPPINGS:
        lines.append(f"# {file_name} is read into {value_name}, which the container maps to {container_name}.")
    lines.append("# A token file is an explicit override of the keychain entry; prefer it only to an inline token.")
    lines.append("#")
    for key, default, purpose in DOCUMENTED_KEYS:
        note = " Secret." if is_secret_key(key) else ""
        lines.append(f"# {purpose} Default: {default}.{note}".replace("`", ""))
        lines.append(f"# {key}=")
    return "\n".join(lines) + "\n"
