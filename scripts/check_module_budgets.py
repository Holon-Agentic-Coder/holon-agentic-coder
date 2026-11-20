#!/usr/bin/env python3
"""Module line budgets: fail any module over its line limit, and fail any change to the frozen budget configuration.

The configuration (default ``holon-config/hygiene/module_budgets.json``) holds ``default_max_lines``, the ``packages``
to check (repository-relative directories) and ``overrides`` (repository-relative module path to its own limit). A
module without an override is limited by the default, so a new module needs no entry. A module over its limit must be
split into smaller modules: the budget is frozen and may not be changed. Stale or redundant overrides are warnings only.

The configuration itself is compared byte for byte with the merge base's copy (``git merge-base HEAD origin/main``).
Any difference, a reformat included, fails unless ``HOLON_ALLOW_MODULE_BUDGET_CHANGE=1``: the maintainer's explicit
instruction (the ``module-budget-change`` pull request label in CI, or the intent's ``module_budget_change`` field in
the flow). With no merge base the comparison is skipped quietly; with no copy at the merge base it is skipped with a
notice.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "holon-config" / "hygiene" / "module_budgets.json"
ALLOW_CHANGE_ENV = "HOLON_ALLOW_MODULE_BUDGET_CHANGE"
NO_BASE = "none"
CONFIG_KEYS = ("default_max_lines", "packages", "overrides")
FROZEN_RULE = (
    "the budget configuration is frozen; split the module into smaller modules instead. It changes only on the "
    "maintainer's explicit instruction: the 'module-budget-change' pull request label in CI, or the intent's "
    "'module_budget_change' field in the flow"
)


class ConfigError(ValueError):
    """The budget configuration is missing, not JSON, or not the expected shape."""


@dataclass(frozen=True)
class BudgetConfig:
    """The parsed budget configuration."""

    default_max_lines: int
    packages: tuple[str, ...]
    overrides: dict[str, int]


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


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
    default, packages, overrides = (data[key] for key in CONFIG_KEYS)
    if not _is_positive_int(default):
        raise ConfigError("default_max_lines must be a positive integer")
    if not isinstance(packages, list) or not packages or not all(isinstance(p, str) and p for p in packages):
        raise ConfigError("packages must be a non-empty list of repository-relative directories")
    if not isinstance(overrides, dict) or not all(_is_positive_int(limit) for limit in overrides.values()):
        raise ConfigError("overrides must map repository-relative module paths to positive integers")
    return BudgetConfig(default, tuple(packages), dict(overrides))


def module_line_counts(root: Path, packages: tuple[str, ...]) -> dict[str, int]:
    """Return the line count of every ``.py`` file under each package, keyed by its repository-relative POSIX path."""
    counts: dict[str, int] = {}
    for package in packages:
        for path in sorted((root / package).rglob("*.py")):
            counts[path.relative_to(root).as_posix()] = len(path.read_text(encoding="utf-8").splitlines())
    return counts


def limit_violations(counts: dict[str, int], config: BudgetConfig) -> list[str]:
    """Return one message per module over its limit (its override, otherwise the default)."""
    violations = []
    for module, lines in counts.items():
        limit = config.overrides.get(module, config.default_max_lines)
        if lines > limit:
            violations.append(
                f"{module}: {lines} lines exceeds its limit of {limit}; split the module into smaller modules - "
                "the budget is frozen and may not be changed"
            )
    return violations


def _in_packages(module: str, packages: tuple[str, ...]) -> bool:
    return any(module.startswith(package.rstrip("/") + "/") for package in packages)


def override_warnings(counts: dict[str, int], config: BudgetConfig) -> list[str]:
    """Return one message per stale, misplaced or redundant override, and one for unsorted override keys."""
    warnings = []
    for module, limit in config.overrides.items():
        if not _in_packages(module, config.packages):
            warnings.append(f"override {module} lies outside every configured package")
        elif module not in counts:
            warnings.append(f"override {module} names a missing module")
        if limit <= config.default_max_lines:
            warnings.append(f"override {module} ({limit}) is at or below the default of {config.default_max_lines}")
    if list(config.overrides) != sorted(config.overrides):
        warnings.append("override keys are not sorted by path")
    return warnings


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    except OSError:
        return None


def merge_base_copy(root: Path, config_path: Path) -> tuple[str | None, str | None]:
    """Return ``(base text, notice)`` for the configuration at ``git merge-base HEAD origin/main``.

    Both are ``None`` when there is no merge base (no remote, a shallow clone or no common ancestor): the comparison
    is skipped quietly. A notice is returned when the merge base has no copy of the configuration.
    """
    base = _git(root, "merge-base", "HEAD", "origin/main")
    if base is None or base.returncode != 0 or not base.stdout.strip():
        return None, None
    try:
        relative = config_path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None, f"{config_path} lies outside the repository; the freeze comparison is skipped"
    shown = _git(root, "show", f"{base.stdout.strip()}:{relative}")
    if shown is None or shown.returncode != 0:
        return None, f"{relative} has no copy at the merge base; the freeze comparison is skipped"
    return shown.stdout, None


def _override_changes(base: dict[str, Any], head: dict[str, Any]) -> list[str]:
    changes = [f"override added: {path} ({head[path]})" for path in head if path not in base]
    changes += [f"override removed: {path} ({base[path]})" for path in base if path not in head]
    for path in (path for path in head if path in base and head[path] != base[path]):
        verb = "raised" if isinstance(head[path], int) and head[path] > base[path] else "lowered"
        changes.append(f"override {verb}: {path} ({base[path]} -> {head[path]})")
    return changes


def describe_change(base_text: str, head_text: str) -> list[str]:
    """Name what changed between two configuration texts; ``reformatted`` when the parsed values are equal."""
    try:
        base, head = json.loads(base_text), json.loads(head_text)
    except ValueError:
        return ["reformatted or no longer valid JSON"]
    if not isinstance(base, dict) or not isinstance(head, dict):
        return ["reformatted or no longer valid JSON"]
    changes = []
    if base.get("default_max_lines") != head.get("default_max_lines"):
        changes.append(f"default_max_lines {base.get('default_max_lines')} -> {head.get('default_max_lines')}")
    if base.get("packages") != head.get("packages"):
        changes.append("packages changed")
    base_overrides, head_overrides = base.get("overrides"), head.get("overrides")
    if isinstance(base_overrides, dict) and isinstance(head_overrides, dict):
        changes += _override_changes(base_overrides, head_overrides)
    elif base_overrides != head_overrides:
        changes.append("overrides changed")
    if changes == [] and set(base) != set(head):
        changes.append("keys changed")
    return changes or ["reformatted"]


def freeze_messages(head_text: str, base_text: str | None, label: str) -> tuple[list[str], list[str]]:
    """Return ``(errors, notices)`` from comparing the configuration with its base copy (``None``: no base)."""
    if base_text is None or base_text == head_text:
        return [], []
    changes = "; ".join(describe_change(base_text, head_text))
    if os.environ.get(ALLOW_CHANGE_ENV) == "1":
        return [], [f"{label} differs from the merge-base copy ({changes}); authorised by {ALLOW_CHANGE_ENV}=1"]
    return [f"{label} differs from the merge-base copy ({changes}); {FROZEN_RULE}"], []


def _base_text(args: argparse.Namespace, root: Path, config_path: Path) -> tuple[str | None, str | None]:
    if args.base is None:
        return merge_base_copy(root, config_path)
    if args.base == NO_BASE:
        return None, None
    base_path = Path(args.base)
    if not base_path.is_file():
        return None, f"{base_path} does not exist; the freeze comparison is skipped"
    return base_path.read_text(encoding="utf-8"), None


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check the per-module line budgets and the budget freeze.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Budget configuration JSON file")
    parser.add_argument(
        "--base", default=None, help="File holding the base copy of the configuration, or 'none' (default: git)"
    )
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="Repository root the package paths are under")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the check; return 1 on any over-limit module, invalid configuration or unauthorised change, else 0."""
    args = _parse_args(argv)
    try:
        head_text = args.config.read_text(encoding="utf-8")
        config = parse_config(head_text)
    except (OSError, ConfigError) as exc:
        print(f"::error::module budget configuration {args.config}: {exc}")
        return 1
    counts = module_line_counts(args.root, config.packages)
    for warning in override_warnings(counts, config):
        print(f"::warning::module budget: {warning}")
    base_text, notice = _base_text(args, args.root, args.config)
    errors, notices = freeze_messages(head_text, base_text, str(args.config))
    for message in [notice, *notices]:
        if message:
            print(f"::notice::module budget: {message}")
    errors = [*limit_violations(counts, config), *errors]
    for error in errors:
        print(f"::error::module budget: {error}")
    if errors:
        return 1
    print(
        f"Module budgets passed: {len(counts)} modules in {len(config.packages)} packages "
        f"(default {config.default_max_lines}, {len(config.overrides)} overrides)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
