"""The Stage 4 token-reduction proxy environment: the switch, the resolved value and both log lines."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from sandbox_executor.flow import FlowContext
from sandbox_executor.flow.review.proxy_env import review_proxy_env

RESOLVER = "sandbox_executor.launcher.sidecar.get_host_proxy_envs"


def _ctx(tmp_path: Path, token_reduce: bool) -> FlowContext:
    return FlowContext(repo_dir=str(tmp_path), token_reduce=token_reduce)


def test_token_reduction_off_is_empty_without_resolving(tmp_path: Path) -> None:
    context = _ctx(tmp_path, token_reduce=False)
    with patch(RESOLVER) as resolver:
        assert review_proxy_env(context) == {}
    resolver.assert_not_called()
    assert context.logs == []


def test_a_resolved_environment_is_returned_and_logged_as_active(tmp_path: Path) -> None:
    context = _ctx(tmp_path, token_reduce=True)
    with patch(RESOLVER, return_value={"HTTPS_PROXY": "http://p"}) as resolver:
        assert review_proxy_env(context) == {"HTTPS_PROXY": "http://p"}
    resolver.assert_called_once_with()
    assert [log.split("] ", 1)[1] for log in context.logs] == ["Token reduction proxy active for review loop."]


def test_an_empty_environment_logs_nothing(tmp_path: Path) -> None:
    context = _ctx(tmp_path, token_reduce=True)
    with patch(RESOLVER, return_value={}):
        assert review_proxy_env(context) == {}
    assert context.logs == []


def test_a_failing_resolver_warns_and_is_empty(tmp_path: Path) -> None:
    context = _ctx(tmp_path, token_reduce=True)
    with patch(RESOLVER, side_effect=RuntimeError("no socket")):
        assert review_proxy_env(context) == {}
    assert len(context.logs) == 1
    assert "Warning: Failed to configure token reduction proxy in review loop: no socket." in context.logs[0]
