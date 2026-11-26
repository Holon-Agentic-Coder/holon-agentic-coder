"""Stage-start templates: the host and origin of a flow after its real Stages 1, 2 or 3 ran, copied per test.

A test whose subject is a later stage needs the state the earlier stages leave: their branches and ledger rows on
origin, the host's refs and checkpoint, and the context fields they set. ``StageTemplates.start`` builds that state once
per test run by running the real stage functions with the fake role containers, as a read-only template
(``tests.repo_templates``), and gives each test a private copy with the context rebuilt from the build's own values.

Rules of use, on top of the template rules:

- The build runs production code, so it is only asked for from a function-scoped fixture (``stage_start`` in the
  flow conftest): it runs under the first asking test's autouse guards.
- The build sets its own ``HOME`` and consult ledgers to the template root, so the template does not depend on which
  test asked first.
- Only a test that asserts nothing about the stages the template ran (beyond their success, which
  test_stage_templates.py proves equal to a fresh run) may start from one.
"""

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
# The agent state a later stage reads: which plan the budget planner writes next, and the container clock's skew.
AGENT_STATE = ("planner_calls", "clock_offset")


@dataclass(frozen=True)
class AgentsSpec:
    """The fake role containers and context of a flow, as far as the stages a template runs can see them.

    ``plans`` set makes ``BudgetRoleAgents`` with ``_budget_context(budget)``; otherwise ``FakeRoleAgents`` with
    ``_dispatch_context``. ``intent_extra`` adds intent fields.
    """

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
    """A test's private copy: its host, origin, fresh fake containers and the context the template's stages left."""

    host: Path
    origin: Path
    agents: FakeRoleAgents
    ctx: FlowContext


def _point_consult_at(monkeypatch: pytest.MonkeyPatch, host: Path) -> None:
    """Production runs the flow from the host checkout, so the consult's default ledgers are the host's own."""
    ledger_dir = host / "holon-knowledge" / "ledger"
    monkeypatch.setattr(consult, "DEFAULT_LEDGERS", [str(ledger_dir / Path(path).name) for path in DEFAULT_LEDGERS])


def _run_stages(root: Path, flow_template: Path, through: str, spec: AgentsSpec) -> dict[str, Any]:
    """Build the template in ``root``: a fresh flow copy, then the real stages up to and including ``through``."""
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


def _repointed(text: str, template_root: str, target: Path) -> str:
    """``text`` naming ``target`` wherever it named the template (in either spelling of a symlinked temp dir)."""
    for root in sorted({template_root, os.path.realpath(template_root)}, key=len, reverse=True):
        text = text.replace(root, str(target))
    return text


def _rewrite_paths(tree: Path, template_root: str, target: Path) -> None:
    """Make the flow state files under ``tree`` (checkpoints) name ``target`` wherever they named the template."""
    for path in tree.rglob("*") if tree.is_dir() else ():
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="surrogateescape")
            path.write_text(_repointed(text, template_root, target), encoding="utf-8", errors="surrogateescape")


@dataclass(frozen=True)
class StageTemplates:
    """The stage-start templates of a test run, built over the run's flow template ``flow_template``."""

    tmp_path_factory: pytest.TempPathFactory
    flow_template: Path

    def start(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, through: str, spec: AgentsSpec = DEFAULT_SPEC
    ) -> StageStart:
        """A private copy in ``tmp_path`` of the flow after its real stages ran up to and including ``through``.

        ``through`` is ``intent``, ``plan`` or ``execute``. The context is rebuilt from the values the template's
        stages left, re-pointed at the copy's host and given fresh fake containers, so dispatch assertions see only
        the stages the test itself runs.
        """
        assert through in dict(STAGES), through
        name = template_name("stage-start", through, spec)
        template, built = shared_template(
            self.tmp_path_factory, name, lambda root: _run_stages(root, self.flow_template, through, spec)
        )
        names = tuple(entry for entry in ("origin.git", "host", "home") if (template / entry).exists())
        copy_template(template, tmp_path, names=names)
        host, origin = tmp_path / "host", tmp_path / "origin.git"
        _rewrite_paths(host / ".holon", built["root"], tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        _point_consult_at(monkeypatch, host)
        agents = spec.agents(tmp_path, origin)
        for attr, value in built["agents"].items():
            setattr(agents, attr, value)
        # The saved context (its log lines, for one) names the template's paths, as its checkpoint does.
        ctx = FlowContext.from_dict(json.loads(_repointed(json.dumps(built["context"]), built["root"], tmp_path)))
        ctx.target_worktree = str(host)
        ctx.dispatcher = agents
        return StageStart(host, origin, agents, ctx)
