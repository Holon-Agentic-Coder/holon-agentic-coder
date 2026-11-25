from __future__ import annotations

import os
import re
from collections.abc import Mapping, MutableMapping
from pathlib import Path

from sandbox_executor import budgets, config_env_secrets, preflight
from sandbox_executor.agent_runner import ENV_CLAUDE_CODE_MODE, RETIRED_ENV_VARS
from sandbox_executor.config_env_keys import DOCUMENTED_KEYS, HOST_SET_KEYS
from sandbox_executor.config_env_model import EffectiveConfig, ParsedLayer, ResolvedSetting
from sandbox_executor.config_env_parse import parse_env_text

LAYERS = ("default", "user", "project", "process", "cli")
FILE_LAYERS = ("user", "project")
_APPLIED_LAYERS = (*FILE_LAYERS, "process")
RESOLVED_MARKER = "HOLON_CONFIG_RESOLVED"
ALLOWED_NON_HOLON_KEYS = frozenset({"GITHUB_TOKEN"})
RESERVED_KEYS = frozenset({"HOLON_ROLE", RESOLVED_MARKER, preflight.SKIP_ENV_VAR, *HOST_SET_KEYS}) | RETIRED_ENV_VARS
SECRET_KEYS = frozenset(
    {"HOLON_AGENT_OAUTH_TOKEN", "HOLON_AGENT_KEY", "GITHUB_TOKEN", "HOLON_AGENT_SETTINGS", "HOLON_MITM_WEB_PASSWORD"}
)
_SECRET_SUFFIXES = ("_TOKEN", "_KEY", "_PASSWORD", "_SECRET")
DEFAULTS: Mapping[str, str] = {
    ENV_CLAUDE_CODE_MODE: "1",
    budgets.ENV_TURN_TIMEOUT: f"{budgets.DEFAULT_TURN_SECONDS:g}",
    budgets.ENV_TURN_STALL: f"{budgets.DEFAULT_STALL_SECONDS:g}",
    budgets.ENV_REVIEW_CI_WAIT: f"{budgets.DEFAULT_REVIEW_CI_WAIT_SECONDS:g}",
    preflight.TIMEOUT_ENV_VAR: f"{preflight.DEFAULT_TIMEOUT_SECONDS:g}",
}
_HOLON_KEY_RE = re.compile(r"HOLON_[A-Z0-9_]+\Z")

__all__ = [
    "DEFAULTS",
    "DOCUMENTED_KEYS",
    "FILE_LAYERS",
    "LAYERS",
    "RESERVED_KEYS",
    "RESOLVED_MARKER",
    "SECRET_KEYS",
    "EffectiveConfig",
    "ParsedLayer",
    "ResolvedSetting",
    "accept_key",
    "apply_to_environ",
    "current",
    "is_secret_key",
    "load_effective_config",
    "parse_env_text",
    "project_env_path",
    "remember",
    "resolve",
    "user_env_path",
    "with_cli_overrides",
]


def user_env_path() -> Path:
    return Path(os.path.expanduser("~")) / ".holon" / ".env"


def project_env_path(start: str | os.PathLike[str]) -> Path | None:
    for directory in (Path(start).resolve(), *Path(start).resolve().parents):
        if (directory / ".git").exists():
            return directory / ".env"
    return None


def accept_key(key: str) -> str | None:
    if key in RESERVED_KEYS:
        return "the host owns this key"
    if _HOLON_KEY_RE.match(key) or key in ALLOWED_NON_HOLON_KEYS:
        return None
    return "only HOLON_* keys and GITHUB_TOKEN are accepted"


def is_secret_key(key: str) -> bool:
    if key in SECRET_KEYS:
        return True
    return not key.endswith("_FILE") and key.endswith(_SECRET_SUFFIXES)


def _shadowed_layer_label(layer_name: str, is_present: bool) -> str:
    return layer_name if is_present else f"{layer_name} (empty)"


def _filter_accepted_env(environ: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in environ.items() if accept_key(key) is None}


def resolve(
    user: Mapping[str, str],
    project: Mapping[str, str],
    process_env: Mapping[str, str],
    cli_overrides: Mapping[str, str] | None = None,
    defaults: Mapping[str, str] = DEFAULTS,
) -> EffectiveConfig:
    process = _filter_accepted_env(process_env)
    layers = (("cli", cli_overrides or {}), ("process", process), ("project", project), ("user", user))
    layers += (("default", defaults),)
    keys = sorted(set().union(*(values for _, values in layers)))
    return EffectiveConfig({key: _resolve_key(key, layers) for key in keys}, [])


def _resolve_key(key: str, layers: tuple[tuple[str, Mapping[str, str]], ...]) -> ResolvedSetting:
    defining = [(name, values[key]) for name, values in layers if key in values]
    winner = next(((name, value) for name, value in defining if value), None)
    source, value = winner or (defining[0][0], "")
    shadowed = tuple(
        _shadowed_layer_label(name, bool(layer_value)) for name, layer_value in reversed(defining) if name != source
    )
    return ResolvedSetting(key, value or None, source, shadowed, is_secret_key(key))


def with_cli_overrides(config: EffectiveConfig, cli_overrides: Mapping[str, str]) -> EffectiveConfig:
    settings = dict(config.settings)
    for key, value in cli_overrides.items():
        previous = settings.get(key)
        shadowed: tuple[str, ...] = ()
        if previous:
            label = _shadowed_layer_label(previous.source, previous.value is not None)
            shadowed = (*previous.shadowed, label)
        settings[key] = ResolvedSetting(key, value or None, "cli", shadowed, is_secret_key(key))
    return EffectiveConfig(dict(sorted(settings.items())), config.warnings, config.inherited)


def _read_layer(path: Path, warnings: list[str]) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError):
        warnings.append(f"Warning: {path} could not be read; that layer is ignored.")
        return {}
    warnings.extend(config_env_secrets.permission_warnings(str(path)))
    parsed = parse_env_text(text, str(path))
    warnings.extend(parsed.warnings)
    accepted: dict[str, str] = {}
    for key, value in parsed.values.items():
        reason = accept_key(key)
        if reason:
            warnings.append(f"Warning: {path}: ignoring {key}: {reason}.")
        else:
            accepted[key] = value
    return accepted


def _expand(values: Mapping[str, str], origin: str, warnings: list[str]) -> dict[str, str]:
    expanded, secret_warnings = config_env_secrets.expand_secret_files(values, origin)
    warnings.extend(secret_warnings)
    return expanded


def _load_project_layer(project_path: Path, warnings: list[str]) -> dict[str, str]:
    parsed = _read_layer(project_path, warnings)
    secret_keys = [key for key, value in parsed.items() if value and is_secret_key(key)]
    warnings.extend(config_env_secrets.tracked_secret_warning(str(project_path), secret_keys))
    project, reference_warnings = config_env_secrets.drop_host_references(parsed, str(project_path))
    warnings.extend(reference_warnings)
    return project


def load_effective_config(environ: Mapping[str, str] | None = None, cwd: str | None = None) -> EffectiveConfig:
    environ = os.environ if environ is None else environ
    if environ.get(RESOLVED_MARKER) == "1":
        return EffectiveConfig(resolve({}, {}, environ).settings, [], inherited=True)
    warnings: list[str] = []
    user_path = user_env_path()
    user = _expand(_read_layer(user_path, warnings), str(user_path), warnings)
    project_path = project_env_path(cwd or os.getcwd())
    project = _load_project_layer(project_path, warnings) if project_path else {}
    process = _expand(_filter_accepted_env(environ), "process environment", warnings)
    return EffectiveConfig(resolve(user, project, process).settings, warnings)


def apply_to_environ(config: EffectiveConfig, environ: MutableMapping[str, str]) -> list[str]:
    added = []
    for setting in config.settings.values():
        if setting.source in _APPLIED_LAYERS and setting.value is not None and not environ.get(setting.key):
            environ[setting.key] = setting.value
            added.append(setting.key)
    return added


_CURRENT: list[EffectiveConfig] = []


def remember(config: EffectiveConfig | None) -> None:
    _CURRENT.clear()
    if config is not None:
        _CURRENT.append(config)


def current() -> EffectiveConfig | None:
    return _CURRENT[0] if _CURRENT else None
