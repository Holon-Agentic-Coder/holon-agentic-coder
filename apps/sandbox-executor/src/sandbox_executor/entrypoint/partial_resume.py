from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass

from sandbox_executor.git import GitRepository
from sandbox_executor.plan_paths import REFRESH_MERGE_PREFIX, split_plan_branch
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.ref_names import INTENT_SUFFIX

RESUMED = "resumed"

_NOT_A_DESCENDANT = "not a descendant of the plan tip"
_LEDGER_DIR_PREFIX = "holon-knowledge/ledger/"
_LEDGER_FILE_SUFFIX = ".jsonl"
_REFRESH_MERGE_PARENT_COUNT = 2
_MERGE_TREE_ALLOWED_EXIT_CODES = (0, 1)
_SHORT_SHA_LEN = 12


@dataclass(frozen=True)
class ResumeStart:
    head: str | None
    reason: str


def _stdout(repo: GitRepository, args: list[str]) -> str | None:
    res = repo.run(args, check=False)
    return (res.stdout or "").strip() if res.returncode == 0 else None


def _is_ancestor(repo: GitRepository, ancestor: str, descendant: str) -> bool:
    return repo.merge_base(ancestor, descendant, is_ancestor=True).returncode == 0


def _is_ledger_path(path: str) -> bool:
    return path.startswith(_LEDGER_DIR_PREFIX) and path.endswith(_LEDGER_FILE_SUFFIX)


def intent_branch_tip(repo: GitRepository, plan_branch_prefix: str) -> str | None:
    intent_branch = f"{split_plan_branch(plan_branch_prefix)[0]}{INTENT_SUFFIX}"
    for ref in (f"refs/remotes/origin/{intent_branch}", f"refs/heads/{intent_branch}"):
        tip = _stdout(repo, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
        if tip:
            return tip
    return None


def plan_lineage_refusal(repo: GitRepository, plan_tip: str, head: str, intent_tip: str | None) -> str | None:
    if _is_ancestor(repo, plan_tip, head):
        return None
    fork = _stdout(repo, ["merge-base", plan_tip, head])
    if not fork:
        return f"{_NOT_A_DESCENDANT}, and it shares no history with the plan branch"
    if intent_tip is None:
        print("Note: the intent branch tip does not resolve; its fork-point check is skipped.", file=sys.stderr)
    plan_history = _stdout(repo, ["rev-list", "--first-parent", plan_tip, *([f"^{intent_tip}"] if intent_tip else [])])
    if fork not in (plan_history or "").split():
        return f"{_NOT_A_DESCENDANT}, and it forked off the plan branch history at {fork[:_SHORT_SHA_LEN]}"
    if not _only_ledgers_changed(repo, fork, plan_tip) and not _engine_owned_since(repo, fork, plan_tip):
        return f"{_NOT_A_DESCENDANT}, and the plan branch changed more than its ledgers since {fork[:_SHORT_SHA_LEN]}"
    return None


def _only_ledgers_changed(repo: GitRepository, base: str, tip: str) -> bool:
    changed = _stdout(repo, ["diff", "--name-only", base, tip])
    return changed is not None and all(_is_ledger_path(path) for path in changed.splitlines() if path)


def _is_refresh_merge(repo: GitRepository, commit: str, parents: list[str]) -> bool:
    subject = _stdout(repo, ["log", "-1", "--format=%s", commit]) or ""
    if len(parents) != _REFRESH_MERGE_PARENT_COUNT or not subject.startswith(REFRESH_MERGE_PREFIX):
        return False
    merged = repo.run(["merge-tree", "--write-tree", *parents], check=False)
    merged_stdout = merged.stdout or ""
    tree = merged_stdout.split("\n", 1)[0].strip() if merged.returncode in _MERGE_TREE_ALLOWED_EXIT_CODES else ""
    return bool(tree) and _only_ledgers_changed(repo, tree, commit)


def _engine_owned_since(repo: GitRepository, fork: str, plan_tip: str) -> bool:
    history = _stdout(repo, ["rev-list", "--first-parent", "--parents", f"{fork}..{plan_tip}"])
    for line in (history or "").splitlines():
        commit, *parents = line.split()
        if not parents or not (
            _only_ledgers_changed(repo, parents[0], commit) or _is_refresh_merge(repo, commit, parents)
        ):
            return False
    return history is not None


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
    if (
        not branch_tip
        or branch_tip == partial_head
        or _changes_only_engine_owned(repo, partial_head, branch_tip, excludes)
    ):
        return ResumeStart(partial_head, RESUMED)
    tip_short, head_short = branch_tip[:_SHORT_SHA_LEN], partial_head[:_SHORT_SHA_LEN]
    if not _is_ancestor(repo, partial_head, branch_tip):
        reason = f"the branch tip {tip_short} does not descend from the recorded partial head {head_short}"
        return ResumeStart(partial_head, f"{reason}; resuming from the recorded head")
    refusal = plan_lineage_refusal(repo, lineage[0], branch_tip, lineage[1])
    if refusal:
        return ResumeStart(partial_head, f"the branch tip {tip_short} is {refusal}; resuming from the recorded head")
    count = _stdout(repo, ["rev-list", "--count", f"{partial_head}..{branch_tip}"]) or "?"
    reason = f"resumed from the branch tip {tip_short}, {count} commit(s) after the recorded partial head {head_short}"
    return ResumeStart(branch_tip, reason)


def merge_plan_tip(
    repo: GitRepository,
    plan_tip: str,
    configure_identity: Callable[[], None],
    reconcile: Callable[[str], tuple[bool, str]],
) -> str | None:
    if _is_ancestor(repo, plan_tip, "HEAD"):
        return None
    configure_identity()
    if repo.run(["merge", "--no-edit", plan_tip], check=False).returncode == 0:
        return None
    merged, message = reconcile(plan_tip)
    return None if merged else str(message)


def start_execution_branch(
    repo: GitRepository,
    branches: tuple[str, str],
    partial_branch: str | None,
    plan_tip: str | None,
    steps: tuple[Callable[[str], ResumeStart], Callable[[str], str | None]],
) -> str | None:
    plan_branch, exec_branch = branches
    resolve, merge = steps
    start = resolve(partial_branch) if partial_branch else ResumeStart(None, "")
    if start.head:
        print(f"Resuming partial work from {partial_branch} at {start.head}")
        if start.reason != RESUMED:
            print(f"Resume: {redact_agent_secrets(start.reason)}", file=sys.stderr)
    elif partial_branch:
        print(f"Starting from the plan tip of {plan_branch}: {redact_agent_secrets(start.reason)}", file=sys.stderr)
    start_point = start.head or plan_tip
    repo.run(["checkout", "-b", exec_branch, *([start_point] if start_point else [])])
    merge_error = merge(plan_tip) if start.head and plan_tip else None
    if merge_error and plan_tip:
        print(
            f"Starting from the plan tip of {plan_branch}: could not merge the current plan tip into the partial "
            f"work: {redact_agent_secrets(merge_error)}",
            file=sys.stderr,
        )
        repo.run(["checkout", "-B", exec_branch, plan_tip])
        return None
    return start.head
