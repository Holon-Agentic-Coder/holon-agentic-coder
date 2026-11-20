"""Stage 3 admission input: the flows in flight beside this one and the files they declare or change.

A flow is in flight when it has an open pull request whose head is a flow execution branch (``I-.../P-.../E-.../_``),
or a checkpoint in another git worktree whose stage is execute or review and whose plan branch still resolves. Each
carries its declared touch set (its plan branch's newest ``plans.jsonl`` row, from refs) and, for a pull request, its
changed files. This run's root intent and children are excluded; a local flow with an open pull request is listed once.

Discovery is host-side and read-only via injected ``gh``/``git`` runners; failures return :class:`AdmissionUnavailable`.
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
from sandbox_executor.plan_paths import is_row_of_plan
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.ref_names import INTENT_SUFFIX
from sandbox_executor.touch_set import TouchSet, normalise_touch_path

logger = logging.getLogger(__name__)

#: Runs ``git <args>`` in a directory and returns the completed process (``check=False`` semantics).
GitRunner = Callable[[list[str], str], subprocess.CompletedProcess[str]]

#: Open flow pull requests compared per admission; more make discovery unavailable (fail closed) rather than truncated.
MAX_OPEN_PULL_REQUESTS = 50
#: Open pull requests of any head listed per admission, before the flow heads among them are picked out.
MAX_LISTED_PULL_REQUESTS = 1000
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
    rows = [row for row in _read_ledger_rows(repo_dir, plan_ref, "plans.jsonl") if is_row_of_plan(row, plan_branch)]
    return TouchSet.from_row(rows[-1].get("touch_set")) if rows else None


def _plan_branch_of_head(head: str) -> str | None:
    match = _EXECUTION_HEAD_RE.match(head)
    return match.group("plan") + INTENT_SUFFIX if match else None


def _root_intent(branch: str) -> str:
    return branch.split("/", 1)[0].split("--", 1)[0]


def _failure(command: str, result: subprocess.CompletedProcess[str]) -> AdmissionUnavailable:
    detail = redact_agent_secrets((result.stderr or "").strip())[:300]
    return AdmissionUnavailable(f"'{command}' failed: {detail or 'no output'}")


def _gh_json(run_gh: merge.GhRunner, args: list[str], repo_dir: str) -> list[dict[str, Any]] | AdmissionUnavailable:
    result = run_gh(args, repo_dir)
    if result.returncode != 0:
        return _failure(f"gh {' '.join(args[:2])}", result)
    try:
        data = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return AdmissionUnavailable(f"'gh {' '.join(args[:2])}' returned output that is not JSON")
    if not isinstance(data, list):
        return AdmissionUnavailable(f"'gh {' '.join(args[:2])}' returned JSON that is not a list")
    return [item for item in data if isinstance(item, dict)]


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
        and _root_intent(head) != own_root
    ]
    if len(others) > MAX_OPEN_PULL_REQUESTS:
        return AdmissionUnavailable(f"more than {MAX_OPEN_PULL_REQUESTS} open flow pull requests; not all compared")
    flows: list[InFlightFlow] = []
    for pr, head, plan_branch in others:
        changed = _pull_request_diff(gh, int(pr.get("number") or 0), repo_dir)
        if isinstance(changed, AdmissionUnavailable):
            return changed
        label = f"pull request #{pr.get('number')} (branch {head})"
        if _resolve_branch_ref(repo_dir, plan_branch) is None:
            logger.warning("Plan branch %s of %s is not on this host; only its diff is compared", plan_branch, label)
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
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        logger.debug("Skipping unreadable checkpoint %s: %s", path, exc)  # stale or foreign: never blocks
        return None
    return (checkpoint.context.plan_branch or None) if checkpoint.current_stage in _IN_FLIGHT_STAGES else None


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
    for flow in local:
        if flow.plan_branch not in {known.plan_branch for known in flows}:
            flows.append(flow)
    return flows
