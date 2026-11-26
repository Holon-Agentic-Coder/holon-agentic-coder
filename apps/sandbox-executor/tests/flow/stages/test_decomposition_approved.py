"""A decomposed flow whose Stage 4 returns APPROVED finishes at the merge boundary with children merged."""

from sandbox_executor.flow import PipelineEngine, StageStatus

from tests.flow.decomposition_fakes import decomposed_flow
from tests.flow.helpers import SUB_INTENT_LIST, BudgetRoleAgents, _budget_context


def test_decomposed_flow_completes_when_stage_4_returns_approved(flow_repos, tmp_path):
    """Also carries the assertions of test_integration.py's former APPROVED test, which ran this same scenario."""
    host, origin = flow_repos
    agents = BudgetRoleAgents(tmp_path, origin, plans=[(9.0, SUB_INTENT_LIST), (2.0, "")])
    ctx = _budget_context(host, agents, 5.0)
    ctx.skip_review = False

    with decomposed_flow(origin, tmp_path, review_status=StageStatus.APPROVED) as reviews:
        results = PipelineEngine(ctx).run()

    assert results["execute"].status == StageStatus.SUCCESS
    assert [e["status"] for e in results["execute"].payload["children_progress"]] == ["merged", "merged"]
    assert results["review"].status == StageStatus.APPROVED
    assert results["review"].payload["halted_for_human"] is False
    assert any(
        "Pipeline finished at the merge boundary: review approved, PR ready for maintainer merge" in line
        for line in ctx.logs
    )
    assert any(
        "finished at the merge boundary: review approved, PR ready for maintainer merge (stage 'review')" in line
        for line in ctx.logs
    )
    assert [reviews.gh.prs[n]["state"] for n in (101, 102, 103)] == ["MERGED", "MERGED", "OPEN"]
    # The parent execution branch's own combined PR, the one Stage 4 reviewed last, stays open for the maintainer.
    assert reviews.gh.prs[reviews.reviewed[-1]["number"]]["state"] == "OPEN"
