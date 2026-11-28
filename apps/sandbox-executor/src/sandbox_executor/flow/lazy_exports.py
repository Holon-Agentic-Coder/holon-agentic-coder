"""The flow package's re-export table: owning module -> the space-separated names ``sandbox_executor.flow`` exports.

Kept out of ``flow/__init__.py`` so the package module stays within its line budget; it imports nothing, so loading it
never pulls in a flow submodule.
"""

from __future__ import annotations

from sandbox_executor.flow.lazy_exports_review import REVIEW_LAZY_EXPORTS
from sandbox_executor.flow.lazy_exports_sync import SYNC_LAZY_EXPORTS

LAZY_EXPORTS: dict[str, str] = {
    "sandbox_executor.entrypoint.planner": "parse_metrics",
    "sandbox_executor.flow.admission": "admission_stage_failure check_admission format_refusal record_admission",
    "sandbox_executor.flow.admission_decision": (
        "AdmissionConflict AdmissionDecision decide_admission format_blocker_provenance refusal"
    ),
    "sandbox_executor.flow.admission_lock": "AdmissionLockError admission_lock_path",
    "sandbox_executor.flow.admission_order": "blocker_is_clear older_ready_waiter older_waiter_flows waiter_key",
    "sandbox_executor.flow.admission_record": "override_already_recorded",
    "sandbox_executor.flow.admission_state": "AdmissionRecord BlockedBy admitted_record blocked_record utc_now",
    "sandbox_executor.flow.base_files": "base_branch_name list_base_files",
    "sandbox_executor.flow.calibrate_rebuild": (
        "holds_only_calibration_commits is_ancestor is_calibration_commit is_calibration_path "
        "rebuild_calibrated_branch stale_calibrated_refs"
    ),
    "sandbox_executor.flow.calibrate_retry": (
        "CalibrateFailure CalibrationRetriesExhausted RetryKind call_with_retries classify_calibrate_error "
        "redacted_cause"
    ),
    "sandbox_executor.flow.calibrate_rerun": (
        "RerunSeams post_stale_signal report_staleness rerun_calibration review_calibration_freshness"
    ),
    "sandbox_executor.flow.calibrate_stale_warning": (
        "StaleRange WarningSeams post_refreshed refreshed_body resolve_stale stale_body superseded_body warn_stale"
    ),
    "sandbox_executor.flow.calibrate_staleness": "HeadMoveCheck head_move_since_calibration",
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
    "sandbox_executor.flow.execution_recovery": (
        "RecoveredExecution keep_rejected_executions rejected_push_error rejected_push_failure rejected_push_recovery"
    ),
    "sandbox_executor.flow.git_refs": (
        "EXECUTOR_GIT_ENV _append_ledger_row _append_superseding_execution_row _discover_child_branch "
        "_execution_effective_diff _execution_parent_plan_branch _fetch_branch_prefix _fetch_branch_prefix_error "
        "_fetch_refspecs_error _git_show_text _has_origin_remote _latest_execution_summary _list_child_branches "
        "_origin_has_branch _read_ledger_rows _resolve_branch_ref _resolve_review_commit _snapshot_child_branches "
        "_sync_local_execution_refs _track_local_branch fast_forward_local_branch"
    ),
    "sandbox_executor.flow.github.adapter": "GitHubAdapter merge_argv parse_gh_json",
    "sandbox_executor.flow.github.adapter_runner": "GhResult gh_env spawn_gh",
    "sandbox_executor.flow.github.token_scopes": (
        "TokenScopeReport doctor_lines fetch_scopes resolve_github_token scope_report token_type touches_workflows "
        "workflow_scope_error"
    ),
    "sandbox_executor.flow.github.approval_invalidation": "CommentRecord gh_comment_editor gh_comment_records",
    "sandbox_executor.flow.github.ci": (
        "CI_REGISTRATION_TIMEOUT_ENV_VAR CI_REGISTRATION_TIMEOUT_SECONDS _wait_for_ci_registration probe_ci_checks "
        "resolve_ci_registration_timeout"
    ),
    "sandbox_executor.flow.github.ci_policy": (
        "_ci_green _extract_failed_ci_check_names local_tests_ci_status summarise_ci_checks"
    ),
    "sandbox_executor.flow.github.ci_final_head": "format_final_head_ci",
    "sandbox_executor.flow.github.ci_head_checks": (
        "classify_head_checks gh_head_checks_probe is_cancelled is_failed is_pending latest_checks_for_head "
        "parse_check_runs parse_suite_statuses probe_head_checks suite_pending with_suite_status"
    ),
    "sandbox_executor.flow.github.ci_head_reruns": "CancelledReruns gh_run_rerunner",
    "sandbox_executor.flow.github.ci_reverify": "rerun_note",
    "sandbox_executor.flow.github.ci_wait": (
        "CiWaitPolicy CiWaitResult is_pending_only next_poll_interval pending_check_names settle_pending_ci "
        "wait_for_pending_ci"
    ),
    "sandbox_executor.flow.github.forge": "GitForge",
    "sandbox_executor.flow.github.merge": "BranchMerge merge_branch_detailed",
    "sandbox_executor.flow.github.merge_method": "choose_merge_flag lookup_merge_flag",
    "sandbox_executor.flow.github.mergeability_policy": (
        "gh_view_verdict local_error_result merge_tree_verdict mergeability_result missing_ref_result "
        "not_a_repository_result"
    ),
    "sandbox_executor.flow.github.pr_head": "pr_head_sha",
    "sandbox_executor.flow.github.pr": (
        "_resolve_commit_ref check_pr_mergeability create_or_update_pr format_review_git_trailers"
    ),
    "sandbox_executor.flow.github.pr_metadata": "generate_semantic_pr_metadata",
    "sandbox_executor.flow.github.review_policy": (
        "CommentAttempt ReviewAttempt comment_failed_output comment_not_attempted_output comment_posted_output "
        "gh_unavailable_outcome is_self_approval_rejection posting_outcome review_failed_output review_flag "
        "review_submitted_output"
    ),
    "sandbox_executor.flow.github.review_post": "post_pr_review_consensus",
    "sandbox_executor.flow.github.stage5_test_failure": "check_log_text record_stage5_test_failure",
    "sandbox_executor.flow.host_remote": (
        "HostRemoteError fetch_base new_local_child_branches publish_new_child_branches push_branch push_commit "
        "remote_error"
    ),
    "sandbox_executor.flow.in_flight": (
        "AdmissionUnavailable BlockedWaiter InFlightFlow command_failure discover_in_flight_flows host_git "
        "local_blocked_waiters local_in_flight_flows open_flow_pull_requests other_checkpoints read_plan_touch_set "
        "root_intent"
    ),
    "sandbox_executor.flow.in_flight_liveness": (
        "ProcessCmdlines fetch_prune_origin origin_branch_ref process_command_lines "
        "pull_request_is_closed_or_merged worktree_has_live_runner"
    ),
    "sandbox_executor.flow.intent_questions": (
        "IntentQuestions QuestionKind answered_by_id answered_by_text has_inline_answer is_marked_open "
        "validate_intent_questions"
    ),
    "sandbox_executor.flow.ledger_warning": "_warn_ledger_failure",
    "sandbox_executor.flow.ledger_reconcile": (
        "_CONFLICT_MARKERS _SECTION_AFTER_MARKER LEDGER_REL_PATHS ConflictResolutionError PrefixSide "
        "_has_conflict_marker_lines _ledgers_with_conflict_markers _resolve_conflicting_ledger "
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
    "sandbox_executor.flow.review.head_test_evidence": ("HeadEvidenceDecisionInput choose_evidence has_pass_marker"),
    "sandbox_executor.flow.process_reaper": "ReapSeams reap_flow_run reset_reaper start_reaper_run",
    "sandbox_executor.flow.review_restart": (
        "approval_stands calibrate_with_review_restart is_review_restart stage5_merged_head_gate"
    ),
    "sandbox_executor.flow.run_lock": (
        "FlowRunLockHeld HeldRunLock acquire_flow_run_lock held_run_lock_paths hold_flow_run_lock "
        "release_flow_run_lock run_lock_path"
    ),
    "sandbox_executor.flow.run_lock_record": "RunLockHolder",
    "sandbox_executor.flow.stage5": "Stage5Outcome Stage5Steps run_stage5 standalone_context",
    "sandbox_executor.flow.stage5_ledger": "already_reconciled recorded_verdicts",
    "sandbox_executor.flow.stage5_verdict": "resolve_execution_verdict",
    "sandbox_executor.flow.stage_conflict_resolution": "ConflictKind classify_conflicts resolve_classified_conflicts",
    "sandbox_executor.flow.stage_conflicts": (
        "ConflictDetection check_and_resolve_stage_conflicts detect_stage_conflicts"
    ),
    "sandbox_executor.flow.stages.calibrate": (
        "_commit_stage5_promoted_artifacts _commit_stage5_reconciliation run_calibrate_stage stage5_steps"
    ),
    "sandbox_executor.flow.stages.children": "_PR_URL_RE _child_context _child_review_pr",
    "sandbox_executor.flow.stages.decomposition": "PlanDecomposition _run_decomposed_plan _sub_intent_depth_error",
    "sandbox_executor.flow.stages.decomposition_summary": "_decomposition_child_lines format_decomposition_summary",
    "sandbox_executor.flow.stages.empty_diff_outcome": "empty_diff_failure",
    "sandbox_executor.flow.stages.execute": "ExecuteStage ExecuteState _resumable_partial_branch run_execute_stage",
    "sandbox_executor.flow.stages.execute_veto": "execution_veto_error",
    "sandbox_executor.flow.stages.intent": "IntentStage _reused_intent_conflict run_intent_stage",
    "sandbox_executor.flow.stages.plan": "PlanStage _format_entropy run_plan_stage",
    "sandbox_executor.flow.stages.plan_decision": (
        "PlanBudget _backfill_plan_row _over_budget_failure _plan_budget_decision"
    ),
    "sandbox_executor.flow.stages.plan_resume": (
        "AMBIGUOUS_PLAN_BRANCHES_HINT PlanCandidate adopt_existing_plan plan_candidates "
        "publish_planned_branches record_plan_branch"
    ),
    "sandbox_executor.flow.stages.plan_support": (
        "dry_run_plan_result plan_id_of plan_metrics plan_stage_payload replan_feedback_env"
    ),
    "sandbox_executor.flow.stages.review": "TESTS_NOT_MEASURED_ERROR _checkout_execution_branch run_review_stage",
    "sandbox_executor.flow.stages.review_outcome": "ReviewOutcomeContext build_review_stage_outcome",
    "sandbox_executor.flow.stages.stage_support": "consult_knowledge intent_payload_file stage_success",
    "sandbox_executor.flow.stages.sync_conflict": "sync_conflict_result",
    "sandbox_executor.flow.stages.token_scope_guard": (
        "declared_paths push_url pushes_over_https_github token_scope_failure"
    ),
    "sandbox_executor.flow.stages.touch_set_guard": (
        "superseding_failure_row touch_set_replan_exhausted touch_set_replan_feedback touch_set_violation "
        "touch_set_violation_result"
    ),
    "sandbox_executor.flow.worktree_checkpoints": "WorktreeCheckpoint read_worktree_checkpoint",
    "sandbox_executor.flow_guard": "SKIP_PUSH_DISPATCH_ERROR",
    "sandbox_executor.git": "run_git",
    "sandbox_executor.redaction": "redact_agent_secrets",
    "sandbox_executor.review_report": "build_pr_review_consensus_report",
    **REVIEW_LAZY_EXPORTS,
    **SYNC_LAZY_EXPORTS,
}
