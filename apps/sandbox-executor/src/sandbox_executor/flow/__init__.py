"""Holon Flow Orchestration Pipeline Engine.

Automates the complete 5-stage Holon Flow lifecycle (Stages 1-5).
The engine is split into focused modules; see docs/architecture.md (Flow engine module map).

The re-exports resolve lazily on first access, so importing one flow submodule (as the container entrypoints do)
does not load the host engine (``flow.pipeline``, ``flow.stages``) through this package.
"""

from __future__ import annotations

import importlib
from typing import Any

# A submodule import binds the submodule over a same-named package attribute, so these two functions are bound
# eagerly, after their (light) modules load, to keep ``flow.admission_lock`` and ``flow.worktree_checkpoints`` the
# functions.
from sandbox_executor.flow.admission_lock import admission_lock
from sandbox_executor.flow.worktree_checkpoints import worktree_checkpoints

# Owning module -> the space-separated names this package re-exports from it.
_LAZY_EXPORTS: dict[str, str] = {
    "sandbox_executor.entrypoint.planner": "parse_metrics",
    "sandbox_executor.flow.admission": "admission_stage_failure check_admission format_refusal record_admission",
    "sandbox_executor.flow.admission_decision": "AdmissionConflict AdmissionDecision decide_admission refusal",
    "sandbox_executor.flow.admission_lock": "AdmissionLockError admission_lock_path",
    "sandbox_executor.flow.admission_order": "blocker_is_clear older_ready_waiter older_waiter_flows waiter_key",
    "sandbox_executor.flow.admission_record": "override_already_recorded",
    "sandbox_executor.flow.admission_state": "AdmissionRecord BlockedBy admitted_record blocked_record utc_now",
    "sandbox_executor.flow.calibrate_rebuild": (
        "holds_only_calibration_commits is_ancestor is_calibration_commit is_calibration_path "
        "rebuild_calibrated_branch stale_calibrated_refs"
    ),
    "sandbox_executor.flow.calibrate_rerun": (
        "HeadMoveCheck RerunSeams head_move_since_calibration post_stale_signal report_staleness rerun_calibration "
        "review_calibration_freshness"
    ),
    "sandbox_executor.flow.calibrate_sync": (
        "CalibrationSyncError discard_empty_calibrated_branch prepare_calibrated_branch publish_calibration "
        "record_starting_branch restore_starting_branch sync_execution_ref"
    ),
    "sandbox_executor.flow.checkpoint": (
        "CHECKPOINT_MIGRATION_REGISTRY CURRENT_CHECKPOINT_SCHEMA_VERSION FlowCheckpoint MigrationTransform "
        "_migrate_v0_to_v1 load_checkpoint migrate_checkpoint_data save_admission save_checkpoint"
    ),
    "sandbox_executor.flow.checkpoint_discovery": "get_default_checkpoint_path",
    "sandbox_executor.flow.checkpoint_store": "CheckpointStore FileCheckpointStore",
    "sandbox_executor.flow.child_progress": "_child_intent_branch _sub_intent_depth",
    "sandbox_executor.flow.constants": "CI_REGISTRATION_POLL_SECONDS CURRENT_CONTEXT_SCHEMA_VERSION",
    "sandbox_executor.flow.context": (
        "_SUMMARY_STATUS_WORDS FlowContext FlowStage StageResult StageStatus _support_legacy_repo_dir"
    ),
    "sandbox_executor.flow.dispatch": (
        "_dispatch_role _get_dispatcher _stage_failure _test_evidence recorded_test_pass_rate"
    ),
    "sandbox_executor.flow.execution_publish": (
        "held_execution_failure hold_unrecorded_executions publish_with_first_row unrecorded_executions"
    ),
    "sandbox_executor.flow.git_refs": (
        "EXECUTOR_GIT_ENV _append_ledger_row _append_superseding_execution_row _discover_child_branch "
        "_execution_effective_diff _execution_parent_plan_branch _fetch_branch_prefix _fetch_branch_prefix_error "
        "_fetch_refspecs_error _git_show_text _has_origin_remote _latest_execution_summary _list_child_branches "
        "_origin_has_branch _read_ledger_rows _resolve_branch_ref _resolve_review_commit _snapshot_child_branches "
        "_sync_local_execution_refs _track_local_branch fast_forward_local_branch"
    ),
    "sandbox_executor.flow.github.ci": (
        "CI_REGISTRATION_TIMEOUT_ENV_VAR CI_REGISTRATION_TIMEOUT_SECONDS _ci_green _extract_failed_ci_check_names "
        "_wait_for_ci_registration probe_ci_checks resolve_ci_registration_timeout"
    ),
    "sandbox_executor.flow.github.ci_final_head": "format_final_head_ci",
    "sandbox_executor.flow.github.ci_reverify": "rerun_note",
    "sandbox_executor.flow.github.ci_wait": (
        "CiWaitPolicy CiWaitResult is_pending_only next_poll_interval pending_check_names settle_pending_ci "
        "wait_for_pending_ci"
    ),
    "sandbox_executor.flow.github.merge": "BranchMerge merge_branch_detailed",
    "sandbox_executor.flow.github.merge_method": "choose_merge_flag lookup_merge_flag",
    "sandbox_executor.flow.github.pr": (
        "_resolve_commit_ref check_pr_mergeability create_or_update_pr format_review_git_trailers "
        "generate_semantic_pr_metadata"
    ),
    "sandbox_executor.flow.github.review_post": "post_pr_review_consensus",
    "sandbox_executor.flow.host_remote": (
        "HostRemoteError fetch_base new_local_child_branches publish_new_child_branches push_branch push_commit "
        "remote_error"
    ),
    "sandbox_executor.flow.in_flight": (
        "AdmissionUnavailable BlockedWaiter InFlightFlow discover_in_flight_flows local_blocked_waiters "
        "local_in_flight_flows open_flow_pull_requests other_checkpoints read_plan_touch_set root_intent"
    ),
    "sandbox_executor.flow.intent_questions": (
        "IntentQuestions QuestionKind answered_by_id answered_by_text has_inline_answer is_marked_open "
        "validate_intent_questions"
    ),
    "sandbox_executor.flow.ledger_warning": "_warn_ledger_failure",
    "sandbox_executor.flow.ledger_reconcile": (
        "_CONFLICT_MARKERS _SECTION_AFTER_MARKER LEDGER_REL_PATHS ConflictResolutionError PrefixSide "
        "_append_only_union _has_conflict_marker_lines _ledgers_with_conflict_markers _resolve_conflicting_ledger "
        "_resolve_ledger_under_lock _split_conflict_sides _write_resolved_ledger extract_valid_ledger_rows "
        "reconcile_jsonl_ledger_conflict reconcile_ledger_file resolve_append_only_conflict"
    ),
    "sandbox_executor.flow.partial_head": "FAILED_EXECUTION_STATUSES failed_partial_head",
    "sandbox_executor.flow.partial_salvage": (
        "SalvageError SalvageResult leftover_paths salvage_for_resume salvage_leftovers"
    ),
    "sandbox_executor.flow.pipeline": "PipelineEngine run_flow_pipeline",
    "sandbox_executor.flow.plan_refresh": (
        "PLAN_REFRESH_KIND PlanRefresh plan_refresh_stage_failure record_plan_refresh refresh_already_recorded "
        "refresh_due refresh_payload refresh_plan_branch"
    ),
    "sandbox_executor.flow.review.adjudication": "_augment_pr_text_with_rejected _filter_adjudicated_findings",
    "sandbox_executor.flow.review.carried_findings": (
        "CarriedPass carried_blocker deserialize_carried merge_findings resolve_carried_first serialize_open undisposed"
    ),
    "sandbox_executor.flow.review.ci_outcome": "describe_ci_outcome gate_ci_flags",
    "sandbox_executor.flow.review.cli": "run_review_loop_cli",
    "sandbox_executor.flow.review.ensemble": (
        "_ENGINE_PASSES_PER_DISPATCH _dispatch_review_ensemble _ensemble_iteration _resolve_reviewed_target "
        "_review_base_ref _semantic_pr_text"
    ),
    "sandbox_executor.flow.review.git_identity": "REVIEW_GIT_ENV",
    "sandbox_executor.flow.review.ids": "build_review_id",
    "sandbox_executor.flow.review.loop": (
        "_loop_status _review_window _reviewers_objected _run_engine_review_loop run_review_loop"
    ),
    "sandbox_executor.flow.review.posting": "is_terminal_verdict",
    "sandbox_executor.flow.review.records": (
        "_SEVERITY_RANK POSTING_ERROR_CONTEXT_MAX_CHARS _attach_transported_detail _bounded_error_context "
        "_posting_failure_context _record_infrastructure_findings _record_reviewer_rows _refused_review_result "
        "_review_row _reviewer_finding_ids _reviewer_summary _worst_severity record_review_action "
        "render_reviewer_summary_line"
    ),
    "sandbox_executor.flow.review.resolver": (
        "DispositionRecorder ResolverInput ResolverOutcome _dispatch_review_resolver _invoke_resolver_pass "
        "_lookup_introducing_action_id"
    ),
    "sandbox_executor.flow.review.verdict": "review_loop_approved review_summary_lines",
    "sandbox_executor.flow.review.workspace": "HOST_REVIEW_REFUSAL reviewers_sandboxed",
    "sandbox_executor.flow.stage_conflict_resolution": "ConflictKind classify_conflicts resolve_classified_conflicts",
    "sandbox_executor.flow.stage_conflicts": (
        "ConflictDetection check_and_resolve_stage_conflicts detect_stage_conflicts"
    ),
    "sandbox_executor.flow.stages.calibrate": (
        "_commit_stage5_promoted_artifacts _commit_stage5_reconciliation run_calibrate_stage"
    ),
    "sandbox_executor.flow.stages.children": "_PR_URL_RE _child_context _child_review_pr",
    "sandbox_executor.flow.stages.decomposition": (
        "_decomposition_child_lines _run_decomposed_plan _sub_intent_depth_error format_decomposition_summary"
    ),
    "sandbox_executor.flow.stages.execute": "_resumable_partial_branch run_execute_stage",
    "sandbox_executor.flow.stages.intent": "_reused_intent_conflict run_intent_stage",
    "sandbox_executor.flow.stages.plan": "_format_entropy run_plan_stage",
    "sandbox_executor.flow.stages.plan_decision": "_backfill_plan_row _over_budget_failure _plan_budget_decision",
    "sandbox_executor.flow.stages.review": "TESTS_NOT_MEASURED_ERROR _checkout_execution_branch run_review_stage",
    "sandbox_executor.flow.stages.sync_conflict": "sync_conflict_result",
    "sandbox_executor.flow.stages.touch_set_guard": (
        "superseding_failure_row touch_set_replan_exhausted touch_set_replan_feedback touch_set_violation "
        "touch_set_violation_result"
    ),
    "sandbox_executor.flow.worktree_checkpoints": "WorktreeCheckpoint read_worktree_checkpoint",
    "sandbox_executor.flow_guard": "SKIP_PUSH_DISPATCH_ERROR",
    "sandbox_executor.git": "run_git",
    "sandbox_executor.redaction": "redact_agent_secrets",
    "sandbox_executor.review_report": "build_pr_review_consensus_report",
}
_OWNER = {name: module for module, names in _LAZY_EXPORTS.items() for name in names.split()}


def __getattr__(name: str) -> Any:
    """Resolve a re-exported name from its owning module on each access, so a patch on the owner is always seen."""
    module = _OWNER.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)


def __dir__() -> list[str]:
    return sorted({*globals(), *_OWNER})


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
    "PLAN_REFRESH_KIND",
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
    "AdmissionConflict",
    "AdmissionDecision",
    "AdmissionLockError",
    "AdmissionRecord",
    "AdmissionUnavailable",
    "BlockedBy",
    "BlockedWaiter",
    "BranchMerge",
    "CalibrationSyncError",
    "CarriedPass",
    "CheckpointStore",
    "CiWaitPolicy",
    "CiWaitResult",
    "ConflictDetection",
    "ConflictKind",
    "ConflictResolutionError",
    "DispositionRecorder",
    "FileCheckpointStore",
    "FlowCheckpoint",
    "FlowContext",
    "FlowStage",
    "HeadMoveCheck",
    "HostRemoteError",
    "InFlightFlow",
    "IntentQuestions",
    "MigrationTransform",
    "PipelineEngine",
    "PlanRefresh",
    "PrefixSide",
    "QuestionKind",
    "RerunSeams",
    "ResolverInput",
    "ResolverOutcome",
    "SalvageError",
    "SalvageResult",
    "StageResult",
    "StageStatus",
    "WorktreeCheckpoint",
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
    "admission_lock",
    "admission_lock_path",
    "admission_stage_failure",
    "admitted_record",
    "answered_by_id",
    "answered_by_text",
    "blocked_record",
    "blocker_is_clear",
    "build_pr_review_consensus_report",
    "build_review_id",
    "carried_blocker",
    "check_admission",
    "check_and_resolve_stage_conflicts",
    "check_pr_mergeability",
    "choose_merge_flag",
    "classify_conflicts",
    "create_or_update_pr",
    "decide_admission",
    "describe_ci_outcome",
    "deserialize_carried",
    "detect_stage_conflicts",
    "discard_empty_calibrated_branch",
    "discover_in_flight_flows",
    "extract_valid_ledger_rows",
    "failed_partial_head",
    "fast_forward_local_branch",
    "fetch_base",
    "format_decomposition_summary",
    "format_final_head_ci",
    "format_refusal",
    "format_review_git_trailers",
    "gate_ci_flags",
    "generate_semantic_pr_metadata",
    "get_default_checkpoint_path",
    "has_inline_answer",
    "head_move_since_calibration",
    "held_execution_failure",
    "hold_unrecorded_executions",
    "holds_only_calibration_commits",
    "is_ancestor",
    "is_calibration_commit",
    "is_calibration_path",
    "is_marked_open",
    "is_pending_only",
    "is_terminal_verdict",
    "leftover_paths",
    "load_checkpoint",
    "local_blocked_waiters",
    "local_in_flight_flows",
    "lookup_merge_flag",
    "merge_branch_detailed",
    "merge_findings",
    "migrate_checkpoint_data",
    "new_local_child_branches",
    "next_poll_interval",
    "older_ready_waiter",
    "older_waiter_flows",
    "open_flow_pull_requests",
    "other_checkpoints",
    "override_already_recorded",
    "parse_metrics",
    "pending_check_names",
    "plan_refresh_stage_failure",
    "post_pr_review_consensus",
    "post_stale_signal",
    "prepare_calibrated_branch",
    "probe_ci_checks",
    "publish_calibration",
    "publish_new_child_branches",
    "publish_with_first_row",
    "push_branch",
    "push_commit",
    "read_plan_touch_set",
    "read_worktree_checkpoint",
    "rebuild_calibrated_branch",
    "reconcile_jsonl_ledger_conflict",
    "reconcile_ledger_file",
    "record_admission",
    "record_plan_refresh",
    "record_review_action",
    "record_starting_branch",
    "recorded_test_pass_rate",
    "redact_agent_secrets",
    "refresh_already_recorded",
    "refresh_due",
    "refresh_payload",
    "refresh_plan_branch",
    "refusal",
    "remote_error",
    "render_reviewer_summary_line",
    "report_staleness",
    "rerun_calibration",
    "rerun_note",
    "resolve_append_only_conflict",
    "resolve_carried_first",
    "resolve_ci_registration_timeout",
    "resolve_classified_conflicts",
    "restore_starting_branch",
    "review_calibration_freshness",
    "review_loop_approved",
    "review_summary_lines",
    "reviewers_sandboxed",
    "root_intent",
    "run_calibrate_stage",
    "run_execute_stage",
    "run_flow_pipeline",
    "run_git",
    "run_intent_stage",
    "run_plan_stage",
    "run_review_loop",
    "run_review_loop_cli",
    "run_review_stage",
    "salvage_for_resume",
    "salvage_leftovers",
    "save_admission",
    "save_checkpoint",
    "serialize_open",
    "settle_pending_ci",
    "stale_calibrated_refs",
    "superseding_failure_row",
    "sync_conflict_result",
    "sync_execution_ref",
    "touch_set_replan_exhausted",
    "touch_set_replan_feedback",
    "touch_set_violation",
    "touch_set_violation_result",
    "undisposed",
    "unrecorded_executions",
    "utc_now",
    "validate_intent_questions",
    "wait_for_pending_ci",
    "waiter_key",
    "worktree_checkpoints",
]
