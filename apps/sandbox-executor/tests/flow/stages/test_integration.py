"""Tests of Stage 3 integration: the plan branch is merged into the parent execution branch for the combined PR."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from sandbox_executor.flow import FlowContext, PipelineEngine, StageStatus
from sandbox_executor.flow.stages.integration import integrate_children

from tests.flow.decomposition_fakes import decomposed_flow
from tests.flow.helpers import SUB_INTENT_LIST, BudgetRoleAgents, _budget_context, _git, _ledger_rows


def _run_decomposed(host: Path, origin: Path, tmp_path: Path):
    agents = BudgetRoleAgents(tmp_path, origin, plans=[(9.0, SUB_INTENT_LIST), (2.0, "")])
    ctx = _budget_context(host, agents, 5.0)
    ctx.skip_review = False
    with decomposed_flow(origin, tmp_path) as reviews:
        results = PipelineEngine(ctx).run()
    return ctx, results, reviews


def test_the_parent_execution_branch_carries_every_record_and_stage_4_halts_at_main(flow_repos, tmp_path):
    host, origin = flow_repos
    ctx, results, reviews = _run_decomposed(host, origin, tmp_path)

    execute = results["execute"]
    assert execute.status == StageStatus.SUCCESS and execute.payload["integrated"] is True
    exec_ref = f"refs/heads/{ctx.execution_branch}"
    files = set(_git(origin, "ls-tree", "-r", "--name-only", exec_ref).splitlines())
    # The parent plan file (an ancestor) and both child plan files (merged into the plan branch) are on the head.
    parent_plan_file = f"plans/{ctx.plan_branch.removesuffix('/_').rsplit('/', 1)[-1]}.md"
    child_plan_files = {
        f"plans/{e['execution_branch'].removesuffix('/_').split('/')[-2]}.md"
        for e in execute.payload["children_progress"]
    }
    assert {parent_plan_file, *child_plan_files} <= files
    rows = _ledger_rows(origin, exec_ref, "executions.jsonl")
    parent_rows = [r for r in rows if r.get("execution_id") == execute.payload["execution_id"]]
    # The executor's decomposed row, then the superseding row listing the merged children.
    assert [r.get("integrated", False) for r in parent_rows] == [False, True]
    superseding = parent_rows[-1]
    assert superseding["status"] == "decomposed" and superseding["ledger_revision"] == 2
    assert superseding["plan_branch"] == ctx.plan_branch
    assert [c["slug"] for c in superseding["children"]] == ["add-the-parser", "add-the-cli-flag"]
    assert all(c["merge_commit"] and c["pr_number"] for c in superseding["children"])
    # Both children's own execution rows reached the parent execution branch through the plan branch.
    child_ids = {e["execution_branch"].removesuffix("/_").rsplit("/", 1)[-1] for e in execute.payload["children"]}
    assert child_ids <= {r.get("execution_id") for r in rows}
    # The plan branch is an ancestor of the combined head, and Stage 4 raised the combined PR into main and halted.
    assert _git(origin, "merge-base", f"refs/heads/{ctx.plan_branch}", exec_ref) == _git(
        origin, "rev-parse", f"refs/heads/{ctx.plan_branch}"
    )
    assert reviews.reviewed[-1] == {
        "slug": "budget-test",
        "head": ctx.execution_branch,
        "base": "main",
        "number": reviews.reviewed[-1]["number"],
    }
    assert results["review"].status == StageStatus.HALTED_FOR_HUMAN
    assert any("Pipeline safely halted for human intervention at stage 'review'" in line for line in ctx.logs)
    assert reviews.gh.prs[reviews.reviewed[-1]["number"]]["state"] == "OPEN"


def test_integration_is_idempotent_on_a_resume(flow_repos, tmp_path):
    host, origin = flow_repos
    ctx, results, _reviews = _run_decomposed(host, origin, tmp_path)
    exec_ref = f"refs/heads/{ctx.execution_branch}"
    head_before = _git(origin, "rev-parse", exec_ref)

    again = integrate_children(ctx, "2026-11-09T00:00:00Z", dict(results["execute"].payload))

    assert again.status == StageStatus.SUCCESS
    assert _git(origin, "rev-parse", exec_ref) == head_before
    rows = _ledger_rows(origin, exec_ref, "executions.jsonl")
    assert sum(bool(r.get("integrated")) for r in rows) == 1


def test_a_failed_integration_merge_fails_stage_3_with_the_reason(tmp_path):
    ctx = FlowContext(repo_dir=str(tmp_path), token_reduce=False, intent_data={"slug": "parent"})
    payload = {
        "decomposed": True,
        "plan_branch": "I-1-p/P-2-a-m/_",
        "execution_branch": "I-1-p/P-2-a-m/E-3-a-m/_",
        "children_progress": [{"slug": "a"}],
    }
    with patch(
        "sandbox_executor.flow.stages.integration.merge_into_branch",
        return_value=(None, "merging conflicts outside holon-knowledge/ledger/*.jsonl"),
    ):
        res = integrate_children(ctx, "2026-11-09T00:00:00Z", payload)

    assert res.status == StageStatus.FAILED
    assert res.error == (
        "Execute stage failed: integrating 'I-1-p/P-2-a-m/_' into 'I-1-p/P-2-a-m/E-3-a-m/_' failed: merging conflicts "
        "outside holon-knowledge/ledger/*.jsonl"
    )
    assert json.loads(json.dumps(res.payload))["plan_branch"] == "I-1-p/P-2-a-m/_"


def test_integration_without_a_parent_execution_branch_fails(tmp_path):
    ctx = FlowContext(repo_dir=str(tmp_path), token_reduce=False, intent_data={"slug": "parent"})
    res = integrate_children(ctx, "2026-11-09T00:00:00Z", {"plan_branch": "P/_", "children_progress": []})
    assert res.status == StageStatus.FAILED
    assert res.error == "Execute stage failed: no parent execution branch to integrate."
    # The recorded decomposition survives, so a resume continues the children instead of re-dispatching the executor.
    assert res.payload == {"plan_branch": "P/_", "children_progress": []}
