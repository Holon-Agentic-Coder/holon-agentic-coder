from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from sandbox_executor.flow import FlowContext, StageStatus, run_execute_stage, run_intent_stage, run_plan_stage
from sandbox_executor.knowledge import consult
from sandbox_executor.knowledge.consult import DEFAULT_LEDGERS

from tests.flow.helpers import BudgetRoleAgents, FakeRoleAgents, _budget_context, _dispatch_context
from tests.flow_repo_templates import template_name
from tests.repo_templates import copy_template, shared_template

STAGES: tuple[tuple[str, Callable[[FlowContext], Any]], ...] = (
    ("intent", run_intent_stage),
    ("plan", run_plan_stage),
    ("execute", run_execute_stage),
)
# why: a later stage reads these agent attributes (the budget planner's next plan, the container clock's skew).
AGENT_STATE = ("planner_calls", "clock_offset")


@dataclass(frozen=True)
class AgentsSpec:
    plans: tuple[tuple[float, str], ...] = ()
    budget: float = 5.0
    planner_records_decision: bool = True
    executor_mode: str = "change"
    recorded_test_pass_rate: Any = None
    intent_extra: tuple[tuple[str, Any], ...] = field(default=())

    def agents(self, root: Path, origin: Path) -> FakeRoleAgents:
        if self.plans:
            agents: FakeRoleAgents = BudgetRoleAgents(root, origin, plans=list(self.plans))
        else:
            agents = FakeRoleAgents(root, origin)
        agents.planner_records_decision = self.planner_records_decision
        agents.executor_mode = self.executor_mode
        agents.recorded_test_pass_rate = self.recorded_test_pass_rate
        return agents

    def context(self, host: Path, agents: FakeRoleAgents) -> FlowContext:
        if self.plans:
            return _budget_context(host, agents, self.budget, **dict(self.intent_extra))
        if self.intent_extra:
            intent = {**_dispatch_context(host, agents).intent_data, **dict(self.intent_extra)}
            return _dispatch_context(host, agents, intent_data=intent)
        return _dispatch_context(host, agents)


DEFAULT_SPEC = AgentsSpec()


@dataclass
class StageStart:
    host: Path
    origin: Path
    agents: FakeRoleAgents
    ctx: FlowContext


# why: production runs the flow from the host checkout, so the consult's default ledgers are the host's own.
def _point_consult_at(monkeypatch: pytest.MonkeyPatch, host: Path) -> None:
    ledger_dir = host / "holon-knowledge" / "ledger"
    monkeypatch.setattr(consult, "DEFAULT_LEDGERS", [str(ledger_dir / Path(path).name) for path in DEFAULT_LEDGERS])


def _run_real_stages_through(root: Path, flow_template: Path, through: str, spec: AgentsSpec) -> dict[str, Any]:
    copy_template(flow_template, root, names=("origin.git", "host"))
    host, origin = root / "host", root / "origin.git"
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("HOME", str(root / "home"))
        _point_consult_at(monkeypatch, host)
        agents = spec.agents(root, origin)
        ctx = spec.context(host, agents)
        for name, run in STAGES:
            result = run(ctx)
            assert result.status == StageStatus.SUCCESS, f"template stage {name} failed: {result.error}"
            if name == through:
                break
    return {
        "root": str(root),
        "context": ctx.to_dict(),
        "agents": {name: getattr(agents, name, 0) for name in AGENT_STATE},
    }


def _with_template_paths_replaced(text: str, template_root: str, target: Path) -> str:
    # why: a symlinked temp dir has two spellings; the longer one is replaced first so it is never half-replaced.
    for root in sorted({template_root, os.path.realpath(template_root)}, key=len, reverse=True):
        text = text.replace(root, str(target))
    return text


def _replace_template_paths_in_flow_state(tree: Path, template_root: str, target: Path) -> None:
    for path in tree.rglob("*") if tree.is_dir() else ():
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="surrogateescape")
            path.write_text(
                _with_template_paths_replaced(text, template_root, target), encoding="utf-8", errors="surrogateescape"
            )


# why: only a test asserting nothing about the template's stages beyond their success may start from one.
@dataclass(frozen=True)
class StageTemplates:
    tmp_path_factory: pytest.TempPathFactory
    flow_template: Path

    def start(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, through: str, spec: AgentsSpec = DEFAULT_SPEC
    ) -> StageStart:
        assert through in dict(STAGES), through
        name = template_name("stage-start", through, spec)
        template, built = shared_template(
            self.tmp_path_factory, name, lambda root: _run_real_stages_through(root, self.flow_template, through, spec)
        )
        names = tuple(entry for entry in ("origin.git", "host", "home") if (template / entry).exists())
        copy_template(template, tmp_path, names=names)
        host, origin = tmp_path / "host", tmp_path / "origin.git"
        _replace_template_paths_in_flow_state(host / ".holon", built["root"], tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        _point_consult_at(monkeypatch, host)
        agents = spec.agents(tmp_path, origin)
        for attr, value in built["agents"].items():
            setattr(agents, attr, value)
        # why: the saved context (its log lines, for one) names the template's paths, as its checkpoint does.
        ctx = FlowContext.from_dict(
            json.loads(_with_template_paths_replaced(json.dumps(built["context"]), built["root"], tmp_path))
        )
        ctx.target_worktree = str(host)
        ctx.dispatcher = agents
        return StageStart(host, origin, agents, ctx)
