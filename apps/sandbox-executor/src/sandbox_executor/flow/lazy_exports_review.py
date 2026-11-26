"""The ``sandbox_executor.flow.review`` part of the flow package's re-export table (see :mod:`.lazy_exports`).

It imports nothing, so loading it never pulls in a flow submodule.
"""

from __future__ import annotations

REVIEW_LAZY_EXPORTS: dict[str, str] = {
    "sandbox_executor.flow.review.adjudication": "_augment_pr_text_with_rejected _filter_adjudicated_findings",
    "sandbox_executor.flow.review.approval_carry_over": (
        "CarryOver apply_carry_over carry_approval_over carry_over_message carry_over_record decide_carry_over "
        "merged_head_coverage record_stage5_carry_over"
    ),
    "sandbox_executor.flow.review.approval_coverage": "ApprovalCoverage approval_covers approved_fingerprint_of",
    "sandbox_executor.flow.review.approval_fingerprint": (
        "approval_fingerprint_of approval_record fingerprints_match is_bookkeeping_only_move is_bookkeeping_path "
        "pr_change_fingerprint reviewable_pathspecs"
    ),
    "sandbox_executor.flow.review.carried_findings": (
        "CarriedPass carried_blocker deserialize_carried merge_findings resolve_carried_first serialize_open undisposed"
    ),
    "sandbox_executor.flow.review.ci_outcome": "describe_ci_outcome gate_ci_flags",
    "sandbox_executor.flow.review.cli": "run_review_loop_cli",
    "sandbox_executor.flow.review.consensus_withdrawal": (
        "WithdrawalSeams failed_runs_of render_withdrawal withdraw_consensus withdrawal_marker withdrawn_consensus_of"
    ),
    "sandbox_executor.flow.review.ensemble": (
        "_ENGINE_PASSES_PER_DISPATCH _dispatch_review_ensemble _ensemble_iteration _resolve_reviewed_target "
        "_review_base_ref _semantic_pr_text"
    ),
    "sandbox_executor.flow.review.git_identity": "REVIEW_GIT_ENV",
    "sandbox_executor.flow.review.ids": "build_review_id",
    "sandbox_executor.flow.review.loop": (
        "_loop_status _review_window _reviewers_objected _run_engine_review_loop run_review_loop"
    ),
    "sandbox_executor.flow.review.merged_head_gate": (
        "MergedHeadSeams format_merged_head_ci gate_merged_head_ci gate_record skipped_record"
    ),
    "sandbox_executor.flow.review.posting": "is_terminal_verdict",
    "sandbox_executor.flow.review.proxy_env": "review_proxy_env",
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
    "sandbox_executor.flow.review.sync_points": (
        "finalize_resolver_turn is_ledger_only_sync sync_after_resolver_turn sync_before_review_iteration "
        "sync_iteration_mergeability sync_review_head"
    ),
    "sandbox_executor.flow.review.sync_scope": (
        "compute_rereview_scope format_conflict_block_reason is_mechanical_only record_rereview_scope"
    ),
    "sandbox_executor.flow.review.verdict": "review_loop_approved review_summary_lines",
    "sandbox_executor.flow.review.workspace": "HOST_REVIEW_REFUSAL reviewers_sandboxed",
}
