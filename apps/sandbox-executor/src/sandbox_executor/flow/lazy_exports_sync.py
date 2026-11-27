from __future__ import annotations

# why: this table imports nothing, so loading it never pulls in a flow submodule
SYNC_LAZY_EXPORTS: dict[str, str] = {
    "sandbox_executor.flow.calibrate_target_sync": "make_calibrate_retry_sync sync_before_calibration",
    "sandbox_executor.flow.conflict_classes": (
        "ConflictClass ConflictSides classify_merge_conflicts classify_path classify_sides read_sides unmerged_stages"
    ),
    "sandbox_executor.flow.conflict_classes_lists": "appended_list_union",
    "sandbox_executor.flow.conflict_classes_resolve": "MechanicalResolution resolve_mechanical resolve_path",
    "sandbox_executor.flow.conflict_classes_tokens": "python_lines unchanged_outside",
    "sandbox_executor.flow.conflict_resolver_attempt": (
        "AttemptCheck MergeSnapshot check_attempt discard_tampered reset_attempt save_attempt_diff snapshot_merge "
        "stage_resolution"
    ),
    "sandbox_executor.flow.conflict_resolver_role": "conflict_code_resolver",
    "sandbox_executor.flow.conflict_resolver_tiers": "next_tier tier_ladder",
    "sandbox_executor.flow.end_of_flow_sync": "EndOfFlowSeams EndOfFlowSyncResult finish_flow",
    "sandbox_executor.flow.resume_guard": "resume_merge_error",
    "sandbox_executor.flow.merge_git": "git_out has_conflict_markers",
    "sandbox_executor.flow.target_probe": (
        "TargetProbe TargetProbeError merge_tree_conflicts parse_merge_tree_paths probe_target"
    ),
    "sandbox_executor.flow.target_sync": "sync_with_target",
    "sandbox_executor.flow.target_sync_hooks": (
        "TurnBoundarySyncError active_flow_branch at_flow_start at_stage_boundary at_turn_boundary "
        "consume_target_merge_notice format_target_merge_notice make_turn_boundary_hook"
    ),
    "sandbox_executor.flow.target_sync_merge": (
        "abort_merge complete_merge merge_in_progress merge_target merge_verification_error unmerged_paths"
    ),
    "sandbox_executor.flow.target_sync_record": "append_sync_row superseded_sync_conflict sync_row",
    "sandbox_executor.flow.target_sync_result": (
        "CodeResolution CodeResolver SyncBoundary SyncRequest SyncResult fail_closed_code_resolver"
    ),
    "sandbox_executor.flow.target_sync_resume": "MergeAdoption adopt_merge_in_progress",
}
