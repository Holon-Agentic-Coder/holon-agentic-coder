# why: authorisation is read from the intent, never the environment, so a role container cannot grant it to itself.
# why: kept outside the flow package so entrypoints import it directly.
from __future__ import annotations

import os
import re
from collections.abc import Iterable, Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from typing import Any

from sandbox_executor.git import run_git
from sandbox_executor.touch_set import TouchSet, normalise_touch_path

MODULE_BUDGET_CONFIG = "holon-config/hygiene/module_budgets.json"
ALLOW_MODULE_BUDGET_CHANGE_ENV = "HOLON_ALLOW_MODULE_BUDGET_CHANGE"
MODULE_BUDGET_CHANGE_FIELD = "module_budget_change"
MODULE_BUDGET_CHANGE_REFUSED = "module_budget_change_refused"

_OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def module_budget_change_authorised(intent_data: Mapping[str, Any] | None) -> bool:
    value = (intent_data or {}).get(MODULE_BUDGET_CHANGE_FIELD)
    return isinstance(value, str) and bool(value.strip())


def with_module_budget_flag(
    env: MutableMapping[str, str], intent_data: Mapping[str, Any] | None
) -> MutableMapping[str, str]:
    if module_budget_change_authorised(intent_data):
        env[ALLOW_MODULE_BUDGET_CHANGE_ENV] = "1"
    else:
        env.pop(ALLOW_MODULE_BUDGET_CHANGE_ENV, None)
    return env


@contextmanager
def module_budget_flag(intent_data: Mapping[str, Any] | None) -> Iterator[None]:
    previous = os.environ.get(ALLOW_MODULE_BUDGET_CHANGE_ENV)
    with_module_budget_flag(os.environ, intent_data)
    try:
        yield
    finally:
        os.environ.pop(ALLOW_MODULE_BUDGET_CHANGE_ENV, None)
        if previous is not None:
            os.environ[ALLOW_MODULE_BUDGET_CHANGE_ENV] = previous


def budget_config_changes(changed_paths: Iterable[str]) -> list[str]:
    return [path for path in changed_paths if normalise_touch_path(path) == MODULE_BUDGET_CONFIG]


# why: a broader entry (a directory or a glob) is kept; Stage 3 still refuses a change to the file itself.
def without_budget_config(touch_set: TouchSet) -> tuple[TouchSet, list[str]]:
    kept = tuple(entry for entry in touch_set.entries if entry.path != MODULE_BUDGET_CONFIG)
    removed = [entry.path for entry in touch_set.entries if entry.path == MODULE_BUDGET_CONFIG]
    return TouchSet(kept), removed


def refusal_message(paths: Iterable[str] = (MODULE_BUDGET_CONFIG,)) -> str:
    return (
        f"Refused a change to {', '.join(paths)}: the module budget configuration is frozen, so split the module "
        f"into smaller modules instead; it changes only when the intent carries '{MODULE_BUDGET_CHANGE_FIELD}' "
        "quoting the maintainer's instruction."
    )


def budget_push_refusal(repo_dir: str, base_sha: str, head_sha: str, intent_data: dict[str, Any]) -> str | None:
    if module_budget_change_authorised(intent_data) or not (_OBJECT_ID.match(head_sha) and _OBJECT_ID.match(base_sha)):
        return None
    # why: the push itself refuses or fails on a non-object-id head or a directory that is not a git worktree.
    if not os.path.exists(os.path.join(repo_dir, ".git")):
        return None
    res = run_git(["diff", "--name-only", base_sha, head_sha, "--"], cwd=repo_dir, check=False)
    if res.returncode != 0 or not isinstance(res.stdout, str):
        return f"refusing to push: could not list the files changed since {base_sha[:12]}"
    paths = budget_config_changes(res.stdout.splitlines())
    return f"refusing to push. {refusal_message(paths)}" if paths else None
