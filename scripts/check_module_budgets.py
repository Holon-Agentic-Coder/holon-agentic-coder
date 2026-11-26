#!/usr/bin/env python3
"""Module line budgets: fail any module over its line limit, and fail any change to the frozen budget configuration.

Every tracked Python source module is measured, ``__init__.py`` included: every ``.py`` file ``git ls-files`` lists
under ``apps/*/src/`` or ``scripts/`` (``SOURCE_ROOTS``), with no package allowlist, so a new package is budgeted from
its first commit. Tests are not budgeted. Outside a git repository the source roots are walked on the filesystem.

The configuration (default ``holon-config/hygiene/module_budgets.json``, parsed by ``module_budget_config``) holds
``default_max_lines``, ``exclude`` (generated or vendored paths, each with its reason) and ``overrides`` (module path
to its own limit). A module without an override is limited by the default, so a new module needs no entry. A module
over its limit must be split into smaller modules: the budget is frozen and may not be changed. Overrides only ratchet
down: a module below its override produces a notice to lower it. Stale or redundant entries are warnings only. The
configuration itself is frozen against its merge-base copy (``module_budget_freeze``).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from pathlib import Path

try:
    from scripts.module_budget_config import BudgetConfig, ConfigError, parse_config
    from scripts.module_budget_freeze import (
        ALLOW_CHANGE_ENV,
        NO_BASE,
        describe_change,
        freeze_messages,
        merge_base_copy,
        run_git,
    )
except ImportError:  # run as a script file: only scripts/ itself is on sys.path
    from module_budget_config import BudgetConfig, ConfigError, parse_config
    from module_budget_freeze import (
        ALLOW_CHANGE_ENV,
        NO_BASE,
        describe_change,
        freeze_messages,
        merge_base_copy,
        run_git,
    )

__all__ = ["ALLOW_CHANGE_ENV", "DEFAULT_CONFIG", "ConfigError", "describe_change", "main", "parse_config"]

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "holon-config" / "hygiene" / "module_budgets.json"
SOURCE_ROOTS = ("apps/*/src/**/*.py", "scripts/**/*.py")
FILESYSTEM_NOTICE = "git ls-files failed; source modules were discovered by walking the filesystem"


def is_source_module(path: str) -> bool:
    """Whether a repository-relative POSIX path is a source module: a ``.py`` file matching ``SOURCE_ROOTS``."""
    parts = path.split("/")
    if not path.endswith(".py"):
        return False
    return (parts[0] == "scripts" and len(parts) >= 2) or (len(parts) >= 4 and parts[0] == "apps" and parts[2] == "src")


def tracked_modules(root: Path) -> tuple[list[str], str | None]:
    """Return ``(sorted source modules, notice)``: tracked files on disk, or a filesystem walk when git fails."""
    listed = run_git(root, "ls-files", "-z")
    if listed is not None and listed.returncode == 0:
        paths, notice = listed.stdout.split("\0"), None
    else:
        paths = [path.relative_to(root).as_posix() for pattern in SOURCE_ROOTS for path in root.glob(pattern)]
        notice = FILESYSTEM_NOTICE
    return sorted({path for path in paths if is_source_module(path) and (root / path).is_file()}), notice


def module_line_counts(root: Path, modules: Iterable[str]) -> tuple[dict[str, int], list[str]]:
    """Return ``(line count per module, one error per unreadable module)``; lines are ``str.splitlines()`` items."""
    counts: dict[str, int] = {}
    errors = []
    for module in modules:
        try:
            counts[module] = len((root / module).read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"{module}: cannot be read as UTF-8 text ({exc})")
    return counts, errors


def limit_violations(counts: dict[str, int], config: BudgetConfig) -> list[str]:
    """Return one message per module over its limit (its override, otherwise the default)."""
    return [
        f"{module}: {lines} lines exceeds its limit of {config.limit_for(module)}; split the module into smaller "
        "modules - the budget is frozen and may not be changed"
        for module, lines in counts.items()
        if lines > config.limit_for(module)
    ]


def ratchet_notices(counts: dict[str, int], config: BudgetConfig) -> list[str]:
    """Return one notice per override above its module's line count: overrides only ratchet down."""
    notices = []
    for module, limit in config.overrides.items():
        lines = counts.get(module)
        if lines is None or lines >= limit:
            continue
        if lines <= config.default_max_lines:
            notices.append(f"remove the override of {module} ({limit}): {lines} lines is within the default")
        else:
            notices.append(f"lower the override of {module} from {limit} to {lines}")
    return notices


def _override_warning(module: str, limit: int, counts: dict[str, int], config: BudgetConfig) -> list[str]:
    warnings = []
    if not is_source_module(module):
        warnings.append(f"override {module} is not a discovered source module")
    elif config.is_excluded(module):
        warnings.append(f"override {module} names an excluded path")
    elif module not in counts:
        warnings.append(f"override {module} names a missing module")
    if limit <= config.default_max_lines:
        warnings.append(f"override {module} ({limit}) is at or below the default of {config.default_max_lines}")
    return warnings


def override_warnings(counts: dict[str, int], config: BudgetConfig, discovered: list[str]) -> list[str]:
    """Return one message per stale, misplaced or redundant override or exclude entry, and one for unsorted keys."""
    warnings = [
        w for module, limit in config.overrides.items() for w in _override_warning(module, limit, counts, config)
    ]
    if list(config.overrides) != sorted(config.overrides):
        warnings.append("override keys are not sorted by path")
    for entry in config.exclude:
        if not any(entry.matches(module) for module in discovered):
            warnings.append(f"exclude entry {entry.path} matches no tracked module")
    return warnings


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
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="Repository root the source modules are under")
    return parser.parse_args(argv)


def _report(kind: str, messages: Iterable[str | None]) -> None:
    for message in messages:
        if message:
            print(f"::{kind}::module budget: {message}")


def main(argv: list[str] | None = None) -> int:
    """Run the check; return 1 on any over-limit module, invalid configuration or unauthorised change, else 0."""
    args = _parse_args(argv)
    try:
        head_text = args.config.read_text(encoding="utf-8")
        config = parse_config(head_text)
    except (OSError, ConfigError) as exc:
        print(f"::error::module budget configuration {args.config}: {exc}")
        return 1
    discovered, discovery_notice = tracked_modules(args.root)
    excluded = [module for module in discovered if config.is_excluded(module)]
    counts, read_errors = module_line_counts(args.root, (m for m in discovered if not config.is_excluded(m)))
    base_text, base_notice = _base_text(args, args.root, args.config)
    freeze_errors, freeze_notices = freeze_messages(head_text, base_text, str(args.config))
    _report("warning", override_warnings(counts, config, discovered))
    _report("notice", [discovery_notice, base_notice, *freeze_notices, *ratchet_notices(counts, config)])
    errors = [*read_errors, *limit_violations(counts, config), *freeze_errors]
    _report("error", errors)
    if errors:
        return 1
    print(
        f"Module budgets passed: {len(counts)} modules (default {config.default_max_lines}, "
        f"{len(config.overrides)} overrides, {len(excluded)} excluded)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
