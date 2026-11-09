"""Per-child progress of a decomposed Stage 3: what each sub-intent has done, reconciled against origin on resume.

The progress list lives in the parent's Stage 3 payload under ``children_progress`` and is checkpointed with it. Each
entry holds only small scalar facts: ``index``, ``slug``, ``intent_branch``, ``execution_branch``, ``pr_number``,
``pr_url``, ``status`` (``pending``, ``in_progress``, ``review_done``, ``merged`` or ``failed``) and
``merge_commit``. :func:`next_child_action` decides each child's next step from those facts alone and never returns
``dispatch`` once a child has an execution branch, a PR or anything else recorded, so a resume never re-runs work.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from sandbox_executor.flow.checkpoint import get_default_checkpoint_path
from sandbox_executor.flow.context import FlowContext, StageResult
from sandbox_executor.flow.git_refs import _fetch_branch_prefix_error, _list_child_branches, _origin_has_branch
from sandbox_executor.flow.github.merge import GhRunner, gh_runner

PROGRESS_KEY = "children_progress"
PENDING = "pending"
IN_PROGRESS = "in_progress"
REVIEW_DONE = "review_done"
MERGED = "merged"
FAILED = "failed"


def _sub_intent_depth(intent_data: dict[str, Any]) -> int:
    """Return the nesting depth recorded on an intent payload (0 for a root intent)."""
    try:
        return max(0, int(intent_data.get("sub_intent_depth") or 0))
    except (TypeError, ValueError):
        return 0


def _child_intent_branch(parent_branch: str, index: int, slug: str) -> str:
    """Return the bare intent branch of the ``index``-th (1-based) child of ``parent_branch``: ``{parent}--{n}-{slug}``.

    The name is derived from the parent's branch and the item's position, never from the clock. A sanitised slug never
    contains ``--``, so the name cannot collide with the parent, a sibling or any other intent, even one created in the
    same second with the same slug; re-running the same parent's list derives the same name for the same child.
    """
    child_slug = re.sub(r"[^a-zA-Z0-9]+", "-", slug).strip("-").lower() or f"sub-intent-{index}"
    return f"{parent_branch.removesuffix('/_')}--{index}-{child_slug}"


def initial_progress(items: list[dict[str, Any]], plan_branch: str) -> list[dict[str, Any]]:
    """Return one ``pending`` entry per sub-intent list item, each with its deterministic intent branch."""
    progress: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        slug = str(item.get("slug") or f"sub-intent-{index}")
        progress.append(
            {
                "index": index,
                "slug": slug,
                "intent_branch": f"{_child_intent_branch(plan_branch, index, slug)}/_",
                "execution_branch": None,
                "pr_number": None,
                "pr_url": None,
                "status": PENDING,
                "merge_commit": None,
            }
        )
    return progress


def has_recorded_decomposition(stage_result: StageResult | None) -> bool:
    """True when a (checkpointed) Stage 3 result already decomposed and recorded per-child progress.

    The pipeline then resumes the children instead of dispatching the parent executor again.
    """
    payload = stage_result.payload if stage_result is not None else None
    return bool(isinstance(payload, dict) and payload.get("decomposed") and payload.get(PROGRESS_KEY))


def child_checkpoint_path(context: FlowContext, intent_branch: str) -> str:
    """Return the child's own checkpoint file, keyed on its bare intent branch as ``save_checkpoint`` keys it."""
    return get_default_checkpoint_path(context.target_worktree, intent_branch.removesuffix("/_"))


def next_child_action(entry: dict[str, Any], checkpoint_exists: bool = False) -> str:
    """Decide a child's next step: ``skip``, ``merge``, ``resume`` or ``dispatch``.

    - ``skip``: the child's PR is merged into the plan branch.
    - ``merge``: its PR is open and its Stage 4 consensus and Stage 5 already completed.
    - ``resume``: anything exists for it (an intent or execution branch on origin, a PR, a checkpoint, a failed origin
      lookup or any status past ``pending``); it continues from its own checkpoint.
    - ``dispatch``: nothing exists anywhere, so a fresh child pipeline runs.
    """
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
    """Return the child's execution branches on origin, oldest first, or None when origin could not be fetched."""
    intent_prefix = intent_branch.removesuffix("/_")
    if _fetch_branch_prefix_error(repo_dir, intent_prefix) is not None:
        return None
    branches = [
        execution
        for plan in _list_child_branches(repo_dir, intent_prefix, "P")
        for execution in _list_child_branches(repo_dir, plan.removesuffix("/_"), "E")
    ]

    def seq(branch: str) -> int:
        match = re.search(r"/E-(\d+)-[^/]+/_$", branch)
        return int(match.group(1)) if match else -1

    return sorted(branches, key=seq)


def _child_pr(repo_dir: str, execution_branch: str, runner: GhRunner) -> tuple[dict[str, Any] | None, bool]:
    """Return ``(pr, ok)``: the newest PR whose head is ``execution_branch`` (None when there is none) and whether
    ``gh`` answered at all."""
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
    """Record an origin fact on the entry; origin wins over a different checkpointed value, which is noted."""
    if value in (None, "") or entry.get(key) == value:
        return
    if entry.get(key) not in (None, ""):
        notes.append(f"sub-intent '{entry.get('slug')}': origin has {key}={value!r}, checkpoint had {entry[key]!r}")
    entry[key] = value


def _reconcile_entry(repo_dir: str, entry: dict[str, Any], runner: GhRunner, notes: list[str]) -> None:
    """Fill one entry's facts from origin and GitHub, promoting it to ``merged`` when its PR merged."""
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
        return
    if pr is None:
        return
    _apply_fact(entry, "pr_number", pr.get("number"), notes)
    _apply_fact(entry, "pr_url", pr.get("url"), notes)
    if str(pr.get("state") or "").upper() == "MERGED":
        commit = pr.get("mergeCommit")
        _apply_fact(entry, "merge_commit", commit.get("oid") if isinstance(commit, dict) else None, notes)
        _apply_fact(entry, "status", MERGED, notes)


def reconcile_with_origin(repo_dir: str, progress: list[dict[str, Any]], runner: GhRunner | None = None) -> list[str]:
    """Reconcile checkpointed progress with origin before a resume, in place.

    For every child not yet merged, its execution branches are discovered under its intent branch on origin and its
    PR is read with ``gh pr list --head <execution branch> --state all``. Missing facts are filled in, a merged PR makes
    the entry ``merged``, and where origin and the checkpoint disagree origin wins. A failed fetch or ``gh`` lookup
    marks the entry ``origin_lookup_failed``, which makes :func:`next_child_action` resume it, never dispatch it.

    Returns:
        list[str]: One note per disagreement between origin and the checkpoint, for the flow log.
    """
    gh = gh_runner(runner)
    notes: list[str] = []
    for entry in progress:
        entry.pop("origin_lookup_failed", None)
        if entry.get("status") != MERGED:
            _reconcile_entry(repo_dir, entry, gh, notes)
    return notes


def checkpoint_exists(context: FlowContext, entry: dict[str, Any]) -> bool:
    """True when the child of ``entry`` has its own checkpoint file."""
    return os.path.exists(child_checkpoint_path(context, str(entry.get("intent_branch") or "")))
