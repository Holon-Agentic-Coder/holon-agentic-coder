"""Stage 2 resume adoption: discover existing plan branches under an intent, check completeness and adopt the newest.

On entry to Stage 2 (outside dry runs and engine re-plans carrying ``replan_feedback``), the stage inspects every
``<intent prefix>/P-.../_`` branch on ``origin`` and in the local worktree. A branch is complete when a ``plans.jsonl``
row of that plan and the plan file that row records both exist at the branch ref (``refs/remotes/origin/<branch>`` when
on ``origin``, else ``refs/heads/<branch>``). When at least one complete branch exists, the stage adopts the newest
complete one (largest plan-id timestamp, ties broken by branch name), pushes it first if it exists only locally, logs
every candidate, the ignored incomplete ones with their reason and the adopted choice, and proceeds without dispatching
the planner.
"""

from __future__ import annotations

from dataclasses import dataclass

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.git_refs import _git_show_text, _list_child_branches, _read_ledger_rows
from sandbox_executor.flow.host_remote import new_local_child_branches, push_branch, remote_error
from sandbox_executor.plan_paths import is_row_of_plan, recorded_plan_file, split_plan_branch

AMBIGUOUS_PLAN_BRANCHES_HINT = (
    "resume with `--from-stage plan`: Stage 2 then adopts the newest complete plan branch, "
    "or delete the branches that should not be used"
)


def _plan_timestamp(branch: str) -> int:
    """Return the integer epoch timestamp from the ``P-<timestamp>-...`` segment of ``branch`` (0 if unparseable)."""
    _, plan_id = split_plan_branch(branch)
    parts = plan_id.split("-")
    if len(parts) >= 2 and parts[1].isdigit():
        return int(parts[1])
    return 0


@dataclass(frozen=True)
class PlanCandidate:
    """One existing ``P-.../_`` branch under an intent prefix, evaluated for Stage 2 resume adoption."""

    branch: str
    ref: str
    published: bool
    missing: str = ""

    @property
    def complete(self) -> bool:
        """True when both the ``plans.jsonl`` row of this plan and its recorded plan file exist at ``ref``."""
        return not self.missing

    def sort_key(self) -> tuple[int, str]:
        """Sort key ordering candidates oldest first: numeric plan-id timestamp, then branch name."""
        return (_plan_timestamp(self.branch), self.branch)


def _missing_reason(repo_dir: str, branch: str, ref: str) -> str:
    """Return why ``branch`` at ``ref`` is incomplete, or ``""`` when its plan row and plan file both exist."""
    rows = [r for r in _read_ledger_rows(repo_dir, ref, "plans.jsonl") if is_row_of_plan(r, branch)]
    if not rows:
        return "no plans.jsonl row"
    plan_file = recorded_plan_file(rows[-1].get("plan_file"), branch)
    if _git_show_text(repo_dir, ref, plan_file) is None:
        return "plan file not found"
    return ""


def plan_candidates(repo_dir: str, intent_prefix: str) -> list[PlanCandidate]:
    """Return every ``<intent_prefix>/P-.../_`` candidate on origin or locally, ordered oldest first."""
    origin_branches = _list_child_branches(repo_dir, intent_prefix, "P")
    local_branches = set(new_local_child_branches(repo_dir, intent_prefix, "P", set()))
    candidates: list[PlanCandidate] = []
    for branch in origin_branches | local_branches:
        published = branch in origin_branches
        ref = f"refs/remotes/origin/{branch}" if published else f"refs/heads/{branch}"
        missing = _missing_reason(repo_dir, branch, ref)
        candidates.append(PlanCandidate(branch=branch, ref=ref, published=published, missing=missing))
    return sorted(candidates, key=PlanCandidate.sort_key)


def _log_adopted_choice(context: FlowContext, chosen: PlanCandidate) -> None:
    """Log the adopted newest complete plan branch, checkpoint match status, and how to force a fresh plan."""
    recorded = context.plan_branch
    if recorded == chosen.branch:
        checkpoint_note = f"matches checkpoint plan_branch '{recorded}'"
    elif recorded:
        checkpoint_note = f"differs from checkpoint plan_branch '{recorded}'"
    else:
        checkpoint_note = "checkpoint recorded no plan_branch"
    context.log(
        f"Stage 2: adopting existing plan branch '{chosen.branch}' (newest complete plan branch; "
        f"{checkpoint_note}). To plan afresh, delete the plan branch locally and on origin."
    )


def adopt_existing_plan(context: FlowContext, intent_prefix: str) -> tuple[str, str | None]:
    """Adopt the newest complete plan branch under ``intent_prefix``, publishing it first if local-only.

    Returns:
        tuple[str, str | None]: ``(adopted_branch, None)`` when a complete branch is adopted, ``("", None)`` when
        Stage 2 should dispatch the planner as usual, or ``("", error)`` when publishing a local branch failed.
    """
    if str(context.intent_data.get("replan_feedback") or "").strip():
        return "", None
    repo_dir = context.target_worktree
    candidates = plan_candidates(repo_dir, intent_prefix)
    if candidates:
        names = ", ".join(c.branch for c in candidates)
        context.log(f"Stage 2: existing plan branch candidates under '{intent_prefix}': {names}")
    for candidate in candidates:
        if not candidate.complete:
            context.log(f"Stage 2: incomplete plan branch '{candidate.branch}' ignored ({candidate.missing}).")
    complete = [c for c in candidates if c.complete]
    if not complete:
        context.log(f"Stage 2: no complete existing plan branch under '{intent_prefix}'; planning as usual.")
        return "", None
    chosen = complete[-1]
    if not chosen.published:
        push_err = remote_error(push_branch, repo_dir, chosen.branch)
        if push_err is not None:
            return "", push_err
    _log_adopted_choice(context, chosen)
    return chosen.branch, None
