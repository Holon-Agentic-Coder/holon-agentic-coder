"""Shared fake-docker routing for launcher tests."""

from types import SimpleNamespace

import pytest
from sandbox_executor.launcher import proxy_paths, run_tracking, sidecar

# Every launcher module that spawns docker owns its own `subprocess` binding, so a fake docker replaces each of them.
# The role container itself is started through `run_tracking.run_tracked`, which `fake_tracked_runs` routes.
DOCKER_MODULES = (proxy_paths, sidecar)


def fake_tracked_runs(monkeypatch: pytest.MonkeyPatch, run) -> None:
    """Route the tracked role-container `docker run` through `run`, keeping its exit-code contract."""
    monkeypatch.setattr(run_tracking, "run_tracked", lambda cmd, env=None: run(cmd, env=env).returncode)


def fake_docker_runs(monkeypatch: pytest.MonkeyPatch, run) -> None:
    """Route every docker invocation of the sidecar and launch code through `run`."""
    fake_subprocess = SimpleNamespace(run=run)
    for module in DOCKER_MODULES:
        monkeypatch.setattr(module, "subprocess", fake_subprocess)
    fake_tracked_runs(monkeypatch, run)
