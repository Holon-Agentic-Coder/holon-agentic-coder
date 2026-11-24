"""Tests for decomposition summaries, budget prechecks, replan edge cases, and PlanDecomposition."""

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sandbox_executor import flow as flow_module
from sandbox_executor.budgets import MAX_REPLAN_ATTEMPTS, MAX_SUB_INTENT_DEPTH
from sandbox_executor.entrypoint.executor import OVER_BUDGET_EXIT_CODE
from sandbox_executor.flow import (
    FlowContext,
    FlowStage,
    PipelineEngine,
    StageResult,
    StageStatus,
    run_execute_stage,
    run_intent_stage,
    run_plan_stage,
)

from tests.flow.helpers import (
    SUB_INTENT_LIST,
    BudgetRoleAgents,
    _budget_context,
    stage4_halts_at_consensus,
)
from tests.flow.lookup_patch import patch_lookup_sites


@pytest.mark.usefixtures("no_origin_reconcile")
class TestDecompositionDetails:
    """Budget precheck rules, depth cap, decomposition summary formatting, and replan edge cases."""

    @staticmethod
    def _decide(rows: list[dict[str, Any]], files: dict[str, str]) -> Any:
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent", "entropy_budget": 5.0})
        ctx.plan_branch, site = "I-1-parent/P-2-agent-model/_", "sandbox_executor.flow.stages.plan_decision"
        with (
            patch(f"{site}._resolve_branch_ref", return_value="refs/remotes/origin/plan"),
            patch(f"{site}._read_ledger_rows", return_value=[{"intent_branch": "I-1-parent/_", **row} for row in rows]),
            patch(f"{site}._git_show_text", side_effect=lambda _repo, _ref, path: files.get(path)),
            patch(f"{site}._backfill_plan_row", return_value=None),  # The write is covered in test_plan_decision.py.
        ):
            return flow_module._plan_budget_decision(ctx)

    def test_budget_precheck_reads_the_newest_plan_row(self):
        rows = [
            {"plan_id": "P-2-agent-model", "entropy": 12.6, "plan_file": "plans/P-2-agent-model.md"},
            {"plan_id": "P-2-agent-model", "entropy": 2.0, "plan_file": "plans/P-2-agent-model.md"},
        ]
        decision = self._decide(rows, {"plans/P-2-agent-model.md": "# Plan\n"})
        assert (decision.decision, decision.entropy) == ("execute", 2.0)

    @pytest.mark.parametrize("plan_file", ["plans/P-x/nested.md", "plans/P-x/../../README.md", "plans/../P-2.md"])
    def test_budget_precheck_ignores_a_plan_file_below_a_subdirectory_of_plans(self, plan_file):
        rows = [{"plan_id": "P-2-agent-model", "entropy": 9.0, "plan_file": plan_file}]
        files = {"plans/I-1-parent/P-2-agent-model.md": "# Plan\n" + SUB_INTENT_LIST, plan_file: "# Plan\n"}
        assert self._decide(rows, files).decision == "decomposed"

    def test_budget_precheck_with_a_missing_plan_file_judges_the_row_alone(self):
        rows = [{"plan_id": "P-2-agent-model", "entropy": 9.0, "plan_file": "plans/P-2-agent-model.md"}]
        decision = self._decide(rows, {})
        assert (decision.decision, decision.entropy, decision.entropy_budget) == ("over_budget", 9.0, 5.0)

    def test_budget_precheck_ignores_a_plan_file_outside_plans(self):
        rows = [{"plan_id": "P-2-agent-model", "entropy": 9.0, "plan_file": "README.md"}]
        files = {"plans/I-1-parent/P-2-agent-model.md": "# Plan\n" + SUB_INTENT_LIST, "README.md": "# Readme\n"}
        decision = self._decide(rows, files)
        assert decision.decision == "decomposed"
        assert [i["goal"] for i in decision.sub_intents] == ["Add the parser", "Add the CLI flag"]

    # --- Depth cap boundary -------------------------------------------------------------------------------------

    def test_sub_intent_depth_error_allows_children_at_the_cap_and_refuses_one_deeper(self):
        at_cap = FlowContext(token_reduce=False, intent_data={"sub_intent_depth": MAX_SUB_INTENT_DEPTH - 1})
        past_cap = FlowContext(token_reduce=False, intent_data={"sub_intent_depth": MAX_SUB_INTENT_DEPTH})

        assert flow_module._sub_intent_depth_error(at_cap) is None
        assert flow_module._sub_intent_depth_error(past_cap) == (
            f"Execute stage failed: the plan lists a sub-intent list at depth {MAX_SUB_INTENT_DEPTH + 1}, deeper than "
            f"the limit of {MAX_SUB_INTENT_DEPTH}; refusing to recurse further."
        )

    # --- Child PR reporting and the flow summary ------------------------------------------------------------------

    @pytest.mark.parametrize(
        ("pr", "expected"),
        [
            (
                {"pr_url": "https://github.com/o/r/pull/7", "pr_number": 7, "output": ""},
                ("https://github.com/o/r/pull/7", 7),
            ),
            (
                {
                    "pr_number": 5,
                    "output": "see https://github.com/o/r/compare/x then https://github.com/o/r/pull/5 ok",
                },
                ("https://github.com/o/r/pull/5", 5),
            ),
            ("not a dict", (None, None)),
            (None, (None, None)),
        ],
        ids=["explicit-url", "url-from-output", "not-a-dict", "missing"],
    )
    def test_child_review_pr(self, pr, expected):
        payload = {} if pr is None else {"pr": pr}
        results = {"review": StageResult(stage=FlowStage.REVIEW, status=StageStatus.HALTED_FOR_HUMAN, payload=payload)}

        assert flow_module._child_review_pr(results) == expected
        assert flow_module._child_review_pr({}) == (None, None)

    def test_failed_decomposition_summary_counts_the_listed_items_not_only_the_children_that_ran(self):
        items = [{"slug": "a"}, {"slug": "b"}, {"slug": "c"}, "not-an-item"]
        plan = "I-1-p/P-2-a-m/_"
        ran_two = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.FAILED,
            payload={
                "decomposed": True,
                "plan_branch": plan,
                "sub_intents": items,
                "children": [
                    {"slug": "a", "pr_url": "https://x/pull/1", "status": "halted_for_human", "merged": True},
                    {"slug": "b", "execution_branch": "E-b", "status": "failed"},
                ],
            },
        )
        assert flow_module.format_decomposition_summary(ran_two) == [
            f"Decomposed into 3 sub-intent(s); 2 of 3 ran and 1 merged into '{plan}' before Stage 3 failed:",
            f"  1. a: https://x/pull/1 (merged into '{plan}')",
            "  2. b: E-b (failed)",
        ]
        # A depth refusal inside the decomposed run fails before any child runs.
        ran_none = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.FAILED,
            payload={"decomposed": True, "sub_intents": items, "children": []},
        )
        assert flow_module.format_decomposition_summary(ran_none) == [
            "Decomposed into 3 sub-intent(s); 0 of 3 ran and 0 merged into 'the plan branch' before Stage 3 failed:",
        ]
        succeeded = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={
                "decomposed": True,
                "plan_branch": plan,
                "sub_intents": items[:2],
                "children": [{"slug": "a", "status": "s"}, {"slug": "b", "status": "s"}],
            },
        )
        assert flow_module.format_decomposition_summary(succeeded)[0] == (
            f"Decomposed into 2 sub-intent(s); each child PR was merged into '{plan}':"
        )

    def test_format_decomposition_summary_fallbacks(self):
        children = [
            {
                "slug": "a",
                "pr_url": "https://x/pull/1",
                "execution_branch": "E-a",
                "intent_branch": "I-a",
                "status": "s",
                "merged": True,
            },
            {"slug": "b", "execution_branch": "E-b", "intent_branch": "I-b", "status": "s"},
            {"slug": "c", "intent_branch": "I-c", "status": "s"},
            {"slug": "d", "status": "s"},
            "not-a-child",
        ]
        result = StageResult(
            stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"decomposed": True, "children": children}
        )

        assert flow_module.format_decomposition_summary(result) == [
            "Decomposed into 4 sub-intent(s); each child PR was merged into 'the plan branch':",
            "  1. a: https://x/pull/1 (merged into 'the plan branch')",
            "  2. b: E-b (s)",
            "  3. c: I-c (s)",
            "  4. d: no branch (s)",
            "Combined PR: Stage 4 raises and reviews 'the execution branch' (with 'the plan branch' merged in) into "
            "'its target branch'.",
        ]

    def test_failed_decomposition_summary_nests_a_decomposed_child_without_merge_guidance(self):
        children = [
            {
                "slug": "a",
                "intent_branch": "I-a/_",
                "execution_branch": "E-a",
                "status": "skipped",
                "decomposed": True,
                "children": [{"slug": "x", "pr_url": "https://x/pull/3", "status": "s"}, "not-a-child"],
            },
            {"slug": "b", "execution_branch": "E-b", "status": "failed"},
        ]
        result = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.FAILED,
            payload={"decomposed": True, "sub_intents": [{"slug": "a"}, {"slug": "b"}], "children": children},
        )

        assert flow_module.format_decomposition_summary(result) == [
            "Decomposed into 2 sub-intent(s); 2 of 2 ran and 0 merged into 'the plan branch' before Stage 3 failed:",
            "  1. a: decomposed into 1 sub-intent(s), combined in E-a (skipped):",
            "     1. x: https://x/pull/3 (s)",
            "  2. b: E-b (failed)",
        ]

    def test_decomposition_summary_names_each_decomposed_level_after_its_own_plan_branch(self):
        # The rendering recurses, so a decomposed grandchild is merged into its parent's plan branch, not the root's.
        grandchild = {
            "slug": "g",
            "intent_branch": "I-a/P-1--1-g/_",
            "plan_branch": "I-a/P-1--1-g/P-2/_",
            "pr_url": "https://x/pull/8",
            "merged": True,
            "decomposed": True,
            "children": [{"slug": "x", "pr_url": "https://x/pull/9", "status": "s", "merged": True}],
        }
        child = {
            "slug": "a",
            "plan_branch": "I-a/P-1/_",
            "pr_url": "https://x/pull/10",
            "merged": True,
            "decomposed": True,
            "children": [grandchild],
        }
        result = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={"decomposed": True, "sub_intents": [{"slug": "a"}], "children": [child], "plan_branch": "I-p/P/_"},
        )

        assert flow_module.format_decomposition_summary(result)[1:4] == [
            "  1. a: decomposed into 1 sub-intent(s), combined in https://x/pull/10 (merged into 'I-p/P/_'):",
            "     1. g: decomposed into 1 sub-intent(s), combined in https://x/pull/8 (merged into 'I-a/P-1/_'):",
            "        1. x: https://x/pull/9 (merged into 'I-a/P-1--1-g/P-2/_')",
        ]

    def test_successful_decomposition_summary_header_counts_the_listed_items(self):
        # The header counts the dict items the plan listed, independently of how many child summaries are present.
        result = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={
                "decomposed": True,
                "sub_intents": [{"slug": "a"}, {"slug": "b"}, {"slug": "c"}, "not-an-item"],
                "children": [{"slug": "a", "status": "s"}],
            },
        )
        assert flow_module.format_decomposition_summary(result)[0] == (
            "Decomposed into 3 sub-intent(s); each child PR was merged into 'the plan branch':"
        )

    @pytest.mark.parametrize("sub_intents", ["abc", 5], ids=["string", "number"])
    def test_decomposition_summary_with_a_non_list_sub_intent_value_counts_the_children(self, sub_intents):
        result = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={"decomposed": True, "sub_intents": sub_intents, "children": [{"slug": "a", "status": "s"}]},
        )
        assert flow_module.format_decomposition_summary(result)[0] == (
            "Decomposed into 1 sub-intent(s); each child PR was merged into 'the plan branch':"
        )

    # --- Pre-dispatch budget decision -----------------------------------------------------------------------------

    def test_budget_precheck_without_a_plan_branch_reads_nothing(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"})
        with patch_lookup_sites(
            "sandbox_executor.flow.git_refs._resolve_branch_ref",
            "sandbox_executor.flow.review.ensemble._resolve_branch_ref",
            "sandbox_executor.flow.stage_conflicts._resolve_branch_ref",
            "sandbox_executor.flow.stages.execute._resolve_branch_ref",
            "sandbox_executor.flow.stages.plan_decision._resolve_branch_ref",
        ) as resolve:
            assert flow_module._plan_budget_decision(ctx) is None
        resolve.assert_not_called()

    def test_budget_precheck_with_an_unresolvable_plan_ref_reads_no_ledger(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"})
        ctx.plan_branch = "I-1-parent/P-2-agent-model/_"
        with (
            patch("sandbox_executor.flow.stages.plan_decision._resolve_branch_ref", return_value=None),
            patch_lookup_sites(
                "sandbox_executor.flow.stages.execute._read_ledger_rows",
                "sandbox_executor.flow.stages.intent._read_ledger_rows",
                "sandbox_executor.flow.stages.plan._read_ledger_rows",
                "sandbox_executor.flow.stages.plan_decision._read_ledger_rows",
            ) as read_rows,
        ):
            assert flow_module._plan_budget_decision(ctx) is None
        read_rows.assert_not_called()

    def test_budget_precheck_ignores_rows_of_other_plans(self):
        rows = [
            {"plan_id": "P-2-agent-model", "entropy": 2.0, "plan_file": "plans/P-2-agent-model.md"},
            {"plan_id": "P-3-agent-model", "entropy": 12.6, "plan_file": "plans/P-3-agent-model.md"},
        ]
        files = {"plans/P-2-agent-model.md": "# Plan\n", "plans/P-3-agent-model.md": "# Plan\n"}
        assert self._decide(rows, files).decision == "execute"
        assert self._decide(rows[1:], files) is self._decide([{**rows[0], "intent_branch": "I-9-x/_"}], files) is None

    def test_budget_precheck_reads_the_recorded_plan_file_and_the_intent_budget(self):
        rows = [{"plan_id": "P-2-agent-model", "entropy": 9.0, "plan_file": "plans/P-2-agent-model-v2.md"}]
        decision = self._decide(rows, {"plans/P-2-agent-model-v2.md": "# Plan\n" + SUB_INTENT_LIST})
        assert decision.decision == "decomposed"
        # No list and no budget on the row: the intent's budget (5.0, not the default) decides.
        decision = self._decide(rows, {"plans/P-2-agent-model-v2.md": "# Plan\n"})
        assert (decision.decision, decision.entropy_budget) == ("over_budget", 5.0)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(12.6, "12.6"), (5, "5"), (5.0, "5"), (True, "True"), (None, "unknown"), ("lots", "lots")],
        ids=str,
    )
    def test_format_entropy(self, value, expected):
        assert flow_module._format_entropy(value) == expected

    @pytest.mark.parametrize(
        ("feedback", "expected"),
        [(" \n\t ", None), ("  Re-plan: entropy 12.6  ", "Re-plan: entropy 12.6")],
        ids=["blank", "padded"],
    )
    def test_plan_stage_forwards_only_non_blank_stripped_feedback(self, feedback, expected, monkeypatch, tmp_path):
        monkeypatch.delenv("HOLON_REPLAN_FEEDBACK", raising=False)
        ctx = FlowContext(
            repo_dir=str(tmp_path), token_reduce=False, intent_data={"slug": "parent", "replan_feedback": feedback}
        )
        ctx.intent_branch = "I-1-parent/_"
        seen: list[str | None] = []

        def dispatch(_context: FlowContext, _role: str, _args: list[str]) -> int:
            seen.append(os.environ.get("HOLON_REPLAN_FEEDBACK"))
            return 1

        with (
            patch("sandbox_executor.flow.stages.plan.at_stage_boundary", return_value=None),
            patch("sandbox_executor.flow.stages.plan._snapshot_child_branches", return_value=set()),
            patch("sandbox_executor.flow.stages.plan._dispatch_role", side_effect=dispatch),
        ):
            res = run_plan_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert seen == [expected]
        assert "HOLON_REPLAN_FEEDBACK" not in os.environ

    # --- Re-plan request wording, exception paths and the executor's refusal exit code -------------------------------

    @pytest.mark.parametrize(
        ("depth", "asked"),
        [
            (
                MAX_SUB_INTENT_DEPTH - 1,
                "write a smaller plan within the budget, or add an explicit sub-intent list.",
            ),
            (
                MAX_SUB_INTENT_DEPTH,
                "write a smaller single plan within the budget. Do not add a sub-intent list: this intent is at the "
                f"sub-intent depth limit of {MAX_SUB_INTENT_DEPTH}, so a sub-intent list would be refused.",
            ),
        ],
        ids=["below-the-depth-cap", "at-the-depth-cap"],
    )
    def test_replan_request_offers_a_sub_intent_list_only_below_the_depth_cap(self, flow_repos, tmp_path, depth, asked):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(12.6, ""), (2.0, "")])
        ctx = _budget_context(host, agents, 5.0, sub_intent_depth=depth)

        with stage4_halts_at_consensus():
            results = PipelineEngine(ctx).run()

        # The smaller re-plan runs normally, even for a leaf intent at the cap.
        assert results["execute"].status == StageStatus.SUCCESS
        assert agents.roles() == ["intent-creator", "planner", "planner", "executor"]
        assert (agents.replan_feedback_seen[1] or "").endswith(f"Re-plan attempt 1 of {MAX_REPLAN_ATTEMPTS}: {asked}")
        assert any(
            line.endswith(
                "] Stage 3: plan entropy 12.6 exceeds the entropy budget 5; returning to Stage 2 "
                f"(re-plan attempt 1 of {MAX_REPLAN_ATTEMPTS})."
            )
            for line in ctx.logs
        )

    def test_replan_feedback_variable_is_restored_when_the_planner_dispatch_raises(
        self, flow_repos, tmp_path, monkeypatch
    ):
        host, origin = flow_repos
        monkeypatch.delenv("HOLON_REPLAN_FEEDBACK", raising=False)
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(2.0, "")])
        ctx = _budget_context(host, agents, 5.0)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        ctx.intent_data["replan_feedback"] = "smaller please"
        seen: list[str | None] = []

        def crash(*_args: Any, **_kwargs: Any) -> int:
            seen.append(os.environ.get("HOLON_REPLAN_FEEDBACK"))
            raise RuntimeError("docker crashed")

        with patch("sandbox_executor.flow.stages.plan._dispatch_role", side_effect=crash), pytest.raises(RuntimeError):
            run_plan_stage(ctx)

        assert seen == ["smaller please"]
        assert "HOLON_REPLAN_FEEDBACK" not in os.environ

    def test_refused_plan_exit_code_with_another_row_fails_the_stage(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(2.0, "")])
        ctx = _budget_context(host, agents, 5.0)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS
        real_execute = agents._execute

        def execute_then_exit_refused(work: Path, args: list[str], kwargs: dict[str, Any]) -> int:
            real_execute(work, args, kwargs)
            return OVER_BUDGET_EXIT_CODE

        # Only an over_budget row explains the refusal exit code; any other row is an executor failure.
        with patch.object(agents, "_execute", side_effect=execute_then_exit_refused):
            res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert res.error == f"Execute stage failed: executor agent exited with code {OVER_BUDGET_EXIT_CODE}."
        assert res.payload["exit_code"] == OVER_BUDGET_EXIT_CODE
        assert res.payload["execution_branch"] == ctx.execution_branch is not None
        assert "over_budget" not in res.payload

    def test_refused_plan_exit_code_names_itself_when_the_fetch_fails(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(12.6, "")])
        ctx = _budget_context(host, agents, 5.0)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS

        real_fetch = flow_module._fetch_branch_prefix_error

        def fetch_fails_after_dispatch(repo_dir: str, prefix: str) -> str | None:
            return "network down" if "executor" in agents.roles() else real_fetch(repo_dir, prefix)

        with (
            patch("sandbox_executor.flow.stages.execute._plan_budget_decision", return_value=None),
            patch_lookup_sites(
                "sandbox_executor.flow.git_refs._fetch_branch_prefix_error",
                "sandbox_executor.flow.stages.execute._fetch_branch_prefix_error",
                side_effect=fetch_fails_after_dispatch,
            ),
        ):
            res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert res.error is not None
        assert res.error.startswith(f"Execute stage failed: the executor agent exited {OVER_BUDGET_EXIT_CODE} but ")
        assert res.error.endswith("could not be fetched to adopt its execution branch: network down")


class TestPlanDecomposition:
    """PlanDecomposition owns the sub-intent list checks, the failure payloads and the child-branch naming."""

    BASE_KEYS: frozenset[str] = frozenset({"execution_id", "decomposed", "sub_intents", "children"})
    PLAN_BRANCH = "I-1-parent/P-2-agent-model/_"

    @staticmethod
    def _failed_payload_keys(ctx: FlowContext, row: dict[str, Any]) -> set[str]:
        res = flow_module._run_decomposed_plan(ctx, "2026-10-26T00:00:00Z", row, {"execution_id": "E-1"})
        assert res.status == StageStatus.FAILED
        return set(res.payload)

    def test_the_no_items_failure_carries_no_plan_branch(self):
        ctx = FlowContext(token_reduce=False, plan_branch=self.PLAN_BRANCH)
        assert self._failed_payload_keys(ctx, {"sub_intents": []}) == self.BASE_KEYS

    def test_the_depth_failure_carries_the_plan_branch_only(self):
        ctx = FlowContext(
            token_reduce=False, plan_branch=self.PLAN_BRANCH, intent_data={"sub_intent_depth": MAX_SUB_INTENT_DEPTH}
        )
        row = {"sub_intents": [{"slug": "child"}], "entropy_budget": 5.0}
        assert self._failed_payload_keys(ctx, row) == self.BASE_KEYS | {"plan_branch"}

    def test_the_missing_plan_branch_failure_records_it_as_none(self):
        ctx = FlowContext(token_reduce=False)
        row = {"sub_intents": [{"slug": "child"}], "entropy_budget": 5.0}
        res = flow_module._run_decomposed_plan(ctx, "2026-10-26T00:00:00Z", row, {"execution_id": "E-1"})
        assert (
            res.error == "Execute stage failed: no plan branch is known, so the sub-intents cannot be nested under it."
        )
        assert set(res.payload) == self.BASE_KEYS | {"plan_branch"}
        assert res.payload["plan_branch"] is None

    def test_a_child_branch_too_long_fails_after_the_target_and_budget_are_recorded(self):
        ctx = FlowContext(token_reduce=False, intent_data={"target_branch": "develop"})
        row = {
            "sub_intents": [{"slug": "child"}],
            "plan_branch": f"I-1-{'a' * 300}/P-2-agent-model/_",
            "entropy_budget": 5.0,
        }
        res = flow_module._run_decomposed_plan(ctx, "2026-10-26T00:00:00Z", row, {"execution_id": "E-1"})
        assert set(res.payload) == self.BASE_KEYS | {"plan_branch", "parent_target_branch", "entropy_budget"}
        assert (res.payload["parent_target_branch"], res.payload["entropy_budget"]) == ("develop", 5.0)

    def test_from_summary_row_keeps_dict_items_and_prefers_the_context_plan_branch(self):
        row = {"sub_intents": [{"slug": "a"}, "not-a-dict", 3, {"slug": "b"}], "plan_branch": "I-9-other/P-1-x/_"}
        decomposition = flow_module.PlanDecomposition.from_summary_row(
            FlowContext(token_reduce=False, plan_branch=self.PLAN_BRANCH), row
        )
        assert decomposition.items == [{"slug": "a"}, {"slug": "b"}]
        assert decomposition.plan_branch == self.PLAN_BRANCH
        fallback = flow_module.PlanDecomposition.from_summary_row(FlowContext(token_reduce=False), row)
        assert fallback.plan_branch == "I-9-other/P-1-x/_"
        assert flow_module.PlanDecomposition.from_summary_row(FlowContext(), {"sub_intents": "x"}).items == []

    def test_depth_counts_the_children_one_level_below_the_intent(self):
        ctx = FlowContext(token_reduce=False, intent_data={"sub_intent_depth": 2})
        assert flow_module.PlanDecomposition(ctx, [], "").depth() == 3
