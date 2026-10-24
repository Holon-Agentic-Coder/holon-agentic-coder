"""Suite-wide isolation from the operator's agent harness, real pre-flight probes and unmocked ``gh`` writes."""

import subprocess
from collections.abc import Iterator

import pytest
from sandbox_executor.agent_defaults import detected_harness_markers

from tests.gh_write_guard import guarded_popen


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


@pytest.fixture(autouse=True)
def gh_write_guard(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Fail any test that runs an unmocked ``gh`` write command (comment, review, create, edit, merge, ...).

    Read-only ``gh`` calls still run. The write is refused before ``gh`` starts and the test fails at teardown, even
    when the code under test swallows the refusal. Yields the list of blocked commands; a test that expects a block
    clears it.
    """
    blocked: list[str] = []
    monkeypatch.setattr(subprocess, "Popen", guarded_popen(blocked))
    yield blocked
    if blocked:
        pytest.fail("Unmocked gh write command(s) ran in this test: " + "; ".join(blocked), pytrace=False)
