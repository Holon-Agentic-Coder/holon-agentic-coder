"""End-to-end tests of the decomposed child loop against a bare origin, with fake role agents and a fake ``gh``.

They prove that children nest under the plan branch (goal 8 b), run strictly one after another from its latest tip
(8 c), are merged into it with merge commits only and never into ``main`` (8 d), and that a resume continues from the
recorded progress without re-dispatching the parent executor or any child (8 f). ``TestChildLoopWithStubbedPipelines``
covers the same loop with each child pipeline, the merge and the integration stubbed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import Mock, patch

import pytest
from sandbox_executor import flow as flow_module
from sandbox_executor.budgets import MAX_SUB_INTENT_DEPTH
from sandbox_executor.flow import FlowContext, FlowStage, PipelineEngine, StageResult, StageStatus
from sandbox_executor.flow.checkpoint import get_default_checkpoint_path, load_checkpoint
from sandbox_executor.flow.child_progress import child_checkpoint_path, next_child_action
from sandbox_executor.flow.github.merge import MergeResult
from sandbox_executor.flow.stages.children import _build_child_intent, resume_children

from tests.flow.decomposition_fakes import decomposed_flow, halted_review
from tests.flow.helpers import SUB_INTENT_LIST, BudgetRoleAgents, _budget_context, _git


def _decomposed_context(host: Path, origin: Path, tmp_path: Path) -> tuple[FlowContext, BudgetRoleAgents]:
    agents = BudgetRoleAgents(tmp_path, origin, plans=[(9.0, SUB_INTENT_LIST), (2.0, "")])
    ctx = _budget_context(host, agents, 5.0)
    ctx.skip_review = False
    return ctx, agents


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=repo).returncode == 0


def _plan_file(execution_branch: str) -> str:
    """The per-plan source file the budget fake executor writes for the plan of ``execution_branch``."""
    plan_id = execution_branch.removesuffix("/_").split("/")[-2]
    return f"src/{plan_id.replace('-', '_')}.py"


def test_children_nest_under_the_plan_branch_and_run_one_after_another(flow_repos, tmp_path):
    host, origin = flow_repos
    ctx, _agents = _decomposed_context(host, origin, tmp_path)

    with decomposed_flow(origin, tmp_path) as reviews:
        results = PipelineEngine(ctx).run()

    execute = results["execute"]
    assert execute.status == StageStatus.SUCCESS
    plan = ctx.plan_branch
    first, second = execute.payload["children_progress"]
    original_plan_tip = _git(origin, "rev-parse", f"refs/heads/{plan}^1^1")
    # (b) Each child is cut from the plan branch tip of its time and its PR targets the plan branch.
    for entry in (first, second):
        assert entry["intent_branch"].startswith(plan.removesuffix("/_") + "--")
        assert reviews.gh.prs[entry["pr_number"]]["base"] == plan
        assert entry["status"] == "merged"
    assert _git(origin, "rev-parse", f"refs/heads/{first['intent_branch']}^") == original_plan_tip
    # (c) The second child is cut from the first child's merge commit, so it carries the first child's work.
    assert _git(origin, "rev-parse", f"refs/heads/{second['intent_branch']}^") == first["merge_commit"]
    assert _is_ancestor(origin, first["merge_commit"], second["execution_branch"])
    first_file = _plan_file(first["execution_branch"])
    assert _git(origin, "show", f"refs/heads/{second['execution_branch']}:{first_file}").startswith("PLAN = ")
    # (d) The plan branch gained exactly one two-parent merge commit per child, the child's head as second parent.
    merges = _git(origin, "rev-list", "--first-parent", "--merges", "--parents", f"refs/heads/{plan}").splitlines()
    assert [line.split()[0] for line in reversed(merges)] == [first["merge_commit"], second["merge_commit"]]
    assert all(len(line.split()) == 3 for line in merges)
    assert [line.split()[2] for line in reversed(merges)] == [
        _git(origin, "rev-parse", f"refs/heads/{entry['execution_branch']}") for entry in (first, second)
    ]
    merge_calls = reviews.gh.merge_calls()
    assert [call[2] for call in merge_calls] == [str(first["pr_number"]), str(second["pr_number"])]
    assert all(
        "--merge" in call and not {"--squash", "--rebase", "--delete-branch"} & set(call) for call in merge_calls
    )


def test_a_child_pr_whose_base_is_main_is_refused_and_stage_3_fails_naming_it(flow_repos, tmp_path):
    host, origin = flow_repos
    ctx, agents = _decomposed_context(host, origin, tmp_path)

    with decomposed_flow(origin, tmp_path, bases={"add-the-parser": "main"}) as reviews:
        results = PipelineEngine(ctx).run()

    execute = results["execute"]
    assert execute.status == StageStatus.FAILED
    assert execute.error.startswith(
        f"Execute stage failed: sub-intent 1/2 'add-the-parser' could not be merged into '{ctx.plan_branch}' "
        "(PR #101): "
    )
    assert "'main' is main, master or the repository default branch" in execute.error
    # gh pr merge was never invoked, main and the plan branch are untouched, and the second child never started.
    assert reviews.gh.merge_calls() == []
    assert reviews.gh.prs[101]["state"] == "OPEN"
    assert _git(origin, "rev-parse", "refs/heads/main") == _git(host, "rev-parse", "origin/main")
    assert _git(origin, "rev-list", "--merges", f"refs/heads/{ctx.plan_branch}") == ""
    # Refused only at the merge: the child stays review_done (merge_error recorded) so a resume retries the merge.
    first, second = execute.payload["children_progress"]
    assert (first["status"], second["status"]) == ("review_done", "pending")
    assert "'main' is main, master or the repository default branch" in first["merge_error"]
    assert next_child_action(first) == "merge"
    assert [p["slug"] for p in agents.intent_payloads[1:]] == ["add-the-parser"]
    assert "review" not in results


def test_a_resume_continues_the_failed_child_without_dispatching_anything_again(flow_repos, tmp_path):
    host, origin = flow_repos
    ctx, agents = _decomposed_context(host, origin, tmp_path)

    with decomposed_flow(origin, tmp_path, fail_once_for="add-the-cli-flag") as reviews:
        first_run = PipelineEngine(ctx).run()
        assert first_run["execute"].status == StageStatus.FAILED
        assert "sub-intent 2/2 'add-the-cli-flag' failed at stage 'review'" in first_run["execute"].error
        progress = first_run["execute"].payload["children_progress"]
        assert [e["status"] for e in progress] == ["merged", "failed"]
        dispatched_before = list(agents.calls)
        created_before = list(reviews.created)
        first_child_checkpoint = Path(child_checkpoint_path(ctx, progress[0]["intent_branch"])).read_bytes()

        checkpoint = load_checkpoint(get_default_checkpoint_path(str(host), "budget-test"))
        assert checkpoint.current_stage.value == "execute"
        resumed_ctx = checkpoint.context
        resumed_ctx.dispatcher = agents
        resumed = PipelineEngine(resumed_ctx, from_stage=checkpoint.current_stage).run()

    # (f) No container was dispatched again: not the parent executor, not a child's intent, plan or executor.
    assert agents.calls == dispatched_before
    # The failed child resumed from its own checkpoint at Stage 4 and reused its PR: the only new PR is the parent's
    # combined PR into main, raised by the parent's own Stage 4.
    (combined,) = [n for n in reviews.created if n not in created_before]
    assert reviews.gh.prs[combined]["base"] == "main"
    assert reviews.gh.prs[combined]["head"] == resumed_ctx.execution_branch
    second = [r for r in reviews.reviewed if r["slug"] == "add-the-cli-flag"]
    assert len(second) == 2 and second[0]["number"] == second[1]["number"]
    assert Path(child_checkpoint_path(ctx, progress[0]["intent_branch"])).read_bytes() == first_child_checkpoint
    assert any("Stage 3 resume: the plan was already decomposed" in line for line in resumed_ctx.logs)
    execute = resumed["execute"]
    assert execute.status == StageStatus.SUCCESS
    assert [e["status"] for e in execute.payload["children_progress"]] == ["merged", "merged"]
    # The first child merged once only; the resume merged the second.
    assert [call[2] for call in reviews.gh.merge_calls()] == [str(e["pr_number"]) for e in progress]
    assert resumed["review"].status == StageStatus.HALTED_FOR_HUMAN


def test_a_resume_after_a_crash_mid_child_merges_a_reviewed_child_without_rerunning_it(flow_repos, tmp_path):
    host, origin = flow_repos
    ctx, agents = _decomposed_context(host, origin, tmp_path)

    with decomposed_flow(origin, tmp_path) as reviews:
        assert PipelineEngine(ctx).run()["execute"].status == StageStatus.SUCCESS
        # Model a crash after the second child's review but before its merge was recorded.
        checkpoint = load_checkpoint(get_default_checkpoint_path(str(host), "budget-test"))
        context = checkpoint.context
        execute = context.stage_results["execute"]
        execute.status = StageStatus.RUNNING
        second = execute.payload["children_progress"][1]
        second.update(status="review_done", merge_commit=None)
        dispatched_before = list(agents.calls)
        context.dispatcher = agents

        resumed = PipelineEngine(context, from_stage="execute").run()

    assert agents.calls == dispatched_before
    # Origin already shows the PR merged, so reconciliation promotes the child and nothing is merged twice.
    assert len(reviews.gh.merge_calls()) == 2
    assert resumed["execute"].status == StageStatus.SUCCESS
    assert resumed["execute"].payload["children_progress"][1]["status"] == "merged"


class TestChildLoopWithStubbedPipelines:
    """The child loop of ``_run_decomposed_plan`` with each child pipeline, the merge and the integration stubbed."""

    _READY: ClassVar[dict[str, StageResult]] = {
        "intent": StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS),
        "review": halted_review(7, "https://github.com/o/r/pull/7"),
        "calibrate": StageResult(
            stage=FlowStage.CALIBRATE, status=StageStatus.SKIPPED, payload={"skipped_by_flag": True}
        ),
    }

    @classmethod
    def _run_children(
        cls,
        intent_data: dict[str, Any],
        row: dict[str, Any],
        child_results: dict[str, StageResult] | None = None,
        reconcile: Mock | None = None,
        **ctx_kw,
    ) -> tuple[StageResult, FlowContext, list[FlowContext]]:
        """Run _run_decomposed_plan with each child pipeline, the merge, the integration and origin stubbed."""
        ctx = FlowContext(token_reduce=False, intent_data=intent_data, **ctx_kw)
        children: list[FlowContext] = []
        done = child_results or cls._READY

        def fake_run(engine: PipelineEngine) -> dict[str, StageResult]:
            children.append(engine.context)
            return done

        def integrated(context: FlowContext, start_time: str, payload: dict[str, Any]) -> StageResult:
            result = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload=payload)
            context.stage_results["execute"] = result
            return result

        base = {"execution_id": "E-1", "execution_branch": "I-1-parent/P-2-agent-model/E-3-agent-model/_"}
        with (
            patch.object(PipelineEngine, "run", autospec=True, side_effect=fake_run),
            patch("sandbox_executor.flow.stages.children.merge_child_pr", return_value=MergeResult(True, "m1")),
            patch("sandbox_executor.flow.stages.children.integrate_children", side_effect=integrated),
            patch("sandbox_executor.flow.stages.children.save_checkpoint"),
            patch("sandbox_executor.flow.stages.children._fetch_refspecs_error", return_value=None),
            # Origin reconciliation fetches from the real origin, which is unreachable in the review-loop container.
            patch("sandbox_executor.flow.stages.children.reconcile_with_origin", reconcile or Mock(return_value=[])),
        ):
            res = flow_module._run_decomposed_plan(ctx, "2026-10-26T00:00:00Z", row, base)
        return res, ctx, children

    _ROW: ClassVar[dict[str, Any]] = {
        "status": "decomposed",
        "plan_branch": "I-1-parent/P-2-agent-model/_",
        "entropy_budget": 5.0,
        "sub_intents": [{"slug": "first", "description": "Add the parser\n- with tests", "goal": "Add the parser"}],
    }

    def test_child_at_exactly_the_depth_cap_runs(self):
        res, ctx, children = self._run_children(
            {"slug": "parent", "sub_intent_depth": MAX_SUB_INTENT_DEPTH - 1},
            self._ROW,
            intent_branch="I-1-parent/_",
            plan_branch="I-1-parent/P-2-agent-model/_",
        )

        assert res.status == StageStatus.SUCCESS
        assert [c.intent_data["sub_intent_depth"] for c in children] == [MAX_SUB_INTENT_DEPTH]
        assert any(
            line.endswith("] Stage 3: all 1 sub-intent(s) merged into 'I-1-parent/P-2-agent-model/_'.")
            for line in ctx.logs
        )

    def test_child_one_deeper_than_the_cap_is_refused(self):
        res, _ctx, children = self._run_children(
            {"slug": "parent", "sub_intent_depth": MAX_SUB_INTENT_DEPTH}, self._ROW, intent_branch="I-1-parent/_"
        )

        assert res.status == StageStatus.FAILED
        assert "deeper than the limit" in (res.error or "")
        assert children == []

    # --- Child intent payloads ----------------------------------------------------------------------------------

    def test_child_payload_fallbacks_and_copies(self):
        labels = ["flow"]
        row = {
            "status": "decomposed",
            "plan_branch": "I-1-parent/P-2-agent-model/_",
            "sub_intents": [{"goal": "Add the parser"}, {"description": "Add the flag\n- detail"}, "not-an-item"],
        }
        with patch("sandbox_executor.flow.stages.children.reconcile_with_origin", return_value=[]):
            res, ctx, children = self._run_children({"slug": "parent", "labels": labels}, row)

        assert res.status == StageStatus.SUCCESS
        # A context without plan_branch (a checkpoint) still nests the children under the recorded plan branch.
        plan = "I-1-parent/P-2-agent-model/_"
        assert res.payload["plan_branch"] == plan
        assert [c.intent_data["target_branch"] for c in children] == [plan] * 2
        assert [c.intent_data["parent_intent_id"] for c in children] == [plan] * 2
        assert [c.intent_data["branch"] for c in children] == [
            "I-1-parent/P-2-agent-model--1-sub-intent-1",
            "I-1-parent/P-2-agent-model--2-sub-intent-2",
        ]
        first, second = (c.intent_data for c in children)
        assert (first["slug"], first["description"], first["goal"]) == (
            "sub-intent-1",
            "Add the parser",
            "Add the parser",
        )
        assert (second["slug"], second["goal"]) == ("sub-intent-2", "Add the flag\n- detail")
        assert second["description"] == "Add the flag\n- detail"
        # A row without entropy_budget hands no budget down, so each child resolves its own.
        assert "entropy_budget" not in first
        # Labels are copied, not shared, so a child cannot change its parent's labels.
        assert first["labels"] == labels and first["labels"] is not labels
        assert res.payload["sub_intents"] == row["sub_intents"][:2]
        assert ctx.stage_results["execute"] is res
        assert any(line.endswith("] Stage 3: running sub-intent 1 'sub-intent-1'.") for line in ctx.logs)
        assert [e["status"] for e in res.payload["children_progress"]] == ["merged", "merged"]

    def test_child_payload_prefers_the_context_plan_branch_and_hands_down_the_budget(self):
        row = {k: v for k, v in self._ROW.items() if k != "plan_branch"}
        res, _ctx, children = self._run_children(
            {"slug": "parent", "labels": "flow"}, row, intent_branch="I-9-parent/_", plan_branch="I-9-parent/P-3-a-m/_"
        )

        assert res.payload["plan_branch"] == "I-9-parent/P-3-a-m/_"
        (child,) = (c.intent_data for c in children)
        # Never the intent branch: the plan branch is both the parent and the merge target.
        assert child["target_branch"] == child["parent_intent_id"] == "I-9-parent/P-3-a-m/_"
        assert child["entropy_budget"] == 5.0
        assert (child["description"], child["goal"]) == ("Add the parser\n- with tests", "Add the parser")
        # Labels that are not a list are not handed down.
        assert "labels" not in child

    def test_without_a_known_plan_branch_no_child_runs_and_the_intent_branch_is_never_the_fallback(self):
        row = {k: v for k, v in self._ROW.items() if k != "plan_branch"}
        res, _ctx, children = self._run_children({"slug": "parent"}, row, intent_branch="I-9-parent/_")

        assert res.status == StageStatus.FAILED
        assert res.error == (
            "Execute stage failed: no plan branch is known, so the sub-intents cannot be nested under it."
        )
        assert children == []

    def test_a_child_that_is_not_ready_to_merge_or_is_refused_fails_the_stage_naming_it(self):
        skipped = {**self._READY, "review": StageResult(stage=FlowStage.REVIEW, status=StageStatus.SKIPPED)}
        res, _ctx, _children = self._run_children(
            {"slug": "parent"}, self._ROW, child_results=skipped, plan_branch=self._ROW["plan_branch"]
        )
        assert res.error == (
            "Execute stage failed: sub-intent 1/1 'first' is not ready to merge into 'I-1-parent/P-2-agent-model/_': "
            "its Stage 4 did not halt at consensus (status skipped)"
        )
        assert res.payload["children_progress"][0]["status"] == "failed"

        refused = MergeResult(False, reason="the PR base 'main' is main, master or the repository default branch")
        with patch("sandbox_executor.flow.stages.children.merge_child_pr", return_value=refused) as merge:
            ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, plan_branch=self._ROW["plan_branch"])
            with (
                patch.object(PipelineEngine, "run", return_value=self._READY),
                patch("sandbox_executor.flow.stages.children.save_checkpoint"),
                patch("sandbox_executor.flow.stages.children._fetch_refspecs_error", return_value=None),
                patch("sandbox_executor.flow.stages.children.reconcile_with_origin", return_value=[]),
            ):
                res = flow_module._run_decomposed_plan(ctx, "2026-10-26T00:00:00Z", self._ROW, {"execution_id": "E-1"})
        # No execution branch was recorded by the stubbed child, so the expected head is empty and fails closed.
        merge.assert_called_once_with(".", 7, "I-1-parent/P-2-agent-model/_", expected_head="")
        assert res.payload["children_progress"][0]["status"] == "review_done"
        assert res.status == StageStatus.FAILED
        assert res.error == (
            "Execute stage failed: sub-intent 1/1 'first' could not be merged into 'I-1-parent/P-2-agent-model/_' "
            "(PR #7): the PR base 'main' is main, master or the repository default branch"
        )

    @pytest.mark.parametrize("execute_failed_too", [False, True], ids=["plan-only", "plan-and-execute"])
    def test_failed_child_names_its_own_error_and_final_stage(self, execute_failed_too):
        failed = {
            "intent": StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS),
            "plan": StageResult(stage=FlowStage.PLAN, status=StageStatus.FAILED, error="Plan stage failed: no plan"),
        }
        if execute_failed_too:
            # A child whose re-plan Stage 2 failed holds a failed plan and a failed execute: the first one is named.
            failed["execute"] = StageResult(
                stage=FlowStage.EXECUTE, status=StageStatus.FAILED, error="Execute stage failed: over budget"
            )
        res, _ctx, _children = self._run_children(
            {"slug": "parent"}, self._ROW, child_results=failed, intent_branch="I-1-parent/_"
        )

        assert res.error == (
            "Execute stage failed: sub-intent 1/1 'first' failed at stage 'plan': Plan stage failed: no plan"
        )
        (child,) = res.payload["children"]
        assert child["final_stage"] == ("execute" if execute_failed_too else "plan")
        assert child["status"] == "failed"

    @pytest.mark.parametrize("sub_intents", [5, "abc"], ids=["number", "string"])
    def test_decomposed_row_with_a_non_list_sub_intent_value_fails_stage(self, sub_intents):
        row = {"status": "decomposed", "plan_branch": "I-1-parent/P-2-agent-model/_", "sub_intents": sub_intents}
        res, _ctx, children = self._run_children({"slug": "parent"}, row, intent_branch="I-1-parent/_")

        assert res.status == StageStatus.FAILED
        assert "without any sub-intent list items" in (res.error or "")
        assert children == []

    def test_decomposed_summary_names_the_parent_intents_own_target_branch(self):
        # The combined PR targets the parent intent's target_branch, not always main.
        res, _ctx, _children = self._run_children(
            {"slug": "parent", "goal": "Change the application", "target_branch": "release/1.x"},
            self._ROW,
            intent_branch="I-1-parent/_",
        )

        assert res.status == StageStatus.SUCCESS
        assert res.payload["parent_target_branch"] == "release/1.x"
        assert flow_module.format_decomposition_summary(res)[-1] == (
            "Combined PR: Stage 4 raises and reviews 'I-1-parent/P-2-agent-model/E-3-agent-model/_' (with "
            "'I-1-parent/P-2-agent-model/_' merged in) into 'release/1.x'."
        )

    def test_child_logs_reach_the_parent_and_progress_survives_when_a_child_pipeline_raises(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, intent_branch="I-1-parent/_")

        def boom(engine: PipelineEngine) -> dict[str, StageResult]:
            engine.context.log("child progress before the crash")
            raise RuntimeError("child crashed")

        with (
            patch.object(PipelineEngine, "run", autospec=True, side_effect=boom),
            patch("sandbox_executor.flow.stages.children._fetch_refspecs_error", return_value=None),
            patch("sandbox_executor.flow.stages.children.reconcile_with_origin", return_value=[]),
            patch("sandbox_executor.flow.stages.children.save_checkpoint") as save,
        ):
            res = flow_module._run_decomposed_plan(ctx, "2026-10-26T00:00:00Z", self._ROW, {"execution_id": "E-1"})

        assert any(
            line.startswith("[sub-intent first] ") and line.endswith("child progress before the crash")
            for line in ctx.logs
        )
        # An unexpected exception is a Stage 3 failure that still carries the recorded progress (so a resume continues
        # the child instead of dispatching the parent executor and every child again), and the checkpoint was saved.
        assert res.status == StageStatus.FAILED
        assert "raised RuntimeError: child crashed" in (res.error or "")
        assert [e["status"] for e in res.payload["children_progress"]] == ["in_progress"]
        assert res.payload["decomposed"] is True
        save.assert_called()

    def test_a_fresh_stage_3_reconciles_with_origin_so_a_child_with_a_merged_pr_is_not_dispatched(self):
        row = {**self._ROW, "sub_intents": [{"slug": "first", "goal": "One"}, {"slug": "second", "goal": "Two"}]}

        def origin_has_child_one(_repo: str, progress: list[dict[str, Any]], _plan: str) -> list[str]:
            progress[0].update(status="merged", execution_branch="I-1/E-1/_", pr_number=5, merge_commit="m0")
            return []

        reconcile = Mock(side_effect=origin_has_child_one)
        res, _ctx, children = self._run_children(
            {"slug": "parent"}, row, intent_branch="I-1-parent/_", reconcile=reconcile
        )

        reconcile.assert_called_once()
        assert res.status == StageStatus.SUCCESS
        assert [c.intent_data["slug"] for c in children] == ["second"]
        assert [e["status"] for e in res.payload["children_progress"]] == ["merged", "merged"]

    def test_skip_review_refuses_a_decomposed_plan_before_any_child_is_dispatched(self):
        res, _ctx, children = self._run_children(
            {"slug": "parent"}, self._ROW, intent_branch="I-1-parent/_", skip_review=True
        )

        assert res.status == StageStatus.FAILED
        assert "--skip-review skips each sub-intent's Stage 4" in (res.error or "")
        assert children == []
        # The recorded decomposition survives for a resume without the flag.
        assert [e["status"] for e in res.payload["children_progress"]] == ["pending"]

    def test_skip_review_refusal_does_no_origin_lookup_and_keeps_its_message(self):
        reconcile = Mock(side_effect=RuntimeError("origin down"))
        res, _ctx, children = self._run_children(
            {"slug": "parent"}, self._ROW, intent_branch="I-1-parent/_", skip_review=True, reconcile=reconcile
        )

        reconcile.assert_not_called()
        assert res.status == StageStatus.FAILED
        assert "--skip-review skips each sub-intent's Stage 4" in (res.error or "")
        assert "origin down" not in (res.error or "")
        assert children == []

    def test_a_failing_checkpoint_save_in_the_exception_path_keeps_the_original_failure(self):
        with (
            patch(
                "sandbox_executor.flow.stages.children.reconcile_with_origin", side_effect=RuntimeError("origin down")
            ),
            patch("sandbox_executor.flow.stages.children.save_checkpoint", side_effect=OSError("disk full")),
        ):
            ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, intent_branch="I-1-parent/_")
            res = flow_module._run_decomposed_plan(ctx, "2026-10-26T00:00:00Z", self._ROW, {"execution_id": "E-1"})

        assert res.status == StageStatus.FAILED
        assert "raised RuntimeError: origin down" in (res.error or "")
        assert "could not be saved either: OSError: disk full" in (res.error or "")
        assert [e["status"] for e in res.payload["children_progress"]] == ["pending"]

    @pytest.mark.parametrize("flag", ["dry_run", "skip_push"])
    def test_dry_run_and_skip_push_fail_at_the_first_child_and_start_no_later_one(self, flag):
        row = {**self._ROW, "sub_intents": [{"slug": "first", "goal": "One"}, {"slug": "second", "goal": "Two"}]}
        # Without a real push there is no PR and no consensus approval: Stage 4 records neither.
        no_pr = {
            **self._READY,
            "review": StageResult(stage=FlowStage.REVIEW, status=StageStatus.SUCCESS, payload={}),
        }
        res, _ctx, children = self._run_children(
            {"slug": "parent"}, row, child_results=no_pr, intent_branch="I-1-parent/_", **{flag: True}
        )

        assert res.status == StageStatus.FAILED
        assert "sub-intent 1/2 'first' is not ready to merge" in (res.error or "")
        assert "its Stage 4 did not halt at consensus" in (res.error or "")
        assert [c.intent_data["slug"] for c in children] == ["first"]
        assert getattr(children[0], flag) is True
        assert [e["status"] for e in res.payload["children_progress"]] == ["failed", "pending"]


def test_a_child_promoted_to_merged_on_resume_is_summarised_as_merged():
    entry = {"index": 1, "slug": "first", "intent_branch": "I-1-parent--first/_", "status": "review_done"}
    payload = {
        "decomposed": True,
        "plan_branch": "I-1-parent/P-2-agent-model/_",
        "children_progress": [entry],
        "children": [{"index": 1, "slug": "first", "status": "halted_for_human"}],
    }

    def promote(_repo: str, progress: list[dict[str, Any]], *_args: Any) -> list[str]:
        progress[0].update(status="merged", merge_commit="m9", pr_number=7)
        return []

    def integrated(context: FlowContext, start_time: str, payload: dict[str, Any]) -> StageResult:
        return StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload=payload)

    stages = "sandbox_executor.flow.stages.children"
    with (
        patch(f"{stages}.reconcile_with_origin", side_effect=promote),
        patch(f"{stages}.integrate_children", side_effect=integrated),
        patch(f"{stages}.merge_child_pr") as merge,
    ):
        res = resume_children(
            FlowContext(token_reduce=False, intent_data={"slug": "parent"}),
            StageResult(stage=FlowStage.EXECUTE, status=StageStatus.RUNNING, payload=payload),
        )

    merge.assert_not_called()
    assert res.payload["children"] == [
        {"index": 1, "slug": "first", "status": "halted_for_human", "merged": True, "merge_commit": "m9"}
    ]


@pytest.mark.parametrize("original", ["a-very-long-original-slug", None])
def test_child_intent_records_original_slug_only_when_shortened(original):
    context = FlowContext(token_reduce=False, intent_data={"slug": "parent"})
    entry = {"index": 1, "slug": "child", "intent_branch": "I-1-parent--1-child/_", "original_slug": original}
    payload = {"plan_branch": "I-1-parent/P-2-a-m/_", "sub_intents": [{"goal": "g"}]}

    child = _build_child_intent(context, payload, entry)

    assert child.get("original_slug") == original
    assert ("original_slug" in child) is (original is not None)
