"""The stage-start templates of ``tests.flow.stage_templates`` give later stages what a fresh run gives them.

For each stage a template can stop after, a fresh run of the real stages and a template copy are compared up to ids,
times, commit shas and paths: the branches on origin and on the host, the host's checkout, every ledger row on origin,
the context fields, and the result of the next stage run from each.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sandbox_executor.flow import FlowContext, StageStatus, run_execute_stage, run_plan_stage, run_review_stage
from sandbox_executor.flow.stages import plan_decision

from tests.flow.helpers import SUB_INTENT_LIST, BudgetRoleAgents, FakeRoleAgents, _git, _ledger_rows
from tests.flow.stage_templates import DEFAULT_SPEC, STAGES, AgentsSpec

_VOLATILE = [
    (re.compile(r"\b([IPE])-\d{9,}"), r"\1-<id>"),
    (re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)?"), "<time>"),
    (re.compile(r"\b[0-9a-f]{40}\b"), "<sha>"),
]
LOOP_RESULT = {"consensus": {"approved": True}, "mergeability": {}, "halted_for_human": False, "approved": True}


def _normalised(value: Any, root: Path) -> Any:
    text = json.dumps(value, sort_keys=True, default=str).replace(str(root), "<root>")
    for pattern, replacement in _VOLATILE:
        text = pattern.sub(replacement, text)
    return json.loads(text)


def _snapshot(host: Path, origin: Path, ctx: FlowContext, root: Path) -> dict[str, Any]:
    """What a later stage can read of the earlier stages' work, with ids, times, shas and paths normalised."""
    origin_branches = _git(origin, "for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines()
    rows = {
        branch: {name: _ledger_rows(origin, f"refs/heads/{branch}", name) for name in ("intents.jsonl", "plans.jsonl")}
        | {"executions.jsonl": _ledger_rows(origin, f"refs/heads/{branch}", "executions.jsonl")}
        for branch in origin_branches
    }
    context = {k: v for k, v in ctx.to_dict().items() if k not in ("target_worktree", "logs")}
    snapshot = {
        "origin_branches": sorted(origin_branches),
        "host_refs": sorted(_git(host, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes").split()),
        "host_head": _git(host, "rev-parse", "--abbrev-ref", "HEAD"),
        "host_status": _git(host, "status", "--porcelain"),
        "rows": rows,
        "context": context,
    }
    return _normalised(snapshot, root)


def _next_stage(ctx: FlowContext, through: str, agents: FakeRoleAgents) -> dict[str, Any]:
    """Run the stage after ``through`` and return what it decided, without the fake containers' earlier calls.

    After a decomposed plan the next step checked is Stage 3's read of the plan row's decision: its full run is a
    whole child flow, which test_children.py runs.
    """
    before = len(agents.calls)
    if isinstance(agents, BudgetRoleAgents) and through == "plan":
        decision = plan_decision._plan_budget_decision(ctx)
        return {"decision": decision.decision if decision else None, "sub_intents": decision and decision.sub_intents}
    if through == "intent":
        result = run_plan_stage(ctx)
    elif through == "plan":
        result = run_execute_stage(ctx)
    else:
        with (
            patch("sandbox_executor.flow.stages.review.at_stage_boundary", return_value=None),
            patch("sandbox_executor.flow.review.loop.run_review_loop", return_value=LOOP_RESULT),
        ):
            result = run_review_stage(ctx)
    return {
        "status": result.status.value,
        "error": result.error,
        "payload_keys": sorted(result.payload),
        "roles": [call["role"] for call in agents.calls[before:]],
    }


DECOMPOSED = AgentsSpec(plans=((9.0, SUB_INTENT_LIST), (2.0, "")))


# Every stage a template stops after, with the dispatch containers; and the budget containers' decomposed plan up to
# Stage 2 (its Stage 3 is a whole child flow, which test_children.py runs).
@pytest.mark.parametrize(
    ("through", "spec"),
    [(name, DEFAULT_SPEC) for name, _run in STAGES] + [("plan", DECOMPOSED)],
    ids=[f"{name}-dispatch" for name, _run in STAGES] + ["plan-budget-decomposed"],
)
def test_a_template_copy_starts_the_next_stage_as_a_fresh_run_does(flow_repos, stage_start, tmp_path, through, spec):
    host, origin = flow_repos
    fresh_agents = spec.agents(tmp_path, origin)
    fresh = spec.context(host, fresh_agents)
    for name, run in STAGES:
        assert run(fresh).status == StageStatus.SUCCESS
        if name == through:
            break
    fresh_state = _snapshot(host, origin, fresh, tmp_path)
    # Not a vacuous comparison: main plus one branch per stage that ran, each carrying that stage's ledger row.
    stages_run = [name for name, _run in STAGES][: [name for name, _run in STAGES].index(through) + 1]
    assert len(fresh_state["origin_branches"]) == 1 + len(stages_run)
    assert set(fresh_state["context"]["stage_results"]) == set(stages_run)
    fresh_next = _next_stage(fresh, through, fresh_agents)

    templated = tmp_path / "templated"
    start = stage_start(through, spec, target=templated)

    assert _snapshot(start.host, start.origin, start.ctx, templated) == fresh_state
    assert start.agents.calls == []
    assert _next_stage(start.ctx, through, start.agents) == fresh_next
    # The copy never names the template: its checkpoints and git config point at the copy only.
    for path in (start.host / ".holon").rglob("*"):
        if path.is_file():
            assert "shared-stage-start" not in path.read_text(encoding="utf-8", errors="replace"), path
