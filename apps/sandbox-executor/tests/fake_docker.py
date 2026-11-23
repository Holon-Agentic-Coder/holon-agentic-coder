"""Shared fake-docker routing for launcher tests."""

from types import SimpleNamespace

import pytest
from sandbox_executor.launcher import containers, proxy_paths, sidecar

# Every launcher module that spawns docker owns its own `subprocess` binding, so a fake docker replaces each of them.
DOCKER_MODULES = (containers, proxy_paths, sidecar)


def fake_docker_runs(monkeypatch: pytest.MonkeyPatch, run) -> None:
    """Route every docker invocation of the sidecar and launch code through `run`."""
    fake_subprocess = SimpleNamespace(run=run)
    for module in DOCKER_MODULES:
        monkeypatch.setattr(module, "subprocess", fake_subprocess)
