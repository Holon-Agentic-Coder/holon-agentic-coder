"""Stage 3 admission input: the flows in flight beside this one and the files they declare or change.

A flow is in flight when it has an open pull request whose head is a flow execution branch
(``I-.../P-.../E-.../_``), or a checkpoint in another git worktree of this repository whose current stage is execute or
review and whose plan branch still resolves. Each flow carries its declared touch set (the newest ``plans.jsonl`` row
of its plan branch, read from refs only) and, for a pull request, the real changed files. Flows of this run's own root
intent are excluded, and a local flow that also has an open pull request is listed once.

Discovery runs host-side only: ``gh`` and ``git`` are injected runners (tests pass fakes), nothing is fetched, checked
out or written, and any ``gh`` or ``git`` failure returns :class:`AdmissionUnavailable` instead of raising.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sandbox_executor.flow.checkpoint import load_checkpoint
from sandbox_executor.flow.context import FlowStage
from sandbox_executor.flow.git_refs import _read_ledger_rows, _resolve_branch_ref
from sandbox_executor.flow.github import merge
from sandbox_executor.git import is_metadata_path, run_git
from sandbox_executor.touch_set import TouchSet, normalise_touch_path

logger = logging.getLogger(__name__)

#: Runs ``git <args>`` in a directory and returns the completed process (``check=False`` semantics).
GitRunner = Callable[[list[str], str], subprocess.CompletedProcess[str]]

#: Open pull requests read per admission; more are ignored with a logged warning.
MAX_OPEN_PULL_REQUESTS = 50
_IN_FLIGHT_STAGES = frozenset({FlowStage.EXECUTE, FlowStage.REVIEW})
_EXECUTION_HEAD_RE = re.compile(r"^(?P<plan>I-\d+-\S*/P-\d+-[^/]+)/E-\d+-[^/]+/_$")


@dataclass(frozen=True)
class InFlightFlow:
    """A flow in flight: its label, plan branch, declared touch set and, for a pull request, its changed files."""

    label: str
    plan_branch: str
    declared: TouchSet | None = None
    changed_files: tuple[str, ...] | None = None


@dataclass(frozen=True)
class AdmissionUnavailable:
    """Discovery could not run (a ``gh`` or ``git`` failure); admission fails closed with this reason."""

    reason: str


def _host_git(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    return run_git(args, cwd=cwd, check=False)


def read_plan_touch_set(repo_dir: str, plan_branch: str) -> TouchSet | None:
    """The ``touch_set`` of the newest ``plans.jsonl`` row of ``plan_branch``; ``None`` when absent or unreadable."""
    plan_ref = _resolve_branch_ref(repo_dir, plan_branch)
    if not plan_ref:
        return None
    plan_id = plan_branch.removesuffix("/_").rsplit("/", 1)[-1]
    rows = [row for row in _read_ledger_rows(repo_dir, plan_ref, "plans.jsonl") if row.get("plan_id") == plan_id]
    return TouchSet.from_row(rows[-1].get("touch_set")) if rows else None


def _plan_branch_of_head(head: str) -> str | None:
    match = _EXECUTION_HEAD_RE.match(head)
    return f"{match.group('plan')}/_" if match else None


def _root_intent(branch: str) -> str:
    return branch.split("/", 1)[0]


def _failure(command: str, result: subprocess.CompletedProcess[str]) -> AdmissionUnavailable:
    return AdmissionUnavailable(f"'{command}' failed: {(result.stderr or '').strip() or 'no output'}")


def _gh_json(run_gh: merge.GhRunner, args: list[str], repo_dir: str) -> list[dict[str, Any]] | AdmissionUnavailable:
    result = run_gh(args, repo_dir)
    if result.returncode != 0:
        return _failure(f"gh {' '.join(args[:2])}", result)
    try:
        data = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return AdmissionUnavailable(f"'gh {' '.join(args[:2])}' returned output that is not JSON")
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _pull_request_diff(run_gh: merge.GhRunner, number: int, repo_dir: str) -> tuple[str, ...] | AdmissionUnavailable:
    result = run_gh(["pr", "diff", str(number), "--name-only"], repo_dir)
    if result.returncode != 0:
        return _failure(f"gh pr diff {number}", result)
    paths = (normalise_touch_path(line) for line in (result.stdout or "").splitlines())
    return tuple(dict.fromkeys(path for path in paths if path and not is_metadata_path(path)))


def open_flow_pull_requests(
    repo_dir: str, own_root: str, run_gh: merge.GhRunner | None = None
) -> list[InFlightFlow] | AdmissionUnavailable:
    """Open pull requests whose head is a flow execution branch outside ``own_root``, with declared and real files."""
    gh = merge.gh_runner(run_gh)
    fields = ["--state", "open", "--limit", str(MAX_OPEN_PULL_REQUESTS), "--json", "number,headRefName,baseRefName"]
    listed = _gh_json(gh, ["pr", "list", *fields], repo_dir)
    if isinstance(listed, AdmissionUnavailable):
        return listed
    if len(listed) >= MAX_OPEN_PULL_REQUESTS:
        logger.warning("Admission read only the first %d open pull requests.", MAX_OPEN_PULL_REQUESTS)
    flows: list[InFlightFlow] = []
    for pr in listed:
        head = str(pr.get("headRefName") or "")
        plan_branch = _plan_branch_of_head(head)
        if plan_branch is None or _root_intent(head) == own_root:
            continue
        changed = _pull_request_diff(gh, int(pr.get("number") or 0), repo_dir)
        if isinstance(changed, AdmissionUnavailable):
            return changed
        label = f"pull request #{pr.get('number')} (branch {head})"
        flows.append(InFlightFlow(label, plan_branch, read_plan_touch_set(repo_dir, plan_branch), changed))
    return flows


def _other_worktrees(repo_dir: str, run_git: GitRunner) -> list[str] | AdmissionUnavailable:
    result = run_git(["worktree", "list", "--porcelain"], repo_dir)
    if result.returncode != 0:
        return _failure("git worktree list", result)
    own = os.path.realpath(repo_dir)
    paths = [line[len("worktree ") :] for line in (result.stdout or "").splitlines() if line.startswith("worktree ")]
    return [path for path in paths if os.path.realpath(path) != own]


def _checkpoint_plan_branch(path: str) -> str | None:
    """The plan branch of a checkpoint whose current stage is execute or review; ``None`` for any other or bad file."""
    try:
        checkpoint = load_checkpoint(path)
    except (OSError, ValueError, KeyError, TypeError) as exc:  # a stale or foreign file never blocks admission
        logger.debug("Skipping unreadable checkpoint %s: %s", path, exc)
        return None
    if checkpoint.current_stage not in _IN_FLIGHT_STAGES:
        return None
    return checkpoint.context.plan_branch or None


def local_in_flight_flows(
    repo_dir: str, own_root: str, run_git: GitRunner | None = None
) -> list[InFlightFlow] | AdmissionUnavailable:
    """Flows whose checkpoint in another worktree is at execute or review and whose plan branch still resolves."""
    worktrees = _other_worktrees(repo_dir, run_git or _host_git)
    if isinstance(worktrees, AdmissionUnavailable):
        return worktrees
    flows: list[InFlightFlow] = []
    for worktree in worktrees:
        for path in sorted(glob.glob(os.path.join(worktree, ".holon", "flow", "checkpoint-*.json"))):
            plan_branch = _checkpoint_plan_branch(path)
            if not plan_branch or _root_intent(plan_branch) == own_root:
                continue
            if _resolve_branch_ref(repo_dir, plan_branch) is None:
                continue  # the plan branch was deleted: the checkpoint is stale
            label = f"local flow on branch {plan_branch} (worktree {worktree})"
            flows.append(InFlightFlow(label, plan_branch, read_plan_touch_set(repo_dir, plan_branch)))
    return flows


def discover_in_flight_flows(
    repo_dir: str, own_branch: str, run_gh: merge.GhRunner | None = None, run_git: GitRunner | None = None
) -> list[InFlightFlow] | AdmissionUnavailable:
    """Open flow pull requests, then local flows without one, excluding the lineage of ``own_branch``'s root intent."""
    own_root = _root_intent(own_branch)
    pull_requests = open_flow_pull_requests(repo_dir, own_root, run_gh)
    if isinstance(pull_requests, AdmissionUnavailable):
        return pull_requests
    local = local_in_flight_flows(repo_dir, own_root, run_git)
    if isinstance(local, AdmissionUnavailable):
        return local
    flows = list(pull_requests)
    seen = {flow.plan_branch for flow in flows}
    for flow in local:
        if flow.plan_branch not in seen:
            seen.add(flow.plan_branch)
            flows.append(flow)
    return flows
