from __future__ import annotations

import json
import os
import re
from typing import Any

from sandbox_executor.flow.checkpoint_discovery import get_default_checkpoint_path
from sandbox_executor.flow.context import FlowContext, StageResult
from sandbox_executor.flow.git_refs import _fetch_branch_prefix_error, _list_child_branches, _origin_has_branch
from sandbox_executor.flow.github.merge import GhRunner, _merged_result, gh_runner
from sandbox_executor.ref_names import FitResult, fit_child_intent_branch, truncate_derived_slug

PROGRESS_KEY = "children_progress"
PENDING = "pending"
IN_PROGRESS = "in_progress"
REVIEW_DONE = "review_done"
MERGED = "merged"
FAILED = "failed"


def _sub_intent_depth(intent_data: dict[str, Any]) -> int:
    try:
        return max(0, int(intent_data.get("sub_intent_depth") or 0))
    except (TypeError, ValueError):
        return 0


def _child_intent_branch(parent_branch: str, index: int, slug: str) -> FitResult:
    slug = truncate_derived_slug(re.sub(r"[^a-zA-Z0-9]+", "-", slug).strip("-").lower()) or f"sub-intent-{index}"
    # why: the name comes from the parent branch and position, never the clock, so a resume derives the same branch
    return fit_child_intent_branch(parent_branch, index, slug)


def initial_progress(items: list[dict[str, Any]], plan_branch: str) -> list[dict[str, Any]]:
    progress: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        slug = str(item.get("slug") or f"sub-intent-{index}")
        fitted = _child_intent_branch(plan_branch, index, slug)
        progress.append(
            {
                "index": index,
                "slug": slug,
                "intent_branch": f"{fitted.name}/_",
                "original_slug": fitted.originals.get("child slug"),
                "execution_branch": None,
                "pr_number": None,
                "pr_url": None,
                "status": PENDING,
                "merge_commit": None,
            }
        )
    return progress


def has_recorded_decomposition(stage_result: StageResult | None, plan_branch: str | None = None) -> bool:
    payload = stage_result.payload if stage_result is not None else None
    if not (isinstance(payload, dict) and payload.get("decomposed") and payload.get(PROGRESS_KEY)):
        return False
    # why: a re-plan must never resume the children of an earlier plan
    return plan_branch is None or payload.get("plan_branch") == plan_branch


def child_checkpoint_path(context: FlowContext, intent_branch: str) -> str:
    return get_default_checkpoint_path(context.target_worktree, intent_branch.removesuffix("/_"))


def next_child_action(entry: dict[str, Any], checkpoint_exists: bool = False) -> str:
    status = entry.get("status")
    if status == MERGED:
        return "skip"
    if status == REVIEW_DONE and entry.get("pr_number"):
        return "merge"
    recorded = ("execution_branch", "pr_number", "pr_url", "intent_branch_on_origin", "origin_lookup_failed")
    if checkpoint_exists or status != PENDING or any(entry.get(key) for key in recorded):
        return "resume"
    return "dispatch"


def _execution_branches(repo_dir: str, intent_branch: str) -> list[str] | None:
    intent_prefix = intent_branch.removesuffix("/_")
    if _fetch_branch_prefix_error(repo_dir, intent_prefix) is not None:
        return None
    branches = [
        execution
        for plan in _list_child_branches(repo_dir, intent_prefix, "P")
        for execution in _list_child_branches(repo_dir, plan.removesuffix("/_"), "E")
    ]
    return sorted(branches, key=lambda b: int(m.group(1)) if (m := re.search(r"/E-(\d+)-[^/]+/_$", b)) else -1)


def _child_pr(repo_dir: str, execution_branch: str, runner: GhRunner) -> tuple[dict[str, Any] | None, bool]:
    fields = "number,state,baseRefName,url,mergeCommit"
    proc = runner(["pr", "list", "--head", execution_branch, "--state", "all", "--json", fields], repo_dir)
    if proc.returncode != 0:
        return None, False
    try:
        prs = json.loads(proc.stdout or "[]")
    except ValueError:
        return None, False
    prs = [p for p in prs if isinstance(p, dict)] if isinstance(prs, list) else []
    return (max(prs, key=lambda p: int(p.get("number") or 0)) if prs else None), True


def _apply_fact(entry: dict[str, Any], key: str, value: Any, notes: list[str]) -> None:
    if value in (None, "") or entry.get(key) == value:
        return
    if entry.get(key) not in (None, ""):
        notes.append(f"sub-intent '{entry.get('slug')}': origin has {key}={value!r}, checkpoint had {entry[key]!r}")
    entry[key] = value


def _reconcile_entry(repo_dir: str, entry: dict[str, Any], plan: str, runner: GhRunner, notes: list[str]) -> None:
    intent_branch = str(entry.get("intent_branch") or "")
    branches = _execution_branches(repo_dir, intent_branch)
    if branches is None:
        entry["origin_lookup_failed"] = True
        return
    entry["intent_branch_on_origin"] = _origin_has_branch(repo_dir, intent_branch)
    recorded = entry.get("execution_branch")
    _apply_fact(entry, "execution_branch", recorded if recorded in branches else (branches or [None])[-1], notes)
    if not entry.get("execution_branch"):
        return
    pr, ok = _child_pr(repo_dir, str(entry["execution_branch"]), runner)
    if not ok:
        entry["origin_lookup_failed"] = True
    if not ok or pr is None:
        return
    _apply_fact(entry, "pr_number", pr.get("number"), notes)
    _apply_fact(entry, "pr_url", pr.get("url"), notes)
    if str(pr.get("state") or "").upper() != "MERGED":
        return
    result = _merged_result(pr, plan, already_merged=True)
    # why: a PR merged into another base is only noted, so the guarded merge refuses it later
    if not result.merged:
        notes.append(f"sub-intent '{entry.get('slug')}': PR #{pr.get('number')} not promoted: {result.reason}")
        return
    _apply_fact(entry, "merge_commit", result.merge_commit, notes)
    _apply_fact(entry, "status", MERGED, notes)


def reconcile_with_origin(repo_dir: str, progress: list[dict[str, Any]], plan: str, runner: GhRunner | None = None):
    gh = gh_runner(runner)
    notes: list[str] = []
    for entry in progress:
        entry.pop("origin_lookup_failed", None)
        if entry.get("status") != MERGED:
            _reconcile_entry(repo_dir, entry, plan, gh, notes)
    return notes


def checkpoint_exists(context: FlowContext, entry: dict[str, Any]) -> bool:
    return os.path.exists(child_checkpoint_path(context, str(entry.get("intent_branch") or "")))
