"""The module budget configuration: parsing and validation of ``holon-config/hygiene/module_budgets.json``.

The configuration holds exactly three keys: ``default_max_lines`` (the limit of a module without an override),
``exclude`` (repository-relative paths that are not measured, each with the ``reason`` it is excluded: generated or
vendored code only; a path ending in ``/`` excludes everything under it) and ``overrides`` (repository-relative module
path to its own, frozen limit). ``scripts/check_module_budgets.py`` uses it to check every discovered source module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

CONFIG_KEYS = ("default_max_lines", "exclude", "overrides")
EXCLUDE_KEYS = ("path", "reason")


class ConfigError(ValueError):
    """The budget configuration is missing, not JSON, or not the expected shape."""


@dataclass(frozen=True)
class ExcludeEntry:
    """One path that is not measured, with the stated reason (generated or vendored code)."""

    path: str
    reason: str

    def matches(self, module: str) -> bool:
        """Whether ``module`` is this path, or lies under it when the path names a directory (trailing ``/``)."""
        return module.startswith(self.path) if self.path.endswith("/") else module == self.path


@dataclass(frozen=True)
class BudgetConfig:
    """The parsed budget configuration."""

    default_max_lines: int
    exclude: tuple[ExcludeEntry, ...]
    overrides: dict[str, int]

    def limit_for(self, module: str) -> int:
        """The module's line limit: its override, otherwise the default."""
        return self.overrides.get(module, self.default_max_lines)

    def is_excluded(self, module: str) -> bool:
        """Whether any ``exclude`` entry matches the module."""
        return any(entry.matches(module) for entry in self.exclude)


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_non_blank(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_relative_path(path: str) -> bool:
    return not path.startswith("/") and ".." not in path.split("/")


def _parse_exclude_entry(index: int, entry: Any) -> ExcludeEntry:
    if not isinstance(entry, dict) or sorted(entry) != sorted(EXCLUDE_KEYS):
        raise ConfigError(f"exclude entry {index} must hold exactly the keys {', '.join(EXCLUDE_KEYS)}")
    path, reason = entry["path"], entry["reason"]
    if not _is_non_blank(path) or not _is_relative_path(path):
        raise ConfigError(f"exclude entry {index} path must be a repository-relative path")
    if not _is_non_blank(reason):
        raise ConfigError(f"exclude entry {index} ({path}) must state a non-blank reason")
    return ExcludeEntry(path, reason)


def _parse_exclude(value: Any) -> tuple[ExcludeEntry, ...]:
    if not isinstance(value, list):
        raise ConfigError("exclude must be a list of {path, reason} objects")
    return tuple(_parse_exclude_entry(index, entry) for index, entry in enumerate(value))


def _parse_overrides(value: Any) -> dict[str, int]:
    if not isinstance(value, dict) or not all(_is_positive_int(limit) for limit in value.values()):
        raise ConfigError("overrides must map repository-relative module paths to positive integers")
    return dict(value)


def parse_config(text: str) -> BudgetConfig:
    """Parse and validate the configuration text.

    Raises:
        ConfigError: If the text is not JSON or does not hold exactly the three keys with the expected types.
    """
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ConfigError(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or sorted(data) != sorted(CONFIG_KEYS):
        raise ConfigError(f"expected exactly the keys {', '.join(CONFIG_KEYS)}")
    if not _is_positive_int(data["default_max_lines"]):
        raise ConfigError("default_max_lines must be a positive integer")
    return BudgetConfig(data["default_max_lines"], _parse_exclude(data["exclude"]), _parse_overrides(data["overrides"]))
