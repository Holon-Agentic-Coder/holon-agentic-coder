"""The frozen module budget configuration: which path is frozen and when the flow may change it.

``holon-config/hygiene/module_budgets.json`` (checked by ``scripts/check_module_budgets.py``) is frozen: a module over
its line limit is split, never given a higher budget. Only the maintainer's explicit instruction authorises a change,
which the flow reads from the intent's ``module_budget_change`` field (the quoted instruction, written into the
maintainer-authored intent row). Without it the planner drops the configuration from the touch set, Stage 3 and the
Stage 4 resolver refuse to commit or push a change to it, and role containers never see
``HOLON_ALLOW_MODULE_BUDGET_CHANGE``. Authorisation is never read from the environment, so a container cannot grant it
to itself. Pure logic plus one environment helper, kept outside the flow package so entrypoints import it directly.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from typing import Any

from sandbox_executor.touch_set import TouchSet, normalise_touch_path

MODULE_BUDGET_CONFIG = "holon-config/hygiene/module_budgets.json"
ALLOW_MODULE_BUDGET_CHANGE_ENV = "HOLON_ALLOW_MODULE_BUDGET_CHANGE"
MODULE_BUDGET_CHANGE_FIELD = "module_budget_change"
MODULE_BUDGET_CHANGE_REFUSED = "module_budget_change_refused"


def module_budget_change_authorised(intent_data: Mapping[str, Any] | None) -> bool:
    """Whether the intent quotes the maintainer's instruction to change the budget (a non-blank string field)."""
    value = (intent_data or {}).get(MODULE_BUDGET_CHANGE_FIELD)
    return isinstance(value, str) and bool(value.strip())


def with_module_budget_flag(
    env: MutableMapping[str, str], intent_data: Mapping[str, Any] | None
) -> MutableMapping[str, str]:
    """Set the flag in ``env`` to ``1`` when the intent is authorised, otherwise remove any inherited value."""
    if module_budget_change_authorised(intent_data):
        env[ALLOW_MODULE_BUDGET_CHANGE_ENV] = "1"
    else:
        env.pop(ALLOW_MODULE_BUDGET_CHANGE_ENV, None)
    return env


@contextmanager
def module_budget_flag(intent_data: Mapping[str, Any] | None) -> Iterator[None]:
    """Apply :func:`with_module_budget_flag` to ``os.environ`` for the block (a role dispatch), then restore it."""
    previous = os.environ.get(ALLOW_MODULE_BUDGET_CHANGE_ENV)
    with_module_budget_flag(os.environ, intent_data)
    try:
        yield
    finally:
        os.environ.pop(ALLOW_MODULE_BUDGET_CHANGE_ENV, None)
        if previous is not None:
            os.environ[ALLOW_MODULE_BUDGET_CHANGE_ENV] = previous


def budget_config_changes(changed_paths: Iterable[str]) -> list[str]:
    """The changed paths that are the budget configuration (after touch-path normalisation)."""
    return [path for path in changed_paths if normalise_touch_path(path) == MODULE_BUDGET_CONFIG]


def without_budget_config(touch_set: TouchSet) -> tuple[TouchSet, list[str]]:
    """Return the touch set without entries naming exactly the configuration, and the removed entry paths.

    A broader entry (a directory or a glob) is kept; Stage 3 still refuses a change to the file itself.
    """
    kept = tuple(entry for entry in touch_set.entries if entry.path != MODULE_BUDGET_CONFIG)
    removed = [entry.path for entry in touch_set.entries if entry.path == MODULE_BUDGET_CONFIG]
    return TouchSet(kept), removed


def refusal_message(paths: Iterable[str] = (MODULE_BUDGET_CONFIG,)) -> str:
    """One sentence refusing an unauthorised change to the budget configuration."""
    return (
        f"Refused a change to {', '.join(paths)}: the module budget configuration is frozen, so split the module "
        f"into smaller modules instead; it changes only when the intent carries '{MODULE_BUDGET_CHANGE_FIELD}' "
        "quoting the maintainer's instruction."
    )
