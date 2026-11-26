import atexit
import contextlib
import json
import logging
import os
import tempfile
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

ATTRIBUTION_OFF: dict[str, Any] = {
    "includeCoAuthoredBy": False,
    "attribution": {"commit": "", "pr": ""},
}


def attribution_off_settings(
    existing: str | None = None,
    *,
    default_env: Mapping[str, str] | None = None,
) -> str:
    merged = dict(ATTRIBUTION_OFF)
    from_file = False
    if existing:
        base = _load_settings(existing)
        if base is None:
            logger.warning("HOLON_AGENT_SETTINGS is not a JSON object or readable file; using attribution-off only.")
        else:
            merged = {**base, **ATTRIBUTION_OFF}
            from_file = not is_inline_json(existing)
    if default_env:
        _merge_default_env(merged, default_env)
    text = json.dumps(merged)
    return _private_temp_file(text) if from_file or _carries_env(merged) else text


def place_settings_arg(cmd: list[str], model_flag: str, value: str) -> list[str]:
    if "--settings" in cmd:
        cmd[cmd.index("--settings") + 1] = value
    else:
        at = cmd.index(model_flag) + 2
        cmd[at:at] = ["--settings", value]
    return cmd


def _carries_env(merged: Mapping[str, Any]) -> bool:
    env = merged.get("env")
    return isinstance(env, dict) and bool(env)


def is_inline_json(value: str) -> bool:
    return value.lstrip().startswith("{")


def _merge_default_env(merged: dict[str, Any], default_env: Mapping[str, str]) -> None:
    existing_env = merged.get("env")
    env_dict: dict[str, Any] = dict(existing_env) if isinstance(existing_env, dict) else {}
    for key, val in default_env.items():
        env_dict.setdefault(key, val)
    merged["env"] = env_dict


_TEMP_FILES: dict[str, str] = {}


def _private_temp_file(text: str) -> str:
    cached = _TEMP_FILES.get(text)
    if cached and os.path.exists(cached):
        return cached
    fd, path = tempfile.mkstemp(prefix="holon-settings-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    atexit.register(_remove_quietly, path)
    _TEMP_FILES[text] = path
    return path


def _remove_quietly(path: str) -> None:
    with contextlib.suppress(OSError):
        os.unlink(path)


def _load_settings(value: str) -> dict[str, Any] | None:
    text = value
    if not is_inline_json(value) and os.path.isfile(value):
        try:
            with open(value, encoding="utf-8") as handle:
                text = handle.read()
        except OSError:
            return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None
