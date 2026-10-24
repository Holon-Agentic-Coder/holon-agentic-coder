"""Suite-wide isolation from the operator's agent harness and from real pre-flight probes."""

import pytest
from sandbox_executor.agent_defaults import detected_harness_markers


@pytest.fixture(autouse=True)
def _isolate_agent_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear harness markers and runner overrides, and disable the real readiness probe.

    The suite runs inside Claude Code, pi and other harnesses whose markers would otherwise change the resolved default
    runner. The pre-flight probe makes a real model call through docker, so tests opt back in explicitly by deleting
    HOLON_SKIP_PREFLIGHT and mocking the probe.
    """
    for _runner_id, name in detected_harness_markers():
        monkeypatch.delenv(name, raising=False)
    for name in ("HOLON_AGENT", "HOLON_MODEL", "HOLON_PREFLIGHT_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOLON_SKIP_PREFLIGHT", "1")
