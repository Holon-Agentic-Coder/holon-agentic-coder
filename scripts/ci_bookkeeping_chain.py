"""The walk through earlier runs for the bookkeeping-only push gate: did this job really succeed before, and where.

A job that the gate skipped also concludes ``success``, so the job conclusion alone proves nothing. Every job ends with
a step named ``FULL_RUN_STEP`` that runs only when the gate said "run" and every earlier step passed. A job of an
earlier run is therefore one of four things: a real success (job ``success``, marker step ``success``), skipped by the
gate (job ``success``, marker step ``skipped``), unverified (job ``success`` without a marker step: a run from before
the gate existed) or not successful (everything else).

``find_real_success`` starts at the newest earlier run of the same workflow on the same branch, which must be on the
commit the caller names, and follows gate-skipped jobs to older runs until it meets a real success. Anything else on
the way ends the walk without one, under a rule name the gate prints as its ``Decision`` for a full run. Runs are found
by branch, not by pull request, so a run that names several pull requests proves nothing about this one and ends the
walk too. No exception is caught here: the gate turns every exception into a full run. Standard library only and
Python 3.9 syntax.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

from scripts.ci_bookkeeping_api import CommitRef, GateClient, RunRef, commit_ref, list_earlier_runs, list_jobs

FULL_RUN_STEP = "Full run completed"
MAX_CHAIN_HOPS = 20

REAL_SUCCESS = "real-success"
GATE_SKIPPED = "gate-skipped"
UNVERIFIED = "unverified"
NOT_SUCCESSFUL = "not-successful"

RULE_PREVIOUS_RUN_MISSING = "previous-run-missing"
RULE_SHARED_HEAD_BRANCH = "shared-head-branch"
RULE_PREVIOUS_RUN_NOT_FINISHED = "previous-run-not-finished"
RULE_PREVIOUS_RUN_NOT_SUCCESSFUL = "previous-run-not-successful"
RULE_PREVIOUS_RUN_UNVERIFIED = "previous-run-unverified"
RULE_CHAIN_TOO_LONG = "chain-too-long"
RULE_PREVIOUS_RUN_SUCCEEDED = "previous-run-succeeded"
CHAIN_RULES = (
    RULE_PREVIOUS_RUN_MISSING,
    RULE_SHARED_HEAD_BRANCH,
    RULE_PREVIOUS_RUN_NOT_FINISHED,
    RULE_PREVIOUS_RUN_NOT_SUCCESSFUL,
    RULE_PREVIOUS_RUN_UNVERIFIED,
    RULE_CHAIN_TOO_LONG,
    RULE_PREVIOUS_RUN_SUCCEEDED,
)


@dataclass(frozen=True)
class JobQuery:
    """Which job to look for: the workflow, the branch, the job's names, the current run and the expected commit."""

    workflow_file: str
    branch: str
    job_name: str
    os_name: str
    run_number: int
    target_sha: str


@dataclass(frozen=True)
class Decision:
    """The gate's answer: skip or run, the rule that decided and a short detail."""

    skip: bool
    rule: str
    detail: str = ""

    def line(self) -> str:
        """Render the one output line; characters that are not printable never reach the log."""
        text = f"bookkeeping-gate: decision={'SKIP' if self.skip else 'RUN'} rule={self.rule}"
        detail = "".join(char if char.isprintable() else "?" for char in self.detail)
        return f"{text} detail={detail}" if detail else text


@dataclass(frozen=True)
class ChainResult:
    """The walk's result: the commit of the real success (``None`` when there is none), the rule and a detail."""

    real_sha: str | None
    rule: str
    detail: str = ""

    def run_decision(self) -> Decision:
        """Return the "run" decision for a walk that found no real success."""
        return Decision(False, self.rule, self.detail)


def _is_match(name: str, job_name: str, os_name: str) -> bool:
    """Return true for ``job_name`` itself and for ``job_name (values)`` that holds ``os_name`` when one is given."""
    if name == job_name:
        return True
    prefix = f"{job_name} ("
    if not (name.startswith(prefix) and name.endswith(")")):
        return False
    values = [value.strip() for value in name[len(prefix) : -1].split(",")]
    return not os_name or os_name in values


def match_job(jobs: Sequence[dict[str, object]], job_name: str, os_name: str) -> dict[str, object] | None:
    """Return the one job of ``jobs`` named ``job_name``, with or without matrix values; ``None`` for none or several.

    A matrix job is shown with the values of its ``matrix`` keys, as ``unit (ubuntu-latest)``; a value that an
    ``include`` entry adds to such a job is not part of the name. A name with several values, such as
    ``unit (ubuntu-latest, 120)``, is accepted all the same: both names match ``unit`` with the runner label
    ``ubuntu-latest``.
    """
    matches = [job for job in jobs if _is_match(str(job.get("name", "")), job_name, os_name)]
    return matches[0] if len(matches) == 1 else None


def classify_job(job: dict[str, object]) -> str:
    """Return ``REAL_SUCCESS``, ``GATE_SKIPPED``, ``UNVERIFIED`` or ``NOT_SUCCESSFUL`` for a job of an earlier run."""
    if job.get("status") != "completed" or job.get("conclusion") != "success":
        return NOT_SUCCESSFUL
    steps = job.get("steps")
    steps = [step for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []
    conclusions = {step.get("conclusion") for step in steps if step.get("name") == FULL_RUN_STEP}
    if not conclusions:
        return UNVERIFIED
    if conclusions == {"success"}:
        return REAL_SUCCESS
    if conclusions == {"skipped"}:
        return GATE_SKIPPED
    return NOT_SUCCESSFUL


def _untrusted(run: RunRef) -> ChainResult | None:
    """Return the result for a ``run`` that is not trusted whatever its jobs say, or ``None`` when its jobs count."""
    where = f"run {run.run_number}"
    if run.pull_request_count > 1:
        return ChainResult(None, RULE_SHARED_HEAD_BRANCH, f"{where} names {run.pull_request_count} pull requests")
    if run.status != "completed":
        return ChainResult(None, RULE_PREVIOUS_RUN_NOT_FINISHED, where)
    return None


def _visit(client: GateClient, run: RunRef, query: JobQuery) -> ChainResult | None:
    """Return the walk's result at ``run``, or ``None`` when the gate skipped the job there and the walk goes on."""
    where = f"run {run.run_number}"
    untrusted = _untrusted(run)
    if untrusted is not None:
        return untrusted
    job = match_job(list_jobs(client, run.run_id), query.job_name, query.os_name)
    if job is None:
        return ChainResult(None, RULE_PREVIOUS_RUN_MISSING, f"no single matching job in {where}")
    kind = classify_job(job)
    if kind == REAL_SUCCESS:
        return ChainResult(run.head_sha, RULE_PREVIOUS_RUN_SUCCEEDED, where)
    if kind == UNVERIFIED:
        return ChainResult(None, RULE_PREVIOUS_RUN_UNVERIFIED, f"no '{FULL_RUN_STEP}' step in {where}")
    if kind == NOT_SUCCESSFUL:
        outcome = job.get("conclusion") or job.get("status") or "unknown"
        return ChainResult(None, RULE_PREVIOUS_RUN_NOT_SUCCESSFUL, f"{outcome} in {where}")
    return None


def find_real_success(client: GateClient, query: JobQuery) -> ChainResult:
    """Walk from the newest earlier run back through gate-skipped jobs to the real success of the queried job.

    The newest earlier run must be on ``query.target_sha``. At most ``MAX_CHAIN_HOPS`` runs are visited. The result
    carries ``real_sha`` only under ``RULE_PREVIOUS_RUN_SUCCEEDED``; the caller still has to compare that commit with
    the new head.
    """
    runs = list_earlier_runs(client, query.workflow_file, query.branch, query.run_number)
    if not runs:
        return ChainResult(None, RULE_PREVIOUS_RUN_MISSING, "no earlier run of this workflow on the branch")
    if runs[0].head_sha != query.target_sha:
        return ChainResult(None, RULE_PREVIOUS_RUN_MISSING, f"run {runs[0].run_number} is on another commit")
    for run in runs[:MAX_CHAIN_HOPS]:
        result = _visit(client, run, query)
        if result is not None:
            return result
    if len(runs) > MAX_CHAIN_HOPS:
        return ChainResult(None, RULE_CHAIN_TOO_LONG, f"the newest {MAX_CHAIN_HOPS} runs were all skipped")
    return ChainResult(None, RULE_PREVIOUS_RUN_MISSING, "only gate-skipped runs on the branch")


@dataclass(frozen=True)
class MergeChain:
    merge: CommitRef
    post_shas: tuple[str, ...]


def find_merge_chain(
    client: GateClient, head_sha: str, diff_rule: Callable[[GateClient, str, str], tuple[Decision | None, int]]
) -> MergeChain | Decision | None:
    cursor = head_sha
    visited: list[str] = []
    for _ in range(MAX_CHAIN_HOPS):
        try:
            commit = commit_ref(client, cursor)
        except Exception:
            if cursor == head_sha:
                return None
            raise
        if len(commit.parents) == 2:
            return MergeChain(commit, tuple(visited))
        if len(commit.parents) > 2:
            return Decision(False, "merge-push-not-two-parents", f"{len(commit.parents)} parents")
        if not commit.parents:
            return None
        parent = commit.parents[0]
        blocked = diff_rule(client, parent, cursor)[0]
        if blocked is not None:
            return None if cursor == head_sha else blocked
        visited.append(cursor)
        cursor = parent
    return None


def verify_bookkeeping_chain(
    client: GateClient,
    ancestor_sha: str,
    tip_sha: str,
    diff_rule: Callable[[GateClient, str, str], tuple[Decision | None, int]],
) -> Decision | None:
    if ancestor_sha == tip_sha:
        return None
    cursor = tip_sha
    for _ in range(MAX_CHAIN_HOPS):
        commit = commit_ref(client, cursor)
        if len(commit.parents) != 1:
            return Decision(False, "force-push-or-diverged", f"{cursor[:12]} has {len(commit.parents)} parents")
        parent = commit.parents[0]
        blocked = diff_rule(client, parent, cursor)[0]
        if blocked is not None:
            return blocked
        cursor = parent
        if cursor == ancestor_sha:
            return None
    return Decision(False, RULE_CHAIN_TOO_LONG, f"more than {MAX_CHAIN_HOPS} commits since {ancestor_sha[:12]}")


def _is_cancelled_job(job: dict[str, object]) -> bool:
    return job.get("status") == "completed" and job.get("conclusion") == "cancelled"


def _visit_or_cancelled(client: GateClient, run: RunRef, query: JobQuery) -> tuple[ChainResult | None, bool]:
    untrusted = _untrusted(run)
    if untrusted is not None:
        return untrusted, False
    where = f"run {run.run_number}"
    job = match_job(list_jobs(client, run.run_id), query.job_name, query.os_name)
    if job is None:
        return ChainResult(None, RULE_PREVIOUS_RUN_MISSING, f"no single matching job in {where}"), False
    kind = classify_job(job)
    if kind == REAL_SUCCESS:
        return ChainResult(run.head_sha, RULE_PREVIOUS_RUN_SUCCEEDED, where), False
    if kind == GATE_SKIPPED:
        return None, False
    if kind == UNVERIFIED:
        return ChainResult(None, RULE_PREVIOUS_RUN_UNVERIFIED, f"no '{FULL_RUN_STEP}' step in {where}"), False
    outcome = job.get("conclusion") or job.get("status") or "unknown"
    return ChainResult(None, RULE_PREVIOUS_RUN_NOT_SUCCESSFUL, f"{outcome} in {where}"), _is_cancelled_job(job)


def find_real_success_for_merge_chain(
    client: GateClient, query: JobQuery, start_shas: Collection[str]
) -> tuple[ChainResult, bool]:
    runs = list_earlier_runs(client, query.workflow_file, query.branch, query.run_number)
    if not runs:
        return ChainResult(None, RULE_PREVIOUS_RUN_MISSING, "no earlier run of this workflow on the branch"), False
    if runs[0].head_sha not in set(start_shas) | {query.target_sha}:
        return ChainResult(None, RULE_PREVIOUS_RUN_MISSING, f"run {runs[0].run_number} is on another commit"), False
    walked_cancelled = False
    first_cancelled: ChainResult | None = None
    for run in runs[:MAX_CHAIN_HOPS]:
        result, cancelled = _visit_or_cancelled(client, run, query)
        if cancelled:
            walked_cancelled = True
            first_cancelled = first_cancelled or result
            continue
        if result is not None:
            return result, walked_cancelled
    if len(runs) > MAX_CHAIN_HOPS:
        return (
            ChainResult(None, RULE_CHAIN_TOO_LONG, f"the newest {MAX_CHAIN_HOPS} runs were all skipped"),
            walked_cancelled,
        )
    if first_cancelled is not None:
        return first_cancelled, True
    return ChainResult(None, RULE_PREVIOUS_RUN_MISSING, "only gate-skipped runs on the branch"), False
