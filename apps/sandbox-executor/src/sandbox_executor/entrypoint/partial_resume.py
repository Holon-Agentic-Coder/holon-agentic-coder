"""Where a resumed execution starts: the plan-lineage rule and the choice between the recorded head and the branch tip.

A failed execution's partial work forks from the plan tip of its day. The plan branch may have moved since, but only by
engine-owned ledger commits (an ``--allow-overlap`` override row, for one) is that work still this plan's: its fork
point lies on the plan branch's own first-parent history, past the intent branch tip, and the plan branch changed
nothing but ``holon-knowledge/ledger/*.jsonl`` after it. A failed execution branch may also have moved past its recorded
``partial_head`` (a human merged ``origin/main`` to settle a sync conflict, say); the branch tip is then preferred when
it still contains that head and passes the same rule.

Each helper takes the caller's GitRepository, so the git commands run through the executor's own runner.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from sandbox_executor.git import GitRepository

#: The reason of a resume from the recorded partial head with nothing to report.
RESUMED = "resumed"

_NOT_A_DESCENDANT = "not a descendant of the plan tip"


@dataclass(frozen=True)
class ResumeStart:
    """The commit a resumed execution starts from (None: the plan tip) and why."""

    head: str | None
    reason: str


def _stdout(repo: GitRepository, args: list[str]) -> str | None:
    res = repo.run(args, check=False)
    return (res.stdout or "").strip() if res.returncode == 0 else None


def _is_ancestor(repo: GitRepository, ancestor: str, descendant: str) -> bool:
    return repo.merge_base(ancestor, descendant, is_ancestor=True).returncode == 0


def _is_ledger_path(path: str) -> bool:
    return path.startswith("holon-knowledge/ledger/") and path.endswith(".jsonl")


def intent_branch_tip(repo: GitRepository, plan_branch_prefix: str) -> str | None:
    """The tip of the plan's intent branch the host fetched (origin first, then local); None when neither resolves."""
    intent_branch = f"{plan_branch_prefix.rsplit('/', 1)[0]}/_"
    for ref in (f"refs/remotes/origin/{intent_branch}", f"refs/heads/{intent_branch}"):
        tip = _stdout(repo, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
        if tip:
            return tip
    return None


def plan_lineage_refusal(repo: GitRepository, plan_tip: str, head: str, intent_tip: str | None) -> str | None:
    """Return why ``head`` is not partial work of this plan branch, or None when it is.

    ``head`` qualifies when it descends from ``plan_tip``, or when its fork point with ``plan_tip`` lies on the plan
    branch's first-parent history after the intent branch tip and the plan branch changed only its ledgers since.
    Without a resolvable intent tip that sub-check is skipped, with a note on stderr.
    """
    if _is_ancestor(repo, plan_tip, head):
        return None
    fork = _stdout(repo, ["merge-base", plan_tip, head])
    if not fork:
        return f"{_NOT_A_DESCENDANT}, and it shares no history with the plan branch"
    if intent_tip is None:
        print("Note: the intent branch tip does not resolve; its fork-point check is skipped.", file=sys.stderr)
    plan_history = _stdout(repo, ["rev-list", "--first-parent", plan_tip, *([f"^{intent_tip}"] if intent_tip else [])])
    if fork not in (plan_history or "").split():
        return f"{_NOT_A_DESCENDANT}, and it forked off the plan branch history at {fork[:12]}"
    changed = _stdout(repo, ["diff", "--name-only", fork, plan_tip])
    if changed is None or not all(_is_ledger_path(path) for path in changed.splitlines() if path):
        return f"{_NOT_A_DESCENDANT}, and the plan branch changed more than its ledgers since {fork[:12]}"
    return None


def _changes_only_engine_owned(repo: GitRepository, base: str, tip: str, excludes: tuple[str, ...]) -> bool:
    changed = _stdout(repo, ["diff", "--name-only", base, tip, "--", ".", *excludes])
    return changed is not None and not changed


def resume_from_branch_tip(
    repo: GitRepository,
    branch_tip: str | None,
    partial_head: str,
    lineage: tuple[str, str | None],
    excludes: tuple[str, ...],
) -> ResumeStart:
    """Choose between the recorded ``partial_head`` and the failed branch's tip (``lineage``: plan tip, intent tip).

    The tip wins when it strictly descends from the head, adds more than engine-owned files (``excludes``, the
    pathspecs of the ledgers and notes the failure commit carries) and passes :func:`plan_lineage_refusal`; otherwise
    the recorded head is kept, and the reason says why when the tip was passed over.
    """
    if (
        not branch_tip
        or branch_tip == partial_head
        or _changes_only_engine_owned(repo, partial_head, branch_tip, excludes)
    ):
        return ResumeStart(partial_head, RESUMED)
    tip12, head12 = branch_tip[:12], partial_head[:12]
    if not _is_ancestor(repo, partial_head, branch_tip):
        reason = f"the branch tip {tip12} does not descend from the recorded partial head {head12}"
        return ResumeStart(partial_head, f"{reason}; resuming from the recorded head")
    refusal = plan_lineage_refusal(repo, lineage[0], branch_tip, lineage[1])
    if refusal:
        return ResumeStart(partial_head, f"the branch tip {tip12} is {refusal}; resuming from the recorded head")
    count = _stdout(repo, ["rev-list", "--count", f"{partial_head}..{branch_tip}"]) or "?"
    reason = f"resumed from the branch tip {tip12}, {count} commit(s) after the recorded partial head {head12}"
    return ResumeStart(branch_tip, reason)
