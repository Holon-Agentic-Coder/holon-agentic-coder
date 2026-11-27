"""The Stage 4 review-resolve loop: iterate the ensemble and the resolver to consensus."""

from __future__ import annotations

import dataclasses
import functools
import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.agent_defaults import FALLBACK_AGENT
from sandbox_executor.budgets import StageTimer, load_budgets, resolve_review_ci_wait_seconds
from sandbox_executor.entrypoint.execution_workspace import workspace_mounted
from sandbox_executor.flow.calibrate_rerun import review_calibration_freshness
from sandbox_executor.flow.constants import CI_REGISTRATION_POLL_SECONDS
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.git_refs import _resolve_review_commit
from sandbox_executor.flow.github.adapter import GitHubAdapter
from sandbox_executor.flow.github.ci import _extract_failed_ci_check_names, _wait_for_ci_registration, probe_ci_checks
from sandbox_executor.flow.github.ci_wait import CiWaitPolicy, CiWaitResult
from sandbox_executor.flow.github.pr import check_pr_mergeability, create_or_update_pr, format_review_git_trailers
from sandbox_executor.flow.github.pr_head import PR_HEAD_TIMEOUT_SECONDS
from sandbox_executor.flow.github.review_post import post_pr_review_consensus
from sandbox_executor.flow.ledger_warning import _warn_ledger_failure
from sandbox_executor.flow.review import delta_review
from sandbox_executor.flow.review.adjudication import (
    Adjudication,
    _augment_pr_text_with_rejected,
    _filter_adjudicated_findings,
    record_nit_rejections,
)
from sandbox_executor.flow.review.approval_fingerprint import approval_fingerprint_of, approval_record
from sandbox_executor.flow.review.approval_gates import IterationGates, gates_summary, record_iteration_gates
from sandbox_executor.flow.review.carried_findings import (
    CarriedPass,
    carried_blocker,
    deserialize_carried,
    merge_findings,
    resolve_carried_first,
    serialize_open,
    undisposed,
)
from sandbox_executor.flow.review.ci_failure_routing import apply_last_classification
from sandbox_executor.flow.review.ci_inspection import CiInspectionInputs, inspect_ci
from sandbox_executor.flow.review.ci_noise_rerun import rerun_noise_once
from sandbox_executor.flow.review.ci_timing_finding import gh_timing_seams, timing_gate_finding
from sandbox_executor.flow.review.ensemble import (
    _dispatch_review_ensemble,
    _ensemble_iteration,
    _resolve_reviewed_target,
    _semantic_pr_text,
)
from sandbox_executor.flow.review.ids import build_review_id
from sandbox_executor.flow.review.ledger_commit import _advance_past_ledger_commit, _commit_pending_ledger_rows
from sandbox_executor.flow.review.local_test_finding import local_test_failure_finding
from sandbox_executor.flow.review.local_tests import _ReviewTestGate
from sandbox_executor.flow.review.posting import (
    PostFacts,
    PostRequest,
    decide_post,
    is_terminal_verdict,
    log_skipped_post,
    post_review_consensus,
)
from sandbox_executor.flow.review.proxy_env import review_proxy_env
from sandbox_executor.flow.review.records import (
    _bounded_error_context,
    _record_infrastructure_findings,
    _record_reviewer_rows,
    _refused_review_result,
    _reviewer_finding_ids,
    _reviewer_summary,
    _worst_severity,
    record_review_action,
)
from sandbox_executor.flow.review.rejected_attempt_record import _record_rejected_attempt
from sandbox_executor.flow.review.resolver import (
    DispositionRecorder,
    ResolverInput,
    _invoke_resolver_pass,
    _lookup_introducing_action_id,
)
from sandbox_executor.flow.review.sync_points import sync_iteration_mergeability
from sandbox_executor.flow.review.verdict import (
    _finding_outcome_blockers,
    _loop_status,
    consensus_blockers,
    decide_returned_consensus,
    not_approved_verdicts,
    render_final_verdicts,
)
from sandbox_executor.flow.review.workspace import reviewers_sandboxed
from sandbox_executor.knowledge import append_action_event, compute_context_fingerprint
from sandbox_executor.ledger import (
    LedgerRewriteError,
    generate_action_id,
    parse_commit_trailers,
    try_append_ledger_line,
)
from sandbox_executor.resolver_evidence import OPEN
from sandbox_executor.review_accounting import accounting_of
from sandbox_executor.review_ensemble import (
    ConsensusVerdict,
    ReviewerResult,
    ReviewerStatus,
    _any_reviewer_objects,
    run_ensemble,
)
from sandbox_executor.review_finding_outcomes import with_commit
from sandbox_executor.review_report import (
    FindingDisposition,
    FindingRow,
    ci_failure_finding,
    collect_review_data,
    deserialize_finding_dispositions,
    finding_signature,
    resolve_report_title,
    review_data_summary,
    serialize_finding_disposition,
)
from sandbox_executor.review_target import DiffVerdict, ReviewTarget, classify_review_diff

logger = logging.getLogger(__name__)


def run_review_loop(  # noqa: C901, PLR0912, PLR0913, PLR0915
    context: FlowContext,
    target: str | None = None,
    max_iterations: int = 25,
    ensemble_size: int = 3,
    auto_pr: bool = True,
    dry_run: bool = False,
    ci_registration_timeout: float | None = None,
    ci_poll_interval: float = CI_REGISTRATION_POLL_SECONDS,
    ci_wait_budget: float | None = None,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    *,
    run_ensemble_fn: Callable[..., list[ReviewerResult]] | None = None,
    run_resolver_fn: Callable[..., Any] | None = None,
    skip_post: bool = False,
    first_iteration: int = 1,
    ledger_file: str | None = None,
    github: GitHubAdapter | None = None,
) -> dict[str, Any]:
    """Execute the native Stage 4 review loop with the real reviewer ensemble.

    Each iteration inspects PR mergeability and launches ``ensemble_size`` independent reviewer agent turns in parallel
    with GitHub CI. Reviewers evaluate the real diff between the base and the resolved head SHA. If any reviewer
    requests changes or reports open findings, pending CI checks are bypassed and findings are immediately dispatched to
    the resolver. Only when all reviewers approve cleanly with zero open findings and mergeability is clean does the
    loop wait for CI checks to settle as a post-review safety gate before calling ``decide_consensus()``. Reviewers run
    only inside a sandbox: on the host the loop refuses before any PR, iteration or post. It enforces the
    human-only PR merge rule by completing with approved consensus without merging to main.

    Args:
        context: Active FlowContext carrying repository and pipeline state.
        target: Target execution branch name, PR number, or PR URL.
        max_iterations: Maximum iteration budget before forced termination.
        ensemble_size: Number of independent reviewer agent turns per iteration.
        auto_pr: Whether to create PR automatically if target is only a branch.
        dry_run: If True, evaluate without applying git commits or mutations.
        ci_registration_timeout: Seconds to wait for CI checks to register when Stage 3 recorded no test
            result (default: ``HOLON_CI_REGISTRATION_TIMEOUT``, then 180; ``0`` disables the wait).
        ci_poll_interval: First interval between CI probes; pending-CI polls double from it.
        ci_wait_budget: Seconds to wait for pending CI, outside the iteration budget (default: env, then 1800).
        clock: Monotonic clock for the per-iteration budget and the CI waits (default: time.monotonic).
        sleep: Sleep function used by the CI waits (default: time.sleep); injectable for tests.
        run_ensemble_fn: Reviewer ensemble seam (default: ``review_ensemble.run_ensemble``); tests inject a fake.
        skip_post: If True, never post the consensus to GitHub (the engine's container dispatch posts on the host).
        first_iteration: Label of the first pass (default 1). It is only a label: the ledger ``review_iteration``
            fields, finding IDs, trailers and the posted ``Iteration {n}`` heading carry it, while ``max_iterations``
            alone caps how many passes run. The engine passes its own continued count here.
        ledger_file: Where this pass appends ledger rows (default: the worktree's holon-knowledge ledger).
        github: The stage's GitHub adapter, used for every PR, CI, mergeability and posting call (default: one built
            here over the worktree with the token-reduction proxy environment); tests inject a ``FakeGitHub``.

    The empty-diff guard runs for every target whatever ``auto_pr`` is: an empty diff is rejected and an
    unverifiable one fails, before any PR, iteration or post. A dry run never measures anything, so it reports
    ``not_measured`` and is never approved; it never dispatches a reviewer. A consensus is posted only
    on a terminal exit (approval, or the label budget exhausted) when at least one reviewer completed and no
    infrastructure step failed (see ``decide_post``); one that cannot be posted is ``post_failed``, never approved.

    Returns:
        dict[str, Any]: Review loop outcome dictionary.
    """
    clock = clock or time.monotonic
    ensemble_fn = run_ensemble_fn or run_ensemble
    sleep = sleep or time.sleep
    review_target = str(target or context.execution_branch or context.intent_branch or "HEAD").strip()
    budget = max(1, max_iterations or context.max_review_iterations or 25)
    # Labels run from first_label to last_label; the counter below always holds the label of the latest pass.
    first_label = max(1, first_iteration)
    last_label = first_label + budget - 1
    repo_dir = context.target_worktree
    target_branch = context.intent_data.get("target_branch", "main")
    effective_dry_run = bool(dry_run or context.dry_run)

    proxy_env = dict(github.proxy_env or {}) if github is not None else review_proxy_env(context)
    github = github or GitHubAdapter(repo_dir, proxy_env)

    pr_head = functools.partial(github.pr_head_sha, timeout=PR_HEAD_TIMEOUT_SECONDS)
    test_gate = _ReviewTestGate(context, repo_dir, target=review_target, pr_head=pr_head, proxy_env=proxy_env)
    _, tests_failed = test_gate.stage_3_evidence
    iteration = first_label - 1
    consensus_reached = False
    last_ci_status: dict[str, Any] = {}
    last_trailers_str = ""
    last_trailers_dict: dict[str, Any] = {}
    last_mergeability: dict[str, Any] = {}
    is_dirty = False
    missing_ref = False
    tests_not_measured = False
    mergeability_note: str | None = None
    finding_ids: list[str] = []
    severity = "clean"
    reviewers: list[ReviewerResult] = []
    ensemble_verdict: ConsensusVerdict | None = None
    blockers: list[str] = []
    # Why the review failed before or outside the ensemble (sandbox refusal, target, diff); never posted.
    infra_error: str | None = None
    resolver_error: str | None = None  # Why a resolver pass failed; the loop halts on it before any adjudication.
    resolved_target: ReviewTarget | None = None

    exec_file = ledger_file or os.path.join(repo_dir, "holon-knowledge", "ledger", "executions.jsonl")
    suppressed_ledger_records = 0  # Rows a dry run would otherwise have written; reported once at the end.

    # Automate semantic PR generation if auto_pr is requested
    pr_res: dict[str, Any] = {}
    pr_allowed = True
    # Why the empty-diff guard refused the PR; reported as the loop's error instead of a test outcome.
    pr_refusal_error: str | None = None
    if effective_dry_run:
        context.log(f"[Review Loop] Dry run: the diff of '{review_target}' is not measured.")
    else:
        # Empty-diff guard, for every target and whatever auto_pr is: nothing to review is never approved.
        diff_verdict, diff_detail = classify_review_diff(repo_dir, review_target, target_branch)
        if diff_verdict is DiffVerdict.EMPTY:
            err_msg = f"Refusing to create or update a PR for '{review_target}': {diff_detail}."
            context.log(f"[Review Loop] {err_msg}")
            return _refused_review_result(review_target, first_label - 1, last_label, err_msg)
        if diff_verdict is DiffVerdict.UNVERIFIED:
            pr_refusal_error = (
                f"Refusing to create or update a PR for '{review_target}': "
                f"could not verify the execution diff ({diff_detail})."
            )
            context.log(f"[Review Loop] Skipping PR creation: could not verify the execution diff ({diff_detail}).")
            pr_res = {"success": False, "skipped": True, "reason": "diff_unverified"}
            pr_allowed = False
        else:
            context.log(f"[Review Loop] Empty-diff guard passed for '{review_target}': {diff_detail}.")
        if pr_refusal_error is None:
            resolved_target, infra_error = _resolve_reviewed_target(repo_dir, review_target)
        if infra_error:
            # Fail closed before any PR, iteration, reviewer row or post.
            context.log(f"[Review Loop] {infra_error}.")
            pr_allowed = False
            pr_res = {"success": False, "skipped": True, "reason": "review_refused"}
    pr_text = _semantic_pr_text(context)
    if pr_allowed and auto_pr and not effective_dry_run and github.available:
        try:
            pr_res = create_or_update_pr(
                repo_dir=repo_dir,
                branch=review_target,
                target_branch=target_branch,
                title=pr_text[0],
                body=pr_text[1],
                proxy_env=proxy_env,
                github=github,
            )
            if pr_res.get("success"):
                context.log(f"[Review Loop] PR {pr_res.get('action')} successful for '{review_target}'.")
        except Exception as exc:
            logger.debug("Non-fatal PR create/update in review loop: %s", exc)

    iteration_budget_seconds = load_budgets(os.environ).stage_seconds("review_iteration")
    ci_wait_budget_seconds = resolve_review_ci_wait_seconds(os.environ) if ci_wait_budget is None else ci_wait_budget
    probe_ci = functools.partial(probe_ci_checks, repo_dir, review_target, proxy_env=proxy_env, github=github)
    ci_policy = CiWaitPolicy(ci_wait_budget_seconds, ci_poll_interval, clock=clock, sleep=sleep)
    ci_wait: CiWaitResult | None = None  # The first wait that spent its budget; recorded once, never waited out again.
    ci_seams = CiInspectionInputs(
        repo_dir,
        review_target,
        proxy_env,
        github,
        test_gate,
        ci_policy,
        probe_ci,
        _wait_for_ci_registration,
        pr_allowed,
        ci_registration_timeout,
        ci_poll_interval,
        clock,
        sleep,
    )
    iteration_summaries: list[str] = []
    budget_breach: dict[str, Any] | None = None
    resolver_enabled = (run_resolver_fn is not None) or (not reviewers_sandboxed())
    noise_rerun_enabled = (run_ensemble_fn is not None) or (not reviewers_sandboxed())
    finding_action_ids: dict[str, str] = {}
    recorded_disposition_keys: set[tuple[str, str, int | None, str]] = set()
    rejected_history: dict[tuple[str, str, int | None, str], FindingDisposition] = {}  # Rejected nits only.
    all_dispositions: list[FindingDisposition] = []
    pending_dispositions: tuple[FindingDisposition, ...] = ()
    # The resolver's latest open disposition per finding, kept so its audit text reaches the end-of-loop ACT-RES row.
    open_dispositions: dict[tuple[str, str, int | None, str], FindingDisposition] = {}
    prev_finding_sigs: frozenset[tuple[str, str, int | None, str]] = frozenset()
    prev_head_sha = ""
    unchanged_streak = 0
    no_convergence = False
    iter_findings: list[FindingRow] = []
    recorder = DispositionRecorder(
        context,
        exec_file,
        review_target,
        finding_action_ids,
        recorded_disposition_keys,
        all_dispositions,
        effective_dry_run=effective_dry_run,
    )
    # Findings a halted pass left open go to the resolver before any reviewer; a failed pass starts no iteration.
    carried_rows, carried_ids = deserialize_carried(context.carried_review_findings)
    finding_action_ids.update(carried_ids)
    carried_block = ""  # Why approval was refused while a carried finding had no disposition.
    if carried_rows and resolver_enabled and resolved_target is not None and not effective_dry_run:
        carried_pass = CarriedPass(context, resolved_target, max(1, first_label - 1), run_resolver_fn, recorder)
        delta_review.record_addressed_findings(context, carried_rows)
        carried_outcome, carried_rows, pending_dispositions = resolve_carried_first(
            carried_rows, finding_action_ids, carried_pass
        )
        resolver_error = _bounded_error_context(carried_outcome.error) if carried_outcome.error else None
        if carried_outcome.error:
            _record_rejected_attempt(context, exec_file, review_target, carried_pass.iteration, carried_outcome)
        if carried_outcome.pushed and carried_outcome.head_sha:
            resolved_target = dataclasses.replace(resolved_target, head_sha=carried_outcome.head_sha)

    if pr_refusal_error:
        # Fail closed: a PR the guard refused is never reviewed, so no evidence can turn the refusal into approval.
        context.log("[Review Loop] Halting review loop without consensus: the empty-diff guard refused the PR.")

    while pr_refusal_error is None and infra_error is None and resolver_error is None and iteration < last_label:
        # The previous iteration's rows; the first boundary precedes any numbered iteration.
        boundary = "" if iteration >= 1 else f"before iteration {iteration + 1}"
        last_commit = None if effective_dry_run else _commit_pending_ledger_rows(context, iteration, boundary)
        resolved_target, prev_head_sha = _advance_past_ledger_commit(resolved_target, prev_head_sha, last_commit)
        if last_commit is not None:
            test_gate.advance_ledger_head(last_commit.parent, last_commit.sha)
        iteration += 1
        context.log(f"[Review Loop] Starting review iteration {iteration}/{last_label} for target '{review_target}'.")
        iteration_timer = StageTimer("review", iteration_budget_seconds, clock=clock)

        # 1. Mergeability inspection and pre-iteration target sync
        mp = functools.partial(check_pr_mergeability, repo_dir, review_target, target_branch, proxy_env, github=github)
        resolved_target, last_mergeability, is_dirty, sync_err = sync_iteration_mergeability(
            context, resolved_target, target_branch, iteration, mp
        )
        infra_error = infra_error or sync_err
        missing_ref = last_mergeability.get("merge_state_status") == "MISSING_REF"
        mergeability_note = None
        if missing_ref:
            unresolved_ref = (last_mergeability.get("diagnostics") or {}).get("missing_ref") or review_target
            mergeability_note = (
                f"cannot evaluate mergeability for ref {unresolved_ref} against {target_branch}: "
                "ref could not be resolved to a commit."
            )
            context.log(f"[Review Loop] {mergeability_note}")

        # 2. Reviewer ensemble execution on the resolved head (a dry run never dispatches reviewers)
        reviewers, ensemble_verdict, container_verdict = [], None, None
        if not effective_dry_run and resolved_target is not None and infra_error is None:
            iter_pr_text = _augment_pr_text_with_rejected(pr_text, tuple(rejected_history.values()))
            delta_review.begin_review_pass(context, repo_dir, resolved_target.head_sha, iteration)
            if run_ensemble_fn is not None or reviewers_sandboxed():
                reviewers, infra_error = _ensemble_iteration(
                    context, resolved_target, target_branch, iteration, iter_pr_text, ensemble_fn, ensemble_size
                )
            else:
                reviewers, container_verdict, infra_error = _dispatch_review_ensemble(
                    context, resolved_target, iteration, ensemble_size
                )
        # The returned reviewers stay the single source of the verdict; adjudication only narrows the resolver input.
        adjudication: Adjudication = _filter_adjudicated_findings(reviewers, rejected_history)
        blockers = consensus_blockers(reviewers, container_verdict, adjudication)
        for blocker in blockers:
            context.log(f"[Review Loop] Consensus blocked at iteration {iteration}: {blocker}.")
        reviewer_findings = collect_review_data(reviewers).findings
        reviewer_objected = _any_reviewer_objects(reviewers) or bool(reviewer_findings) or bool(blockers)
        # why: added after reviewer_objected so the CI fast path and dry runs never see these blockers.
        outcome_dispositions = (*all_dispositions, *pending_dispositions, *rejected_history.values())
        blockers += _finding_outcome_blockers(
            reviewers, merge_findings(iter_findings, carried_rows), outcome_dispositions
        )

        # 3. CI status inspection: post-review gate or fast-path probe (see ci_inspection.py)
        ci_inputs = dataclasses.replace(
            ci_seams,
            iteration_timer=iteration_timer,
            reviewer_objected=reviewer_objected,
            is_dirty=is_dirty,
            missing_ref=missing_ref,
            effective_dry_run=effective_dry_run,
            infra_failed=infra_error is not None,
            ci_wait=ci_wait,
        )
        ci = inspect_ci(ci_inputs, context)  # A noise failure is rerun once here, keeping the diff approval.
        if noise_rerun_enabled:  # The host reruns: a container's rerun budget and routing rows never reach it.
            ci = rerun_noise_once(context, ci_inputs, ci, resolved_target.head_sha if resolved_target else None)
        last_ci_status, ci_wait, tests_failed, ci_finding = ci.ci_status, ci.ci_wait, ci.tests_failed, ci.ci_finding
        tests_not_measured |= ci.tests_not_measured
        mergeable = None if missing_ref else not is_dirty
        if ci.decides:
            ensemble_verdict = decide_returned_consensus(reviewers, ensemble_size, mergeable, ci.ci_green, blockers)
        if not effective_dry_run and infra_error is None and resolved_target is not None and resolved_target.head_sha:
            gate_base = resolved_target.base_branch or target_branch
            fingerprint_of = functools.partial(approval_fingerprint_of, repo_dir, base_branch=gate_base)
            gate_facts = (reviewers, ensemble_size, mergeable, blockers, ci.ci_green, last_ci_status, gate_base)
            scoped = delta_review.current_decision(context).scoped or bool(context.review_scope_paths)
            gate_mode = "scoped" if scoped else "full"  # A conflict re-review saw only the scoped paths.
            facts = IterationGates(
                resolved_target.head_sha, iteration, *gate_facts, fingerprint_of=fingerprint_of, mode=gate_mode
            )
            record_iteration_gates(context, facts, ensemble_verdict)  # Both gates; the verdict stays the conjunction.
            apply_last_classification(context, resolved_target.head_sha)
            delta_review.end_review_pass(context, resolved_target.head_sha, reviewers, ensemble_size)

        # 4. Findings: mergeability and CI infrastructure ids, then every reviewer finding
        finding_ids = [
            finding_id
            for finding_id, present in (
                (f"FIND-DIRTY-{iteration:03d}", is_dirty),
                (f"FIND-REF-{iteration:03d}", missing_ref),
                (f"FIND-CI-{iteration:03d}", ci_finding),
            )
            if present
        ]
        infra_ids = list(finding_ids)
        severity = _worst_severity(
            "critical" if is_dirty else "clean",
            "important" if (missing_ref or ci_finding) else "clean",
            *(finding.severity for result in reviewers for finding in result.findings),
        )
        for result in reviewers:
            finding_ids.extend(_reviewer_finding_ids(result, iteration))

        # 5. Git trailer generation
        last_trailers_str = format_review_git_trailers("pr_review", iteration, finding_ids, severity)
        last_trailers_dict = parse_commit_trailers(last_trailers_str)

        findings_context = f"Findings detected: {finding_ids}"
        if mergeability_note:
            findings_context += f"; {mergeability_note}"
        if infra_error:
            findings_context += f"; {infra_error}"

        # 6. Ledger rows: the iteration evaluation, one row per reviewer, one row per finding
        action_record = {
            "action_id": generate_action_id("ACT-REV"),
            "execution_id": f"REV-ITR-{iteration}-{int(time.time())}",
            "action": f"Review Loop iteration {iteration} evaluation for {review_target}",
            "category": "code",
            "target": review_target,
            "outcome": "success" if ensemble_verdict is ConsensusVerdict.APPROVED else "failure",
            "entry_type": "action",
            "action_origin": "pr_review",
            "review_severity": severity if severity in ("critical", "important", "nit") else None,
            "review_iteration": iteration,
            "review_finding_ids": finding_ids if finding_ids else None,
            "labels": ["review", "review-loop", f"iteration-{iteration}"],
            "error_context": _bounded_error_context(findings_context) if (finding_ids or infra_error) else None,
            "created_at": datetime.now(UTC).isoformat(),
        }
        if effective_dry_run:
            suppressed_ledger_records += 1 + len(infra_ids)
        else:
            record_review_action(context, exec_file, action_record)
            _record_reviewer_rows(
                context,
                review_target,
                iteration,
                reviewers,
                finding_action_ids=finding_action_ids,
            )
            _record_infrastructure_findings(
                context,
                exec_file,
                review_target,
                iteration,
                infra_ids,
                _bounded_error_context(findings_context),
                finding_action_ids=finding_action_ids,
            )

        iteration_timer.stop()
        iteration_breached = iteration_timer.breached()
        iteration_line = f"iteration {iteration} " + iteration_timer.summary("failed" if iteration_breached else "ok")
        iteration_summaries.append(iteration_line)
        context.log(f"[Review Loop] {iteration_line}")

        # 7. A ref that does not resolve will not resolve on the next pass either: fail closed now
        # rather than burn the remaining iteration budget.
        if missing_ref:
            context.log("[Review Loop] Halting review loop without consensus: mergeability could not be evaluated.")
            break
        # Unmeasured tests stay unmeasured on the next pass too (CI had its registration window): stop now.
        if tests_not_measured:
            context.log("[Review Loop] Halting review loop without consensus: test results not measured.")
            break

        # 8. Termination and Resolver Pass
        # A carried fix applied but not yet pushed is recorded with the pending dispositions; it blocks nothing.
        if ensemble_verdict is ConsensusVerdict.APPROVED and undisposed(carried_rows, pending_dispositions):
            carried_block = carried_blocker(undisposed(carried_rows, pending_dispositions))
            blockers.append(carried_block)
            ensemble_verdict = ConsensusVerdict.CHANGES_REQUESTED
            context.log(f"[Review Loop] Consensus blocked at iteration {iteration}: {carried_block}.")
            break
        if ensemble_verdict is ConsensusVerdict.APPROVED:
            consensus_reached, context.review_scope_paths = True, []
            context.log(f"[Review Loop] Consensus approval reached at iteration {iteration}.")
            break
        if iteration_breached:
            breach = iteration_timer.breach()
            budget_breach = {
                "stage": "review",
                "budget_name": "review_iteration",
                "budget_seconds": breach.budget_seconds,
                "elapsed_seconds": round(breach.elapsed_seconds, 3),
                "iteration": iteration,
            }
            context.log(f"[Review Loop] Stopping: iteration {iteration} exceeded its {breach.describe()}.")
            break
        if effective_dry_run:
            context.log("[Review Loop] Dry run: no reviewer was dispatched; stopping after one iteration.")
            break
        if ensemble_verdict is not ConsensusVerdict.CHANGES_REQUESTED:
            context.log(
                f"[Review Loop] Halting review loop at iteration {iteration}: verdict "
                f"{ensemble_verdict or ConsensusVerdict.FAILED}."
            )
            break

        iter_findings = list(adjudication.actionable)
        if ci_finding:
            failed_names = _extract_failed_ci_check_names(last_ci_status, tests_failed)
            ci_row = (
                timing_gate_finding(iteration, last_ci_status, failed_names, gh_timing_seams(repo_dir, proxy_env or {}))
                or local_test_failure_finding(iteration, last_ci_status, failed_names)
                or ci_failure_finding(iteration, failed_names)
            )
            iter_findings.append(ci_row)
            if finding_action_ids is not None:
                act_id = finding_action_ids.get(ci_row.id) or generate_action_id("ACT-FIND")
                finding_action_ids[ci_row.id] = act_id
                sig_key = str(finding_signature(ci_row.severity, ci_row.path, ci_row.line, ci_row.summary))
                finding_action_ids.setdefault(sig_key, act_id)

        if not resolver_enabled or not iter_findings or is_dirty or resolved_target is None:
            context.log(
                f"[Review Loop] Halting review loop at iteration {iteration}: verdict "
                f"{ensemble_verdict} (no resolver pass applicable"
                + (f": {'; '.join(blockers)})." if blockers else ").")
            )
            break

        # 3-iteration unchanged-finding no-convergence guard
        current_sigs = frozenset(finding_signature(f.severity, f.path, f.line, f.summary) for f in iter_findings)
        current_sha = (resolved_target.head_sha if resolved_target else "") or ""
        if current_sigs and current_sigs == prev_finding_sigs and current_sha == prev_head_sha:
            unchanged_streak += 1
        else:
            unchanged_streak = 1
            prev_finding_sigs = current_sigs
            prev_head_sha = current_sha

        if unchanged_streak >= 3:
            no_convergence = True
            nc_dispositions = tuple(
                FindingDisposition(
                    id=row.id,
                    severity=row.severity,
                    path=row.path,
                    line=row.line,
                    summary=row.summary,
                    recommendation=row.recommendation,
                    disposition="rejected",
                    rejection_reason="no-convergence",
                    introduced_by_action_id=_lookup_introducing_action_id(row, finding_action_ids),
                )
                for row in iter_findings
            )
            suppressed_ledger_records += recorder.record(iteration, nc_dispositions)
            pending_dispositions = ()
            context.log(
                f"[Review Loop] Halting review loop at iteration {iteration}: finding set unchanged across 3 "
                "consecutive iterations with no diff change (rejected: no-convergence)."
            )
            break

        delta_review.record_addressed_findings(context, merge_findings(iter_findings, carried_rows))
        resolver_outcome = _invoke_resolver_pass(
            context,
            resolved_target,
            iteration,
            ResolverInput(
                findings=merge_findings(iter_findings, carried_rows),
                action_ids=finding_action_ids,
                rejected_findings=tuple(rejected_history.values()),  # Rejected nits only.
            ),
            run_resolver_fn,
        )
        if resolver_outcome.error:
            # Halt before adjudication: a failed pass's fallback dispositions are no rejections to filter on, and a
            # head it committed but did not push is never re-reviewed. Open findings are recorded once after the loop.
            resolver_error = _bounded_error_context(resolver_outcome.error)
            if not effective_dry_run:
                _record_rejected_attempt(context, exec_file, review_target, iteration, resolver_outcome)
            pending_dispositions = ()
            context.log(
                f"[Review Loop] Halting review loop at iteration {iteration}: resolver failed: {resolver_error}"
            )
            break
        rejected_now = [d for d in resolver_outcome.dispositions if d.disposition == "rejected"]
        applied_now = [d for d in resolver_outcome.dispositions if d.disposition == "applied"]
        open_dispositions.update(
            (finding_signature(d.severity, d.path, d.line, d.summary), d)
            for d in resolver_outcome.dispositions
            if d.disposition == OPEN
        )
        if rejected_now:
            suppressed_ledger_records += recorder.record(iteration, rejected_now)
            record_nit_rejections(rejected_history, rejected_now)
            test_gate.accept_resolver_evidence(current_sha, rejected_now)

        head_changed = bool(resolver_outcome.pushed)
        if applied_now or (head_changed and pending_dispositions):
            if head_changed or iteration >= last_label:
                commit_sha = resolver_outcome.head_sha if head_changed else None
                to_record = with_commit((*pending_dispositions, *applied_now), commit_sha)
                if to_record:
                    suppressed_ledger_records += recorder.record(iteration, to_record)
                pending_dispositions = ()
            else:
                pending_dispositions = (*pending_dispositions, *applied_now)
        carried_rows = undisposed(carried_rows, all_dispositions)

        if head_changed and resolved_target is not None:
            new_sha = resolver_outcome.head_sha or f"{current_sha or '0' * 32}-iter{iteration}"
            resolved_target = dataclasses.replace(resolved_target, head_sha=new_sha)
            ci_wait = None  # A spent wait belongs to the old head; the new head's pending checks get their own wait.
            context.log(
                f"[Review Loop] Resolver pushed new head '{new_sha}' at iteration {iteration}; proceeding to re-review."
            )

    if resolver_enabled and not effective_dry_run and pending_dispositions:
        suppressed_ledger_records += recorder.record(iteration, pending_dispositions)
    remaining_findings = [
        row
        for row in merge_findings(iter_findings or collect_review_data(reviewers).findings, carried_rows)
        if finding_signature(row.severity, row.path, row.line, row.summary) not in recorded_disposition_keys
    ]
    # A failed resolver pass decided nothing: its findings stay open, unrecorded, and are returned to be carried.
    if resolver_enabled and not effective_dry_run and resolver_error is None and remaining_findings:
        fallback_dispositions = [
            open_dispositions.get(finding_signature(d.severity, d.path, d.line, d.summary), d)
            for d in deserialize_finding_dispositions([], remaining_findings, finding_action_ids)
        ]
        suppressed_ledger_records += recorder.record(iteration, fallback_dispositions)

    if not consensus_reached and pr_refusal_error is None and infra_error is None and iteration >= last_label:
        context.log(f"[Review Loop] Iteration budget ({budget}) exhausted without consensus.")

    # A dry run measures nothing and dispatches no reviewer, so a clean simulation is reported as not measured.
    passes = iteration - first_label + 1
    not_measured = tests_not_measured or (effective_dry_run and passes > 0 and not finding_ids)
    consensus_status = _loop_status(
        consensus_reached,
        ensemble_verdict,
        bool(pr_refusal_error or infra_error or resolver_error),
        is_dirty,
        not_measured,
    )
    approval_score = 1.0 if consensus_reached else 0.0

    current_commit_sha = (resolved_target.head_sha if resolved_target else "") or "HEAD"  # The SHA reviewers saw.
    try:
        if current_commit_sha == "HEAD":
            current_commit_sha = _resolve_review_commit(
                repo_dir, review_target, fetch=not (effective_dry_run or workspace_mounted())
            )
    except Exception as exc:
        logger.debug("Could not resolve the reviewed commit for %s: %s", review_target, exc)

    review_data = dataclasses.replace(collect_review_data(reviewers), dispositions=tuple(all_dispositions))
    base_branch = (resolved_target.base_branch if resolved_target else None) or target_branch
    fingerprint = approval_fingerprint_of(repo_dir, current_commit_sha, base_branch) if consensus_reached else None
    consensus_data = {
        "approved": consensus_reached,
        "status": consensus_status,
        "iterations": iteration,
        # Label units: the last pass label pairs with the label budget, never with the remaining pass limit.
        "max_review_iterations": last_label,
        "ci_checks": last_ci_status,
        "ci_wait": ci_wait.as_record() if ci_wait else None,
        "mergeability": last_mergeability,
        "mergeability_note": mergeability_note,
        "trailers": last_trailers_str,
        "findings": finding_ids,
        "severity": severity,
        "agent": context.agent or FALLBACK_AGENT,
        "model": context.model or "inherit",
        "ensemble_size": ensemble_size,
        "ensemble_verdict": str(ensemble_verdict) if ensemble_verdict else None,
        "reviewers": [_reviewer_summary(result) for result in reviewers],
        "review_data": review_data_summary(review_data, all_dispositions),
        "dispositions": [serialize_finding_disposition(d) for d in all_dispositions],
        "no_convergence": no_convergence,
        "base_branch": base_branch,
        "intent_data": context.intent_data,
        "target_commit": current_commit_sha,
        "approval_fingerprint": fingerprint,
        "pr_url": pr_res.get("pr_url"),
        "pr_number": pr_res.get("pr_number"),
    }

    # Post only a terminal review: approval, or the label budget exhausted. Other exits keep the report local.
    posting_res: dict[str, Any] = {}
    post_error: str | None = None
    decision = decide_post(
        PostFacts(
            dry_run=effective_dry_run,
            skip_post=skip_post,
            failed=bool(pr_refusal_error or infra_error),
            reviewer_completed=any(result.status is ReviewerStatus.COMPLETED for result in reviewers),
            consensus_reached=consensus_reached,
            halted_early=bool(
                resolver_error or no_convergence or budget_breach or missing_ref or tests_not_measured or carried_block
            ),
            iteration=iteration,
            last_label=last_label,
            verdict_terminal=is_terminal_verdict(ensemble_verdict, reviewers),
        )
    )
    if decision.count_suppressed:
        suppressed_ledger_records += 1
    if decision.post:
        post_error, posting_res = post_review_consensus(
            functools.partial(post_pr_review_consensus, github=github),
            context,
            exec_file,
            PostRequest(
                repo_dir=repo_dir,
                review_target=review_target,
                consensus_data=consensus_data,
                proxy_env=proxy_env,
                reviewers=reviewers,
                title=resolve_report_title(context.intent_data, resolved_target, review_target),
                iteration=iteration,
                mergeability_probe=functools.partial(check_pr_mergeability, github=github),
            ),
        )
        if post_error:  # A conflict-gate block is not a posting failure: the approval is re-earned on the merged head.
            status = "conflict_gate" if "conflict_gate" in posting_res else "post_failed"
            consensus_reached, consensus_status, approval_score = False, status, 0.0
    else:
        log_skipped_post(context, review_target, decision)

    # Every re-run and resolver push flows through here: a stale calibration report posts its signal (and, in-flow,
    # reruns Stage 5) before the loop reports; calibrate_rerun.py owns the logic and never raises.
    calibration_freshness = review_calibration_freshness(context, str(pr_res.get("pr_number") or "") or None)

    halt_reasons: list[str] = []
    if pr_refusal_error or infra_error:
        halt_reasons.append(_bounded_error_context(str(pr_refusal_error or infra_error)))
    if resolver_error:
        halt_reasons.append(f"resolver failed: {resolver_error}")
    if carried_block:
        halt_reasons.append(carried_block)
    if post_error:  # The conflict gate's post_error already reads "conflict gate: <reason>".
        halt_reasons.append(post_error if consensus_status == "conflict_gate" else "consensus post failed")
    if tests_not_measured:
        halt_reasons.append("test results not measured")
    not_approved = not_approved_verdicts(reviewers)
    if not_approved:
        halt_reasons.append(f"not approved: {', '.join(not_approved)}")
    final_verdicts = f"final verdicts: {render_final_verdicts(reviewers)}"

    # Record overall summary in executions.jsonl
    review_exec_id = build_review_id(context.agent)
    summary_entry = {
        "execution_id": review_exec_id,
        "plan_branch": context.plan_branch or review_target,
        "execution_branch": review_target,
        "agent": context.agent,
        "agent_version": "1.4.0",
        "model": context.model,
        "status": "approved" if consensus_reached else "failed",
        "entry_type": "summary",
        "summary": (
            f"Review Loop reached consensus approval after {iteration} iteration(s); PR ready for maintainer merge"
            if consensus_reached
            else f"Review Loop halted without consensus after {iteration} iteration(s)"
            + (f": {'; '.join(halt_reasons)}" if halt_reasons else "")
        )
        + f"; {final_verdicts}",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        # Label units: 'total_iterations' is the last pass label, so its ceiling is the label budget.
        "total_iterations": iteration,
        "max_iterations": last_label,
        "ci_checks_summary": last_ci_status,
        "trailers": last_trailers_dict,
        "review_post_withheld": decision.reason or None,
    }
    if effective_dry_run:
        suppressed_ledger_records += 1
    else:
        try_append_ledger_line(exec_file, summary_entry, label="Stage 4 review summary", log_fn=context.log)

    # Record action event in executions.jsonl if available
    if effective_dry_run:
        suppressed_ledger_records += 1
        context.log(f"[Review Loop] Dry run: {suppressed_ledger_records} ledger record(s) not appended to {exec_file}.")
    else:
        try:
            append_action_event(
                {
                    "execution_id": review_exec_id,
                    "action": f"Review Loop executed for {review_target}",
                    "category": "test",
                    "target": review_target,
                    "verdict": "success" if consensus_reached else "failure",
                    "outcome": "success" if consensus_reached else "failure",
                    "entry_type": "action",
                    "context_fingerprint": compute_context_fingerprint(
                        target=review_target, tool=context.agent, command_prefix="review-loop"
                    ),
                    "labels": ["review", "review-loop", context.agent],
                    "error_context": (
                        None
                        if consensus_reached
                        else f"Consensus failed: findings={finding_ids}"
                        + (f"; {'; '.join(halt_reasons)}" if halt_reasons else "")
                        + f"; {final_verdicts}"
                    ),
                },
                ledger_path=exec_file,
                project_db_path=os.path.join(repo_dir, "holon-knowledge", ".index", "action_index.db"),
            )
        except LedgerRewriteError:
            raise
        except Exception as e:
            _warn_ledger_failure(context, "Stage 4 review action event", exec_file, e)
    if not effective_dry_run:  # Every exit path, a failed or halted iteration included, commits its final rows.
        _commit_pending_ledger_rows(context, iteration)

    loop_result: dict[str, Any] = {
        "target": review_target,
        "approved": consensus_reached,
        "ready_for_maintainer_merge": consensus_reached,
        "consensus": {
            "approved": consensus_reached,
            "score": approval_score,
            "iterations": iteration,
            "passes": passes,
            "max_review_iterations": last_label,
            "status": consensus_status,
            **approval_record(consensus_data),  # Stage 5 compares the head it calibrates with the approved one.
            "gates": gates_summary(context),  # Latest recorded gates; may predate a dry-run/infra-failed iteration.
        },
        "halted_for_human": False,
        "human_merge_gate_enforced": consensus_reached,
        "human_approval_command": f"holon review approve {review_target}",
        "mergeability": last_mergeability,
        "ci_checks": last_ci_status,
        "ci_wait": ci_wait.as_record() if ci_wait else None,
        "tests_failed": tests_failed,
        "trailers": last_trailers_str,
        # Label units, matching the summary ledger row and the console 'Iterations: {label} / {label budget}' line.
        "total_iterations": iteration,
        "max_iterations": last_label,
        "findings": finding_ids,
        "severity": severity,
        "posting": posting_res,
        "pr": pr_res,
        "iteration_summaries": iteration_summaries,
        "budget_breach": budget_breach,
        "ensemble_verdict": consensus_data["ensemble_verdict"],
        "reviewers": consensus_data["reviewers"],
        "review_data": consensus_data["review_data"],
        "dispositions": consensus_data["dispositions"],
        "no_convergence": no_convergence,
        "consensus_blockers": list(blockers),
        "resolver_error": resolver_error,
        "calibration_freshness": calibration_freshness,
    }
    if not effective_dry_run:  # A dry run decides nothing, so the checkpointed carried findings stay as they were.
        loop_result["open_findings"] = serialize_open(remaining_findings, finding_action_ids)
    if pr_refusal_error or infra_error or post_error or resolver_error:
        loop_result["error"] = pr_refusal_error or infra_error or post_error or f"resolver failed: {resolver_error}"
    return loop_result


def _reviewers_objected(loop_res: dict[str, Any]) -> bool:
    """True when a completed reviewer did not approve cleanly in the deciding iteration."""
    return any(
        r.get("status") == str(ReviewerStatus.COMPLETED)
        and (r.get("verdict") != "APPROVED" or any((r.get("counts") or {}).values()))
        for r in loop_res.get("reviewers") or []
    )


def _review_window(context: FlowContext) -> tuple[int, int]:
    """Return the next Stage 4 pass label and the engine's remaining pass budget."""
    done = max(0, context.review_iterations_done)
    return done + 1, context.max_review_iterations - done


def _run_engine_review_loop(context: FlowContext, *, github: GitHubAdapter | None = None) -> dict[str, Any]:
    """Run the remaining engine passes of Stage 4, continuing the checkpointed count; dispatch nothing when spent.

    ``github`` is the stage's adapter, handed to every pass of the loop.

    The engine's checkpoint save after Stage 4 persists the updated count, whatever the stage outcome.
    """
    first_label, remaining = _review_window(context)
    if remaining <= 0:
        err = (
            f"the review iteration budget ({context.max_review_iterations}) is exhausted after "
            f"{context.review_iterations_done} iteration(s)."
        )
        context.log(f"[Review Loop] Not dispatching: {err}")
        # iterations = label of the latest pass already run (the clamped checkpointed count); passes = 0 dispatches.
        done = first_label - 1
        consensus = {"approved": False, "score": 0.0, "iterations": done, "passes": 0, "status": "failed"}
        return {"consensus": consensus, "total_iterations": done, "error": err, "accounting": accounting_of(context)}
    loop_res = run_review_loop(
        context=context,
        target=context.execution_branch,
        max_iterations=remaining,
        ensemble_size=context.review_ensemble_size,
        dry_run=context.dry_run,
        first_iteration=first_label,
        github=github,
    )
    context.review_iterations_done = max(context.review_iterations_done, int(loop_res.get("total_iterations") or 0))
    if "open_findings" in loop_res:  # Saved with the checkpoint after Stage 4, so a resume resolves them first.
        context.carried_review_findings = list(loop_res["open_findings"])
    loop_res["accounting"] = accounting_of(context)  # None (rendered "not recorded") when nothing was counted.
    return loop_res
