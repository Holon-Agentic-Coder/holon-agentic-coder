"""The stage-start templates of ``tests.flow.stage_templates`` give later stages what a fresh run gives them.

One fresh run of the real stages is snapshotted after each stage and compared with a copy of the template that stops
there, up to ids, times, commit shas and paths: the branches on origin and on the host, the host's checkout and status,
every ledger file on every origin branch, and the context fields. The stage after it, run from the copy, must decide
what the fresh run's own next stage decided.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

from sandbox_executor.flow import FlowContext, StageResult, StageStatus, run_review_stage
from sandbox_executor.flow.stages import plan_decision

from tests.flow.helpers import SUB_INTENT_LIST, FakeRoleAgents, _git
from tests.flow.stage_templates import DEFAULT_SPEC, STAGES, AgentsSpec

_VOLATILE = [
    (re.compile(r"\b([IPE])-\d{9,}"), r"\1-<id>"),
    (re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)?"), "<time>"),
    (re.compile(r"\b[0-9a-f]{40}\b"), "<sha>"),
]
LEDGER_DIR = "holon-knowledge/ledger"
LOOP_RESULT = {"consensus": {"approved": True}, "mergeability": {}, "halted_for_human": False, "approved": True}


def _normalised(value: Any, root: Path) -> Any:
    text = json.dumps(value, sort_keys=True, default=str).replace(str(root), "<root>")
    for pattern, replacement in _VOLATILE:
        text = pattern.sub(replacement, text)
    return json.loads(text)


def _ledger_files(origin: Path, branches: list[str]) -> dict[str, dict[str, str]]:
    """Every file under holon-knowledge/ledger on each origin branch, read with one ``git cat-file --batch``."""
    listed = {
        branch: [
            line.split(None, 3)[2:]
            for line in _git(origin, "ls-tree", "-r", branch, "holon-knowledge/ledger").splitlines()
        ]
        for branch in branches
    }
    blobs = sorted({sha for entries in listed.values() for sha, _path in entries})
    batch = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=origin,
        input="".join(f"{sha}\n" for sha in blobs).encode(),
        check=True,
        capture_output=True,
    ).stdout
    contents: dict[str, str] = {}
    for sha in blobs:
        header, _, batch = batch.partition(b"\n")
        size = int(header.split()[2])
        contents[sha], batch = batch[:size].decode(), batch[size + 1 :]
    return {branch: {path: contents[sha] for sha, path in entries} for branch, entries in listed.items()}


def _snapshot(host: Path, origin: Path, ctx: FlowContext, root: Path) -> dict[str, Any]:
    """What a later stage can read of the earlier stages' work, with ids, times, shas and paths normalised."""
    origin_branches = sorted(_git(origin, "for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines())
    context = {k: v for k, v in ctx.to_dict().items() if k not in ("target_worktree", "logs")}
    snapshot = {
        "origin_branches": origin_branches,
        "host_refs": sorted(_git(host, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes").split()),
        "host_head": _git(host, "rev-parse", "--abbrev-ref", "HEAD"),
        "host_status": _git(host, "status", "--porcelain"),
        "ledgers": _ledger_files(origin, origin_branches),
        "context": context,
    }
    return _normalised(snapshot, root)


def _outcome(result: StageResult, calls: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": result.status.value,
        "error": result.error,
        "payload_keys": sorted(result.payload),
        "roles": [call["role"] for call in calls],
    }


def _review(ctx: FlowContext) -> StageResult:
    """Stage 4 with its loop and boundary sync stubbed: what it checks out and reports from the copied state."""
    with (
        patch("sandbox_executor.flow.stages.review.at_stage_boundary", return_value=None),
        patch("sandbox_executor.flow.review.loop.run_review_loop", return_value=LOOP_RESULT),
    ):
        return run_review_stage(ctx)


def _next_stage(ctx: FlowContext, through: str, agents: FakeRoleAgents) -> dict[str, Any]:
    """Run the stage after ``through`` from ``ctx`` and return what it decided."""
    following = dict(zip([name for name, _run in STAGES], [run for _name, run in STAGES[1:]] + [_review], strict=True))
    before = len(agents.calls)
    result = following[through](ctx)
    return _outcome(result, agents.calls[before:])


def test_each_template_copy_equals_the_fresh_run_at_its_stage_and_runs_the_next_stage_alike(
    flow_repos, stage_start, tmp_path
):
    host, origin = flow_repos
    agents = DEFAULT_SPEC.agents(tmp_path, origin)
    fresh = DEFAULT_SPEC.context(host, agents)
    states: dict[str, dict[str, Any]] = {}
    outcomes: dict[str, dict[str, Any]] = {}
    for name, run in STAGES:
        before = len(agents.calls)
        result = run(fresh)
        assert result.status == StageStatus.SUCCESS
        if states:
            outcomes[list(states)[-1]] = _outcome(result, agents.calls[before:])
        states[name] = _snapshot(host, origin, fresh, tmp_path)
    outcomes["execute"] = _outcome(_review(fresh), [])

    for index, (name, _run) in enumerate(STAGES):
        # Not a vacuous comparison: main plus one branch per stage that ran, each with that stage's results.
        assert len(states[name]["origin_branches"]) == 2 + index
        assert sorted(states[name]["context"]["stage_results"]) == sorted(stage for stage, _ in STAGES[: index + 1])
        templated = tmp_path / f"templated-{name}"
        start = stage_start(name, target=templated)

        assert _snapshot(start.host, start.origin, start.ctx, templated) == states[name]
        assert start.agents.calls == []
        assert _next_stage(start.ctx, name, start.agents) == outcomes[name]
        # The copy never names the template: its checkpoints point at the copy only.
        for path in (start.host / ".holon").rglob("*"):
            if path.is_file():
                assert "shared-stage-start" not in path.read_text(encoding="utf-8", errors="replace"), path


# The spec test_plan_decision.py's recorded-decomposed test uses, so the check shares its template build.
DECOMPOSED = AgentsSpec(plans=((9.0, SUB_INTENT_LIST),))


def test_a_budget_plan_template_carries_the_decision_stage_3_reads(flow_repos, stage_start, tmp_path):
    """The budget containers' decomposed plan up to Stage 2; its Stage 3 is a whole child flow (test_children.py), so
    the next step compared is Stage 3's read of the plan row's decision."""
    host, origin = flow_repos
    agents = DECOMPOSED.agents(tmp_path, origin)
    fresh = DECOMPOSED.context(host, agents)
    for _name, run in STAGES[:2]:
        assert run(fresh).status == StageStatus.SUCCESS
    state = _snapshot(host, origin, fresh, tmp_path)
    decision = plan_decision._plan_budget_decision(fresh)
    assert decision is not None and decision.decision == "decomposed"

    templated = tmp_path / "templated"
    start = stage_start("plan", DECOMPOSED, target=templated)

    assert _snapshot(start.host, start.origin, start.ctx, templated) == state
    assert plan_decision._plan_budget_decision(start.ctx) == decision
