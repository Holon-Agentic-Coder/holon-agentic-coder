from __future__ import annotations

from collections.abc import Mapping

from sandbox_executor import local_llm
from sandbox_executor.runners.antigravity import SPEC as ANTIGRAVITY
from sandbox_executor.runners.claude import SPEC as CLAUDE
from sandbox_executor.runners.codex import SPEC as CODEX
from sandbox_executor.runners.opencode import SPEC as OPENCODE
from sandbox_executor.runners.pi import SPEC as PI
from sandbox_executor.runners.spec import RunnerSpec

__all__ = ["RUNNER_SPECS", "RunnerSpec", "runner_spec_for"]

RUNNER_SPECS: Mapping[str, RunnerSpec] = {
    spec.agent_id: spec
    for spec in (
        ANTIGRAVITY,
        CLAUDE,
        PI,
        CODEX,
        OPENCODE,
    )
}


def runner_spec_for(agent: str) -> RunnerSpec | None:
    return RUNNER_SPECS.get(local_llm.normalize_agent_id(agent))
