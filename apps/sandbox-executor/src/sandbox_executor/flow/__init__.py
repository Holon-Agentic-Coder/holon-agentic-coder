"""Holon Flow Orchestration Pipeline Engine.

Automates the complete 5-stage Holon Flow lifecycle. Stages 1-3 dispatch the same containerised role
agents as 'holon intent', 'holon plan' and 'holon execute' (via cli.dispatch_role_agent), so a flow run
is equivalent to invoking the stages by hand; the flow orchestrates and validates but never authors content:
1. Stage 1 (Intent Creation): Validate intent payload, dispatch the intent-creator agent, verify its branch.
2. Stage 2 (Plan Generation): Dispatch the planner agent and adopt the plan branch and plans/P-*.md it pushed;
   fail (never fabricate a plan) when no planner produced one.
3. Stage 3 (Plan Execution): Dispatch the executor agent, adopt its execution branch, and fail an execution
   whose diff against the plan branch is empty outside holon-knowledge/ledger/, plans/ and executions/.
4. Stage 4 (Review Loop): Autonomous review loop evaluating diffs and test results, strictly enforcing
   the human-only PR merge rule by halting at consensus approval without merging to main.
5. Stage 5 (Post-Execution Calibration): Run the post-execution calibration engine on the execution branch,
   producing calibration analysis report on the /calibrated branch.

The engine is split into focused modules; see docs/architecture.md (Flow engine module map).
"""

from __future__ import annotations

from sandbox_executor.entrypoint.planner import parse_metrics
from sandbox_executor.flow.checkpoint import (
    CHECKPOINT_MIGRATION_REGISTRY,
    CURRENT_CHECKPOINT_SCHEMA_VERSION,
    FlowCheckpoint,
    MigrationTransform,
    _migrate_v0_to_v1,
    get_default_checkpoint_path,
    load_checkpoint,
    migrate_checkpoint_data,
    save_checkpoint,
)
from sandbox_executor.flow.child_progress import _child_intent_branch, _sub_intent_depth
from sandbox_executor.flow.constants import CI_REGISTRATION_POLL_SECONDS, CURRENT_CONTEXT_SCHEMA_VERSION
from sandbox_executor.flow.context import (
    _SUMMARY_STATUS_WORDS,
    FlowContext,
    FlowStage,
    StageResult,
    StageStatus,
    _support_legacy_repo_dir,
)
from sandbox_executor.flow.dispatch import (
    _dispatch_role,
    _get_dispatcher,
    _stage_failure,
    _test_evidence,
    recorded_test_pass_rate,
)
from sandbox_executor.flow.git_refs import (
    EXECUTOR_GIT_ENV,
    _append_ledger_row,
    _append_superseding_execution_row,
    _discover_child_branch,
    _execution_effective_diff,
    _execution_parent_plan_branch,
    _fast_forward_local_branch,
    _fetch_branch_prefix,
    _fetch_branch_prefix_error,
    _fetch_refspecs_error,
    _git_show_text,
    _has_origin_remote,
    _latest_execution_summary,
    _list_child_branches,
    _origin_has_branch,
    _read_ledger_rows,
    _resolve_branch_ref,
    _resolve_review_commit,
    _snapshot_child_branches,
    _sync_local_execution_refs,
    _track_local_branch,
)
from sandbox_executor.flow.github.ci import (
    CI_REGISTRATION_TIMEOUT_ENV_VAR,
    CI_REGISTRATION_TIMEOUT_SECONDS,
    _ci_green,
    _extract_failed_ci_check_names,
    _wait_for_ci_registration,
    probe_ci_checks,
    resolve_ci_registration_timeout,
)
from sandbox_executor.flow.github.ci_wait import (
    CiWaitPolicy,
    CiWaitResult,
    is_pending_only,
    next_poll_interval,
    pending_check_names,
    settle_pending_ci,
    wait_for_pending_ci,
)
from sandbox_executor.flow.github.pr import (
    _resolve_commit_ref,
    check_pr_mergeability,
    create_or_update_pr,
    format_review_git_trailers,
    generate_semantic_pr_metadata,
)
from sandbox_executor.flow.github.review_post import post_pr_review_consensus
from sandbox_executor.flow.host_remote import (
    HostRemoteError,
    fetch_base,
    new_local_child_branches,
    publish_new_child_branches,
    push_branch,
    push_commit,
    remote_error,
)
from sandbox_executor.flow.intent_questions import validate_intent_questions
from sandbox_executor.flow.ledger_reconcile import (
    _CONFLICT_MARKERS,
    _SECTION_AFTER_MARKER,
    LEDGER_REL_PATHS,
    ConflictResolutionError,
    PrefixSide,
    _append_only_union,
    _has_conflict_marker_lines,
    _ledgers_with_conflict_markers,
    _resolve_conflicting_ledger,
    _resolve_ledger_under_lock,
    _split_conflict_sides,
    _write_resolved_ledger,
    extract_valid_ledger_rows,
    reconcile_jsonl_ledger_conflict,
    reconcile_ledger_file,
    resolve_append_only_conflict,
)
from sandbox_executor.flow.pipeline import PipelineEngine, run_flow_pipeline
from sandbox_executor.flow.review.adjudication import _augment_pr_text_with_rejected, _filter_adjudicated_findings
from sandbox_executor.flow.review.ci_outcome import describe_ci_outcome, gate_ci_flags
from sandbox_executor.flow.review.cli import run_review_loop_cli
from sandbox_executor.flow.review.ensemble import (
    _ENGINE_PASSES_PER_DISPATCH,
    _dispatch_review_ensemble,
    _ensemble_iteration,
    _resolve_reviewed_target,
    _review_base_ref,
    _semantic_pr_text,
)
from sandbox_executor.flow.review.loop import (
    _loop_status,
    _review_window,
    _reviewers_objected,
    _run_engine_review_loop,
    run_review_loop,
)
from sandbox_executor.flow.review.records import (
    _SEVERITY_RANK,
    POSTING_ERROR_CONTEXT_MAX_CHARS,
    _attach_transported_detail,
    _bounded_error_context,
    _posting_failure_context,
    _record_infrastructure_findings,
    _record_reviewer_rows,
    _refused_review_result,
    _review_row,
    _reviewer_finding_ids,
    _reviewer_summary,
    _worst_severity,
    record_review_action,
    render_reviewer_summary_line,
)
from sandbox_executor.flow.review.resolver import (
    DispositionRecorder,
    ResolverInput,
    ResolverOutcome,
    _dispatch_review_resolver,
    _invoke_resolver_pass,
    _lookup_introducing_action_id,
)
from sandbox_executor.flow.review.verdict import review_loop_approved, review_summary_lines
from sandbox_executor.flow.review.workspace import (
    HOST_REVIEW_REFUSAL,
    reviewers_sandboxed,
)
from sandbox_executor.flow.stage_conflicts import check_and_resolve_stage_conflicts
from sandbox_executor.flow.stages import children as _children
from sandbox_executor.flow.stages.calibrate import (
    _commit_stage5_promoted_artifacts,
    _commit_stage5_reconciliation,
    run_calibrate_stage,
)
from sandbox_executor.flow.stages.children import _PR_URL_RE, _child_context, _child_review_pr
from sandbox_executor.flow.stages.decomposition import (
    _decomposition_child_lines,
    _run_decomposed_plan,
    _sub_intent_depth_error,
    format_decomposition_summary,
)
from sandbox_executor.flow.stages.execute import (
    FAILED_EXECUTION_STATUSES,
    _resumable_partial_branch,
    _warn_ledger_failure,
    failed_partial_head,
    run_execute_stage,
)
from sandbox_executor.flow.stages.intent import _reused_intent_conflict, run_intent_stage
from sandbox_executor.flow.stages.plan import _format_entropy, run_plan_stage
from sandbox_executor.flow.stages.plan_decision import _backfill_plan_row, _over_budget_failure, _plan_budget_decision
from sandbox_executor.flow.stages.review import (
    REVIEW_GIT_ENV,
    TESTS_NOT_MEASURED_ERROR,
    _checkout_execution_branch,
    run_review_stage,
)
from sandbox_executor.flow_guard import SKIP_PUSH_DISPATCH_ERROR
from sandbox_executor.git import run_git
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.review_report import build_pr_review_consensus_report

# stages.children runs child flows through PipelineEngine, but pipeline imports the stages; bind it late.
_children.PipelineEngine = PipelineEngine

__all__ = [
    "CHECKPOINT_MIGRATION_REGISTRY",
    "CI_REGISTRATION_POLL_SECONDS",
    "CI_REGISTRATION_TIMEOUT_ENV_VAR",
    "CI_REGISTRATION_TIMEOUT_SECONDS",
    "CURRENT_CHECKPOINT_SCHEMA_VERSION",
    "CURRENT_CONTEXT_SCHEMA_VERSION",
    "EXECUTOR_GIT_ENV",
    "FAILED_EXECUTION_STATUSES",
    "HOST_REVIEW_REFUSAL",
    "LEDGER_REL_PATHS",
    "POSTING_ERROR_CONTEXT_MAX_CHARS",
    "REVIEW_GIT_ENV",
    "SKIP_PUSH_DISPATCH_ERROR",
    "TESTS_NOT_MEASURED_ERROR",
    "_CONFLICT_MARKERS",
    "_ENGINE_PASSES_PER_DISPATCH",
    "_PR_URL_RE",
    "_SECTION_AFTER_MARKER",
    "_SEVERITY_RANK",
    "_SUMMARY_STATUS_WORDS",
    "CiWaitPolicy",
    "CiWaitResult",
    "ConflictResolutionError",
    "DispositionRecorder",
    "FlowCheckpoint",
    "FlowContext",
    "FlowStage",
    "HostRemoteError",
    "MigrationTransform",
    "PipelineEngine",
    "PrefixSide",
    "ResolverInput",
    "ResolverOutcome",
    "StageResult",
    "StageStatus",
    "_append_ledger_row",
    "_append_only_union",
    "_append_superseding_execution_row",
    "_attach_transported_detail",
    "_augment_pr_text_with_rejected",
    "_backfill_plan_row",
    "_bounded_error_context",
    "_checkout_execution_branch",
    "_child_context",
    "_child_intent_branch",
    "_child_review_pr",
    "_ci_green",
    "_commit_stage5_promoted_artifacts",
    "_commit_stage5_reconciliation",
    "_decomposition_child_lines",
    "_discover_child_branch",
    "_dispatch_review_ensemble",
    "_dispatch_review_resolver",
    "_dispatch_role",
    "_ensemble_iteration",
    "_execution_effective_diff",
    "_execution_parent_plan_branch",
    "_extract_failed_ci_check_names",
    "_fast_forward_local_branch",
    "_fetch_branch_prefix",
    "_fetch_branch_prefix_error",
    "_fetch_refspecs_error",
    "_filter_adjudicated_findings",
    "_format_entropy",
    "_get_dispatcher",
    "_git_show_text",
    "_has_conflict_marker_lines",
    "_has_origin_remote",
    "_invoke_resolver_pass",
    "_latest_execution_summary",
    "_ledgers_with_conflict_markers",
    "_list_child_branches",
    "_lookup_introducing_action_id",
    "_loop_status",
    "_migrate_v0_to_v1",
    "_origin_has_branch",
    "_over_budget_failure",
    "_plan_budget_decision",
    "_posting_failure_context",
    "_read_ledger_rows",
    "_record_infrastructure_findings",
    "_record_reviewer_rows",
    "_refused_review_result",
    "_resolve_branch_ref",
    "_resolve_commit_ref",
    "_resolve_conflicting_ledger",
    "_resolve_ledger_under_lock",
    "_resolve_review_commit",
    "_resolve_reviewed_target",
    "_resumable_partial_branch",
    "_reused_intent_conflict",
    "_review_base_ref",
    "_review_row",
    "_review_window",
    "_reviewer_finding_ids",
    "_reviewer_summary",
    "_reviewers_objected",
    "_run_decomposed_plan",
    "_run_engine_review_loop",
    "_semantic_pr_text",
    "_snapshot_child_branches",
    "_split_conflict_sides",
    "_stage_failure",
    "_sub_intent_depth",
    "_sub_intent_depth_error",
    "_support_legacy_repo_dir",
    "_sync_local_execution_refs",
    "_test_evidence",
    "_track_local_branch",
    "_wait_for_ci_registration",
    "_warn_ledger_failure",
    "_worst_severity",
    "_write_resolved_ledger",
    "build_pr_review_consensus_report",
    "check_and_resolve_stage_conflicts",
    "check_pr_mergeability",
    "create_or_update_pr",
    "describe_ci_outcome",
    "extract_valid_ledger_rows",
    "failed_partial_head",
    "fetch_base",
    "format_decomposition_summary",
    "format_review_git_trailers",
    "gate_ci_flags",
    "generate_semantic_pr_metadata",
    "get_default_checkpoint_path",
    "is_pending_only",
    "load_checkpoint",
    "migrate_checkpoint_data",
    "new_local_child_branches",
    "next_poll_interval",
    "parse_metrics",
    "pending_check_names",
    "post_pr_review_consensus",
    "probe_ci_checks",
    "publish_new_child_branches",
    "push_branch",
    "push_commit",
    "reconcile_jsonl_ledger_conflict",
    "reconcile_ledger_file",
    "record_review_action",
    "recorded_test_pass_rate",
    "redact_agent_secrets",
    "remote_error",
    "render_reviewer_summary_line",
    "resolve_append_only_conflict",
    "resolve_ci_registration_timeout",
    "review_loop_approved",
    "review_summary_lines",
    "reviewers_sandboxed",
    "run_calibrate_stage",
    "run_execute_stage",
    "run_flow_pipeline",
    "run_git",
    "run_intent_stage",
    "run_plan_stage",
    "run_review_loop",
    "run_review_loop_cli",
    "run_review_stage",
    "save_checkpoint",
    "settle_pending_ci",
    "validate_intent_questions",
    "wait_for_pending_ci",
]
