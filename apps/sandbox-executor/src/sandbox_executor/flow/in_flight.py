"""Stage 3 admission input: the flows in flight beside this one and the files they declare or change.

A flow is in flight when it has an open pull request whose head is a flow execution branch (``I-.../P-.../E-.../_``),
or a checkpoint in another git worktree whose stage is execute or review, whose plan branch still resolves and whose
Stage 3 admission state is not ``blocked`` (a refused flow waits; it blocks no one). Each carries the reference a
``blocked_by`` entry names and what admission compares: a pull request only its changed files (its ``gh pr diff``
patch, or paginated REST ``pulls/{number}/files`` when ``gh pr diff`` hits GitHub's HTTP 406 diff ceiling, rename
sources included), never its planned touch set; a local flow its declared touch set (its plan branch's newest
``plans.jsonl`` row, from refs).
This run's root intent and children are excluded; a local flow with an open pull request is listed once, so it stays
in flight whatever its checkpoint says. :func:`local_blocked_waiters` lists the blocked ones.

Discovery is host-side and read-only via injected ``gh``/``git`` runners; failures return :class:`AdmissionUnavailable`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sandbox_executor.flow.admission_state import BLOCKED_BY_LOCAL_FLOW, BLOCKED_BY_PULL_REQUEST, AdmissionRecord
from sandbox_executor.flow.git_refs import _read_ledger_rows, _resolve_branch_ref
from sandbox_executor.flow.github import merge
from sandbox_executor.flow.pr_changed_files import _pull_request_changed_files
from sandbox_executor.flow.worktree_checkpoints import WorktreeCheckpoint, worktree_checkpoints
from sandbox_executor.git import run_git
from sandbox_executor.plan_paths import is_row_of_plan
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.ref_names import INTENT_SUFFIX
from sandbox_executor.touch_set import TouchSet

#: Runs ``git <args>`` in a directory and returns the completed process (``check=False`` semantics).
GitRunner = Callable[[list[str], str], subprocess.CompletedProcess[str]]

#: Open flow pull requests compared per admission; more make discovery unavailable (fail closed) rather than truncated.
MAX_OPEN_PULL_REQUESTS = 50
#: Open pull requests of any head listed per admission, before the flow heads among them are picked out.
MAX_LISTED_PULL_REQUESTS = 1000
_EXECUTION_HEAD_RE = re.compile(r"^(?P<plan>I-\d+-\S*/P-\d+-[^/]+)/E-\d+-[^/]+/_$")


@dataclass(frozen=True)
class InFlightFlow:
    """A flow in flight: label, plan branch and reference (pull request ``number`` and ``head``, else the local
    ``worktree``); a pull request carries its ``changed_files``, a local flow (or an older waiter) its ``declared``."""

    label: str
    plan_branch: str
    declared: TouchSet | None = None
    changed_files: tuple[str, ...] | None = None
    number: int | None = None
    head: str | None = None
    worktree: str | None = None

    @property
    def kind(self) -> str:
        """The ``blocked_by`` kind naming this flow: a pull request when it has a number, else a local flow."""
        return BLOCKED_BY_PULL_REQUEST if self.number is not None else BLOCKED_BY_LOCAL_FLOW


@dataclass(frozen=True)
class BlockedWaiter:
    """A local flow Stage 3 refused for an overlap: it waits (not in flight) with its recorded admission state."""

    plan_branch: str
    worktree: str
    declared: TouchSet | None
    record: AdmissionRecord


@dataclass(frozen=True)
class AdmissionUnavailable:
    """Discovery could not run (a ``gh`` or ``git`` failure); admission fails closed with this reason."""

    reason: str


def host_git(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    return run_git(args, cwd=cwd, check=False)


def read_plan_touch_set(repo_dir: str, plan_branch: str) -> TouchSet | None:
    """The ``touch_set`` of the newest ``plans.jsonl`` row of ``plan_branch``; ``None`` when absent or unreadable."""
    plan_ref = _resolve_branch_ref(repo_dir, plan_branch)
    if not plan_ref:
        return None
    rows = [row for row in _read_ledger_rows(repo_dir, plan_ref, "plans.jsonl") if is_row_of_plan(row, plan_branch)]
    return TouchSet.from_row(rows[-1].get("touch_set")) if rows else None


def _plan_branch_of_head(head: str) -> str | None:
    match = _EXECUTION_HEAD_RE.match(head)
    return match.group("plan") + INTENT_SUFFIX if match else None


def root_intent(branch: str) -> str:
    return branch.split("/", 1)[0].split("--", 1)[0]


def command_failure(command: str, result: subprocess.CompletedProcess[str]) -> AdmissionUnavailable:
    detail = redact_agent_secrets((result.stderr or "").strip())[:300]
    return AdmissionUnavailable(f"'{command}' failed: {detail or 'no output'}")


def _gh_json(run_gh: merge.GhRunner, args: list[str], repo_dir: str) -> list[dict[str, Any]] | AdmissionUnavailable:
    result = run_gh(args, repo_dir)
    if result.returncode != 0:
        return command_failure(f"gh {' '.join(args[:2])}", result)
    try:
        data = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return AdmissionUnavailable(f"'gh {' '.join(args[:2])}' returned output that is not JSON")
    if not isinstance(data, list):
        return AdmissionUnavailable(f"'gh {' '.join(args[:2])}' returned JSON that is not a list")
    return [item for item in data if isinstance(item, dict)]


def _pull_request_diff(run_gh: merge.GhRunner, number: int, repo_dir: str) -> tuple[str, ...] | AdmissionUnavailable:
    changed = _pull_request_changed_files(run_gh, number, repo_dir)
    return AdmissionUnavailable(changed) if isinstance(changed, str) else changed


def open_flow_pull_requests(
    repo_dir: str, own_root: str, run_gh: merge.GhRunner | None = None
) -> list[InFlightFlow] | AdmissionUnavailable:
    """Open pull requests whose head is a flow execution branch outside ``own_root``, with their changed files."""
    gh = merge.gh_runner(run_gh)
    limit = str(MAX_LISTED_PULL_REQUESTS + 1)
    fields = ["--state", "open", "--limit", limit, "--json", "number,headRefName,baseRefName"]
    listed = _gh_json(gh, ["pr", "list", *fields], repo_dir)
    if isinstance(listed, AdmissionUnavailable):
        return listed
    if len(listed) > MAX_LISTED_PULL_REQUESTS:
        return AdmissionUnavailable(f"more than {MAX_LISTED_PULL_REQUESTS} open pull requests; not all could be listed")
    others = [
        (pr, head, plan_branch)
        for pr in listed
        if (plan_branch := _plan_branch_of_head(head := str(pr.get("headRefName") or ""))) is not None
        and root_intent(head) != own_root
    ]
    if len(others) > MAX_OPEN_PULL_REQUESTS:
        return AdmissionUnavailable(f"more than {MAX_OPEN_PULL_REQUESTS} open flow pull requests; not all compared")
    flows: list[InFlightFlow] = []
    for pr, head, plan_branch in others:
        changed = _pull_request_diff(gh, int(pr.get("number") or 0), repo_dir)
        if isinstance(changed, AdmissionUnavailable):
            return changed
        number = int(pr.get("number") or 0)
        label = f"pull request #{number} (branch {head})"
        flows.append(InFlightFlow(label, plan_branch, None, changed, number=number, head=head))
    return flows


def _other_worktrees(repo_dir: str, run_git: GitRunner) -> list[str] | AdmissionUnavailable:
    result = run_git(["worktree", "list", "--porcelain"], repo_dir)
    if result.returncode != 0:
        return command_failure("git worktree list", result)
    own = os.path.realpath(repo_dir)
    paths = [line[len("worktree ") :] for line in (result.stdout or "").splitlines() if line.startswith("worktree ")]
    return [path for path in paths if os.path.realpath(path) != own]


def other_checkpoints(
    repo_dir: str, own_root: str, run_git: GitRunner | None = None
) -> list[WorktreeCheckpoint] | AdmissionUnavailable:
    """Checkpoints at execute or review in other worktrees, outside ``own_root``, whose plan branch still resolves."""
    worktrees = _other_worktrees(repo_dir, run_git or host_git)
    if isinstance(worktrees, AdmissionUnavailable):
        return worktrees
    return [
        checkpoint
        for worktree in worktrees
        for checkpoint in worktree_checkpoints(worktree)
        if checkpoint.in_flight_stage
        and root_intent(checkpoint.plan_branch) != own_root
        and _resolve_branch_ref(repo_dir, checkpoint.plan_branch) is not None  # a deleted plan branch is stale
    ]


def local_in_flight_flows(repo_dir: str, checkpoints: list[WorktreeCheckpoint]) -> list[InFlightFlow]:
    """Flows of ``checkpoints`` (:func:`other_checkpoints`) that are not blocked: in flight whatever their stage."""
    return [
        InFlightFlow(
            f"local flow on branch {checkpoint.plan_branch} (worktree {checkpoint.worktree})",
            checkpoint.plan_branch,
            read_plan_touch_set(repo_dir, checkpoint.plan_branch),
            worktree=checkpoint.worktree,
        )
        for checkpoint in checkpoints
        if checkpoint.record is None or not checkpoint.record.blocked  # no record: an older flow, still in flight
    ]


def local_blocked_waiters(repo_dir: str, checkpoints: list[WorktreeCheckpoint]) -> list[BlockedWaiter]:
    """Flows of ``checkpoints`` (:func:`other_checkpoints`) whose checkpoint records a ``blocked`` admission state."""
    return [
        BlockedWaiter(c.plan_branch, c.worktree, read_plan_touch_set(repo_dir, c.plan_branch), c.record)
        for c in checkpoints
        if c.record is not None and c.record.blocked
    ]


def discover_in_flight_flows(
    repo_dir: str,
    own_branch: str,
    run_gh: merge.GhRunner | None = None,
    run_git: GitRunner | None = None,
    checkpoints: list[WorktreeCheckpoint] | None = None,
) -> list[InFlightFlow] | AdmissionUnavailable:
    """Open flow pull requests, then local flows without one, excluding the lineage of ``own_branch``'s root intent;
    ``checkpoints`` are :func:`other_checkpoints` already read for this check (``run_git`` reads them otherwise)."""
    own_root = root_intent(own_branch)
    pull_requests = open_flow_pull_requests(repo_dir, own_root, run_gh)
    if isinstance(pull_requests, AdmissionUnavailable):
        return pull_requests
    checkpoints = other_checkpoints(repo_dir, own_root, run_git) if checkpoints is None else checkpoints
    if isinstance(checkpoints, AdmissionUnavailable):
        return checkpoints
    flows = list(pull_requests)
    for flow in local_in_flight_flows(repo_dir, checkpoints):
        if flow.plan_branch not in {known.plan_branch for known in flows}:
            flows.append(flow)
    return flows
