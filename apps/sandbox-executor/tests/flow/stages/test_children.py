"""End-to-end tests of the decomposed child loop against a bare origin, with fake role agents and a fake ``gh``.

They prove that children nest under the plan branch (goal 8 b), run strictly one after another from its latest tip
(8 c), are merged into it with merge commits only and never into ``main`` (8 d), and that a resume continues from the
recorded progress without re-dispatching the parent executor or any child (8 f).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from sandbox_executor.flow import FlowContext, PipelineEngine, StageStatus
from sandbox_executor.flow.checkpoint import get_default_checkpoint_path, load_checkpoint
from sandbox_executor.flow.child_progress import child_checkpoint_path

from tests.flow.decomposition_fakes import decomposed_flow
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
    assert [e["status"] for e in execute.payload["children_progress"]] == ["failed", "pending"]
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
