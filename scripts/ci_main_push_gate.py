"""The push-to-``main`` route of the bookkeeping gate: skip a job whose tree was already tested on the merged head.

After a merge, ``main`` holds the same tree that the pull request's head commit held, and that head already ran the same
jobs. ``scripts.ci_bookkeeping_gate`` hands a push to ``refs/heads/main`` in one of ``GATED_WORKFLOWS`` to
``decide_main_push``; every other event keeps the gate's own rules. Only positive evidence skips. The rules are checked
in this order, and the first one that fails decides for a full run:

1. ``main-push-rerun-attempt``, ``main-push-incomplete-event``: the run is a first attempt and names the pushed commit,
   the workflow file, the job and its run number.
2. ``main-push-no-pull-request``, ``main-push-several-pull-requests``: exactly one pull request is associated with the
   pushed commit.
3. ``main-push-pull-request-not-merged``: that pull request was merged into ``main`` and its merge commit is the pushed
   commit (for a rebase merge that is the rebased tip, so it skips only when rule 4 also holds).
4. ``main-push-tree-mismatch``: the pushed commit's tree is the tree of the pull request's head commit.
5. ``main-push-head-job-missing``, ``main-push-head-job-not-finished``, ``main-push-head-job-not-successful``,
   ``main-push-head-job-unverified``: the newest pull-request run of this workflow on the head commit that holds this
   job (same name and runner label) and names no pull request but the merged one into ``main`` shows it completed with
   success and a ``Full run completed`` marker that succeeded (a real success) or was skipped (the job's own gates
   proved it on an ancestor).
6. ``main-push-head-skip-unproven``: when that job was skipped and the head commit is a merge commit, the walk of
   ``scripts.ci_bookkeeping_chain`` from that run on the head branch reaches a real success, and every path changed
   since it is a bookkeeping path. A merge-push skip (``merge-push-clean-main-merge``) proves the two parents, not the
   merged tree, so it is never evidence here and the push to ``main`` runs in full.

The one rule that skips is ``main-push-tested-tree``. No exception is caught here: the gate turns every exception into
``api-error`` and a full run. Standard library only and Python 3.9 syntax, so it runs on the runner's own ``python3``.
"""

from __future__ import annotations

from scripts.ci_bookkeeping_api import (
    GateClient,
    GateContext,
    PullRef,
    RunRef,
    commit_ref,
    commit_tree,
    list_head_runs,
    list_jobs,
    pulls_for_commit,
)
from scripts.ci_bookkeeping_chain import (
    FULL_RUN_STEP,
    GATE_SKIPPED,
    REAL_SUCCESS,
    UNVERIFIED,
    Decision,
    JobQuery,
    classify_job,
    find_real_success,
    match_job,
)
from scripts.ci_merge_push_gate import GateRules

MAIN_REF = "refs/heads/main"
MAIN_BRANCH = "main"
# Only these two workflows hold ``pull-requests: read``, which the commit-to-pull-request read needs; any other workflow
# keeps the gate's ``event-not-pull-request`` on a push.
GATED_WORKFLOWS = ("test-unit.yml", "test-stress.yml")
MAX_HEAD_RUNS = 5

RULE_RERUN_ATTEMPT = "main-push-rerun-attempt"
RULE_INCOMPLETE_EVENT = "main-push-incomplete-event"
RULE_NO_PULL_REQUEST = "main-push-no-pull-request"
RULE_SEVERAL_PULL_REQUESTS = "main-push-several-pull-requests"
RULE_PULL_REQUEST_NOT_MERGED = "main-push-pull-request-not-merged"
RULE_TREE_MISMATCH = "main-push-tree-mismatch"
RULE_HEAD_JOB_MISSING = "main-push-head-job-missing"
RULE_HEAD_JOB_NOT_FINISHED = "main-push-head-job-not-finished"
RULE_HEAD_JOB_NOT_SUCCESSFUL = "main-push-head-job-not-successful"
RULE_HEAD_JOB_UNVERIFIED = "main-push-head-job-unverified"
RULE_HEAD_SKIP_UNPROVEN = "main-push-head-skip-unproven"
RULE_TESTED_TREE = "main-push-tested-tree"
MAIN_PUSH_RUN_RULES = (
    RULE_RERUN_ATTEMPT,
    RULE_INCOMPLETE_EVENT,
    RULE_NO_PULL_REQUEST,
    RULE_SEVERAL_PULL_REQUESTS,
    RULE_PULL_REQUEST_NOT_MERGED,
    RULE_TREE_MISMATCH,
    RULE_HEAD_JOB_MISSING,
    RULE_HEAD_JOB_NOT_FINISHED,
    RULE_HEAD_JOB_NOT_SUCCESSFUL,
    RULE_HEAD_JOB_UNVERIFIED,
    RULE_HEAD_SKIP_UNPROVEN,
)
MAIN_PUSH_SKIP_RULES = (RULE_TESTED_TREE,)


def is_main_push(context: GateContext) -> bool:
    """Return true only for a push to ``main`` in one of ``GATED_WORKFLOWS``."""
    return context.event_name == "push" and context.ref == MAIN_REF and context.workflow_file in GATED_WORKFLOWS


def _event_check(context: GateContext) -> Decision | None:
    """Return the "run" decision for a re-run or an incomplete event, or ``None`` when the event is usable."""
    if context.run_attempt > 1:
        return Decision(False, RULE_RERUN_ATTEMPT, f"attempt {context.run_attempt}")
    named = (context.sha, context.workflow_file, context.job_name)
    if not all(named) or context.run_number <= 0 or context.run_attempt <= 0:
        return Decision(False, RULE_INCOMPLETE_EVENT)
    return None


def _merged_pull_request(context: GateContext, client: GateClient) -> Decision | PullRef:
    """Return the one pull request merged into ``main`` as the pushed commit, or the "run" decision."""
    pulls = pulls_for_commit(client, context.sha)
    if not pulls:
        return Decision(False, RULE_NO_PULL_REQUEST, context.sha[:12])
    if len(pulls) > 1:
        return Decision(False, RULE_SEVERAL_PULL_REQUESTS, f"{len(pulls)} pull requests")
    pull = pulls[0]
    where = f"#{pull.number}"
    if not pull.merged:
        return Decision(False, RULE_PULL_REQUEST_NOT_MERGED, f"{where} not merged")
    if pull.base_ref != MAIN_BRANCH:
        return Decision(False, RULE_PULL_REQUEST_NOT_MERGED, f"{where} base {pull.base_ref}")
    if pull.merge_commit_sha != context.sha:
        return Decision(False, RULE_PULL_REQUEST_NOT_MERGED, f"{where} merge commit {pull.merge_commit_sha[:12]}")
    return pull


def _tree_check(context: GateContext, client: GateClient, pull: PullRef) -> Decision | None:
    """Return the "run" decision when the pushed tree differs from the head's tree, else ``None``."""
    pushed, head = commit_tree(client, context.sha), commit_tree(client, pull.head_sha)
    if pushed != head:
        return Decision(False, RULE_TREE_MISMATCH, f"pushed tree {pushed[:12]}, head tree {head[:12]}")
    return None


def _ran_for(run: RunRef, pull: PullRef) -> bool:
    """Return false when the run names pull requests and none of them is ``pull`` against ``main``.

    The API may answer an empty list for a merged pull request's run, so an empty list does not exclude the run; a base
    left out of an entry is not held against it either.
    """
    if not run.pull_requests:
        return True
    return any(number == pull.number and base in ("", MAIN_BRANCH) for number, base in run.pull_requests)


def _head_job(context: GateContext, client: GateClient, pull: PullRef) -> tuple[int, dict[str, object] | None]:
    """Return the run number and the job of the newest head run of ``pull`` that holds exactly one matching job."""
    for run in list_head_runs(client, context.workflow_file, pull.head_sha)[:MAX_HEAD_RUNS]:
        if not _ran_for(run, pull):
            continue
        job = match_job(list_jobs(client, run.run_id), context.job_name, context.os_name)
        if job is not None:
            return run.run_number, job
    return 0, None


def _skip_check(context: GateContext, client: GateClient, pull: PullRef, run_number: int, rules: GateRules) -> str:
    """Return why a gate-skipped job on a merge head is not proven, or empty text when its tree is.

    A merge-push skip leaves a gate-skipped job on a tree no job ran, so a merge head needs the walk to a real success
    and bookkeeping-only changes since; the head run itself is the first run of the walk.
    """
    if len(commit_ref(client, pull.head_sha).parents) < 2:
        return ""
    if not pull.head_ref:
        return f"#{pull.number} names no head branch"
    query = JobQuery(
        context.workflow_file, pull.head_ref, context.job_name, context.os_name, run_number + 1, pull.head_sha
    )
    chain = find_real_success(client, query)
    if chain.real_sha is None:
        return f"{chain.rule} {chain.detail}"
    blocked = rules.diff_rule(client, chain.real_sha, pull.head_sha)[0] if chain.real_sha != pull.head_sha else None
    return f"{blocked.rule} {blocked.detail} since {chain.real_sha[:12]}" if blocked else ""


def _head_job_check(context: GateContext, client: GateClient, pull: PullRef, rules: GateRules) -> Decision:
    """Judge the job on the head commit; the decision skips only for a real success or a proven gate-skipped one."""
    run_number, job = _head_job(context, client, pull)
    if job is None:
        return Decision(False, RULE_HEAD_JOB_MISSING, f"no single matching job on {pull.head_sha[:12]}")
    where = f"run {run_number}"
    if job.get("status") != "completed":
        return Decision(False, RULE_HEAD_JOB_NOT_FINISHED, f"{job.get('status') or 'unknown'} in {where}")
    kind = classify_job(job)
    if kind == UNVERIFIED:
        return Decision(False, RULE_HEAD_JOB_UNVERIFIED, f"no '{FULL_RUN_STEP}' step in {where}")
    if kind not in (REAL_SUCCESS, GATE_SKIPPED):
        return Decision(False, RULE_HEAD_JOB_NOT_SUCCESSFUL, f"{job.get('conclusion') or 'unknown'} in {where}")
    unproven = _skip_check(context, client, pull, run_number, rules) if kind == GATE_SKIPPED else ""
    if unproven:
        return Decision(False, RULE_HEAD_SKIP_UNPROVEN, f"gate-skipped merge head in {where}: {unproven}")
    proof = "real success" if kind == REAL_SUCCESS else "gate-skipped"
    return Decision(True, RULE_TESTED_TREE, f"#{pull.number} head {pull.head_sha[:12]} {where} {proof}")


def decide_main_push(context: GateContext, client: GateClient, rules: GateRules) -> Decision:
    """Apply the main-push rules in order; only the head-job check can return "skip". Exceptions reach the caller.

    ``rules`` carries the gate's diff rule, which judges the changes since a real success behind a gate-skipped head.
    """
    blocked = _event_check(context)
    if blocked is not None:
        return blocked
    pull = _merged_pull_request(context, client)
    if isinstance(pull, Decision):
        return pull
    blocked = _tree_check(context, client, pull)
    if blocked is not None:
        return blocked
    return _head_job_check(context, client, pull, rules)
