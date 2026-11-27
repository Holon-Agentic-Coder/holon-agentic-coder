from __future__ import annotations

from pathlib import Path

import pytest

from scripts.self_explaining_comments import find_comments_and_docstrings

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_PREFIX = "apps/sandbox-executor"
TESTS_DIR = Path(__file__).resolve().parent

PINNED_MODULES_BY_DIR: dict[str, str] = {
    "src/sandbox_executor": (
        "agent_runner.py calibration_diff.py calibration_ev.py calibration_evaluated_sha.py "
        "calibration_execution_owner.py calibration_format.py calibration_outcome.py calibration_predicted.py "
        "calibration_record.py calibration_review_churn.py calibration_rows.py calibration_self_check.py "
        "calibration_touch_set.py config_env_report.py env_registry.py env_registry_model.py "
        "intent_scope_check.py intent_scope_graph.py module_budget_guard.py preflight_outage.py redaction.py "
        "resolver_evidence.py resolver_result.py review_accounting.py review_ensemble.py review_request.py "
        "scaffold.py turns.py"
    ),
    "src/sandbox_executor/calibration": (
        "__init__.py actual.py actual_evidence.py branches.py checkout.py deltas.py physics.py predicted.py "
        "refs.py run.py"
    ),
    "src/sandbox_executor/cli": (
        "__init__.py __main__.py antigravity_mounts.py doctor.py doctor_env.py logging.py main.py options.py "
        "pi_mounts.py preflight.py"
    ),
    "src/sandbox_executor/cli/commands": "intent_check.py",
    "src/sandbox_executor/entrypoint": (
        "conflict_resolver.py conflict_resolver_verify.py planner.py review_loop.py review_loop_args.py"
    ),
    "src/sandbox_executor/entrypoint/executor": "__init__.py run.py",
    "src/sandbox_executor/flow": (
        "__init__.py admission.py admission_decision.py admission_lock.py admission_order.py admission_record.py "
        "admission_state.py base_files.py calibrate_fetch_retry.py calibrate_promotion.py calibrate_rebuild.py "
        "calibrate_rerun.py calibrate_retry.py calibrate_stale_warning.py calibrate_staleness.py "
        "calibrate_sync.py calibrate_target_sync.py checkpoint.py checkpoint_discovery.py checkpoint_store.py "
        "child_progress.py combined_base.py conflict_classes.py conflict_classes_lists.py "
        "conflict_classes_resolve.py conflict_classes_tokens.py conflict_resolver_attempt.py "
        "conflict_resolver_role.py constants.py context.py dispatch.py end_of_flow_sync.py execution_publish.py "
        "execution_recovery.py host_remote.py in_flight.py intent_questions.py lazy_exports_sync.py "
        "ledger_interleave.py ledger_reconcile.py ledger_warning.py ledger_worktree.py merge_git.py "
        "partial_head.py partial_salvage.py pipeline.py plan_refresh.py pr_changed_files.py process_reaper.py "
        "resume_guard.py review_restart.py run_lock.py run_lock_record.py stage5.py stage5_ledger.py "
        "stage5_verdict.py stage_conflict_resolution.py stage_conflicts.py stage_status.py target_probe.py "
        "target_sync.py target_sync_branch.py target_sync_hooks.py target_sync_merge.py target_sync_record.py "
        "target_sync_result.py target_sync_resume.py test_evidence.py unrecorded_branch_adoption.py "
        "worktree_checkpoints.py"
    ),
    "src/sandbox_executor/flow/github": (
        "__init__.py ci.py ci_failure_detail.py ci_head_reruns.py ci_policy.py ci_slowest_tests.py"
    ),
    "src/sandbox_executor/flow/review": (
        "__init__.py adjudication.py approval_carry_over.py approval_coverage.py approval_fingerprint.py "
        "approval_gates.py carried_findings.py ci_evidence.py ci_failure_routing.py ci_inspection.py "
        "ci_noise_rerun.py ci_outcome.py ci_timing_finding.py cli.py conflict_gate.py conflict_merge.py "
        "consensus_withdrawal.py delta_review.py delta_review_forwarding.py delta_review_git.py "
        "delta_review_limits.py delta_review_mode.py delta_review_request.py git_identity.py "
        "head_test_evidence.py ids.py ledger_commit.py local_tests.py merged_head_gate.py merged_head_resume.py "
        "merged_head_routing.py posting.py"
    ),
    "src/sandbox_executor/flow/stages": (
        "__init__.py calibrate.py children.py decomposition.py decomposition_summary.py empty_diff_outcome.py "
        "execute.py execute_veto.py integration.py intent.py plan.py plan_decision.py plan_resume.py "
        "plan_support.py review_outcome.py stage_support.py sync_conflict.py token_scope_guard.py "
        "touch_set_guard.py"
    ),
    "src/sandbox_executor/knowledge": "fragment_identities.py ledger_fragment_sync.py",
    "src/sandbox_executor/launcher": "run_tracking.py",
    "src/sandbox_executor/runners": "__init__.py antigravity.py claude.py codex.py opencode.py pi.py spec.py",
    "tests": (
        "ci_bookkeeping_fakes.py executor_fixtures.py hermetic_fixtures.py test_agent_runner.py "
        "test_calibrate_rerun.py test_calibration_commit.py test_calibration_dual_ev.py "
        "test_ci_timing_compare.py test_ci_timing_noise.py test_complexity_gate.py test_doctor_replay.py "
        "test_executor_agent_output_redaction.py test_executor_commands.py test_executor_outcome.py "
        "test_executor_sub_intents.py test_flow_default_branch_worktree.py test_flow_e2e_harness.py test_git.py "
        "test_host_worktree_sandbox.py test_init.py test_knowledge.py test_launcher_sidecar.py "
        "test_ledger_append_only.py test_ledger_lock.py test_ledger_query.py test_local_llm.py test_makefile.py "
        "test_malformed_config_never_crashes.py test_planner.py test_preflight.py test_promotion_idempotence.py "
        "test_review_ensemble.py test_review_failure.py test_review_failure_detail.py "
        "test_review_loop_budget_guard.py test_review_loop_git_identity.py test_review_loop_worktree_head.py "
        "test_review_markdown_gate.py test_review_mounted_no_fetch.py test_review_report.py "
        "test_review_request_bulk_diff.py test_review_resolver_failure.py test_review_resolver_turn_budgets.py "
        "test_review_restart.py test_review_stacked_pr.py test_review_target.py "
        "test_review_workspace_integration.py test_runner_specs.py test_sandbox_hermetic_guard.py "
        "test_self_doc_gate_coverage.py test_sidecar_scratch_location.py test_stress_selection.py "
        "test_stress_support.py test_taken_ids.py test_test_measurement.py test_touch_set.py "
        "test_touch_set_overlap.py test_turn_live_children.py test_turn_supervisor.py test_turns.py "
        "test_unique_stage_ids.py test_url_credential_redaction.py test_uv_lock.py test_wisdom.py "
        "test_workflow_triggers.py test_workspace_survival_stress.py test_worktree_head.py "
        "url_redaction_matrix.py worktree_fixtures.py worktree_repo_templates.py"
    ),
    "tests/cli": (
        "_helpers.py test_doctor.py test_env_files_and_redaction.py test_flow_cli.py test_intent_check_cli.py "
        "test_main.py test_patch_targets.py"
    ),
    "tests/e2e": "test_flow_e2e.py",
    "tests/flow": (
        "decomposition_fakes.py fake_gh.py helpers.py hermetic_repos.py lookup_patch.py sleep_patch.py "
        "stage_templates.py sync_boundary_repos.py test_admission.py test_admission_base_files.py "
        "test_admission_lock.py test_admission_order.py test_calibrate_nested_refs.py "
        "test_calibrate_sync_refs.py test_checkpoint.py test_checkpoint_store.py test_child_progress.py "
        "test_conflict_classes.py test_conflict_resolver_attempt.py test_conflict_resolver_role.py "
        "test_conflict_resolver_tiers.py test_context.py test_dispatch.py test_end_of_flow_sync.py "
        "test_execution_recovery.py test_flow_repos_hermetic.py test_flow_worktree_run_lock.py test_git_refs.py "
        "test_git_revisions.py test_host_publish.py test_host_remote.py test_in_flight.py "
        "test_intent_questions.py test_ledger_interleave.py test_ledger_reconcile.py "
        "test_ledger_worktree_cleanup.py test_module_budget_discovery.py test_package_exports.py "
        "test_patch_targets.py test_pipeline.py test_pipeline_approved.py test_pipeline_touch_set.py "
        "test_pipeline_unlimited.py test_plan_refresh.py test_process_reaper.py test_resume_checkpoint.py "
        "test_resume_guard.py test_review_local_tests.py test_run_lock.py test_sleep_patch.py "
        "test_worktree_all_stages.py"
    ),
    "tests/flow/github": "test_pr.py test_review_post.py",
    "tests/flow/stages": "test_children.py test_decomposition.py",
}

COVERED_MODULES: tuple[str, ...] = tuple(
    sorted(
        f"{APP_PREFIX}/{directory}/{filename}"
        for directory, filenames in PINNED_MODULES_BY_DIR.items()
        for filename in filenames.split()
    )
)

FORBIDDEN_SLICE_GLOBS: tuple[str, ...] = (
    "test_self_doc_src_*.py",
    "test_self_doc_tests_*.py",
    "test_self_doc_cli_package.py",
    "test_self_doc_flow_github_package.py",
    "test_self_doc_root_modules_*.py",
    "test_self_doc_runners_launcher.py",
)


def test_covered_modules_registry_is_unique_and_non_empty() -> None:
    assert len(COVERED_MODULES) == 326
    assert len(set(COVERED_MODULES)) == len(COVERED_MODULES)
    assert f"{APP_PREFIX}/tests/test_self_doc_gate_coverage.py" in COVERED_MODULES


@pytest.mark.parametrize("relative_path", COVERED_MODULES)
def test_covered_module_exists(relative_path: str) -> None:
    assert (REPO_ROOT / relative_path).is_file(), f"Missing covered module: {relative_path}"


@pytest.mark.parametrize("relative_path", COVERED_MODULES)
def test_covered_module_is_self_explaining(relative_path: str) -> None:
    source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    findings = find_comments_and_docstrings(relative_path, source)
    assert findings == [], f"{relative_path} has self-explaining findings: {findings}"


def test_detector_flags_prepended_docstring_and_explanatory_comment() -> None:
    source = '"""Module docstring."""\nvalue = 1\n# Explain value.\n'
    findings = find_comments_and_docstrings("example.py", source)
    assert [(finding.first_line, finding.kind) for finding in findings] == [
        (1, "module docstring"),
        (3, "comment"),
    ]


def test_detector_allows_reason_comments_and_machine_directives() -> None:
    source = (
        "#!/usr/bin/env python3\n"
        "# -*- coding: utf-8 -*-\n"
        "import os  # noqa: E501\n"
        "value = os.sep  # type: ignore[arg-type]\n"
        "# why: preserve wire format\n"
        "# fmt: off\n"
        "if value:  # pragma: no cover\n"
        "    pass\n"
        "# fmt: on\n"
        "other = 1  # fmt: skip\n"
    )
    assert find_comments_and_docstrings("example.py", source) == []


def test_no_per_slice_self_doc_copies_remain() -> None:
    remaining = sorted(path.name for pattern in FORBIDDEN_SLICE_GLOBS for path in TESTS_DIR.glob(pattern))
    assert remaining == []
    assert (TESTS_DIR / "test_self_doc_modules.py").is_file()
