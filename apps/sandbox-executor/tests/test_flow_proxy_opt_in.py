"""The ``holon flow`` proxy opt-in rule: only an explicit flag spawns a sidecar, a running proxy is only attached to.

Every test is hermetic: the TCP port probe, ``docker`` and ``PATH`` lookups are stubbed, so no real socket, Docker
daemon or local port 8080 can change an outcome.
"""

import argparse
import itertools
import logging
import shutil
import socket
import subprocess
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from sandbox_executor.cli.commands.flow import _build_flow_context
from sandbox_executor.flow import FlowContext
from sandbox_executor.flow.dispatch import _dispatch_role
from sandbox_executor.launcher import coherence, proxy_attach, proxy_image, proxy_paths, role_dispatch, sidecar
from sandbox_executor.launcher.coherence import FlowProxyChoice, resolve_flow_proxy_choice

_INTENT = {"slug": "proxy-opt-in", "goal": "Route only on an explicit opt-in"}
_COHERENCE_IMAGES = ("holon-coherence:latest", "ghcr.io/holon-agentic-coder/holon-coherence:latest")
_OPT_OUT_VALUES = ("0", "false", "no", "off")
_IMAGE_ONLY = [(image, None) for image in _COHERENCE_IMAGES]
_NO_CHOICE = FlowProxyChoice(spawn_sidecar=False, attach_running_proxy=False)


class _Recorder:
    """Records every argv handed to a stubbed ``subprocess.run``/``Popen``; ``docker image inspect`` of
    ``present_image`` succeeds, everything else fails."""

    def __init__(self, present_image: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.present_image = present_image

    def __call__(self, argv: Any, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self.calls.append([str(part) for part in argv])
        present = self.present_image is not None and self.present_image in argv
        return SimpleNamespace(returncode=0 if present else 1, stdout="[{}]" if present else "", stderr="")

    def started_a_container(self) -> bool:
        """True when any recorded docker call would start a container or create a network."""
        for argv in self.calls:
            words = argv[argv.index("docker") + 1 :] if "docker" in argv else argv
            if words[:1] == ["run"] or words[:2] == ["network", "create"]:
                return True
        return False


def _closable() -> SimpleNamespace:
    return SimpleNamespace(close=lambda: None)


def _refused(*_args: Any, **_kwargs: Any) -> Any:
    raise ConnectionRefusedError("no proxy running")


def _must_not_run(name: str) -> Any:
    def fail(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail(f"{name} must not run on this path")

    return fail


@pytest.fixture(autouse=True)
def _clean_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HOLON_TOKEN_REDUCE", "HOLON_PASSIVE_MONITORING", "HOLON_PROXY_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOLON_PROXY_PORT", "18080")


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Recorder]:
    """Stub ``subprocess.run`` and ``Popen`` (seen by `coherence` and `sidecar`) and `proxy_paths._run_docker`."""
    rec = _Recorder()
    monkeypatch.setattr(proxy_image.subprocess, "run", rec)
    monkeypatch.setattr(sidecar.subprocess, "run", rec)
    monkeypatch.setattr(subprocess, "Popen", rec)
    monkeypatch.setattr(proxy_paths, "_run_docker", lambda *argv: rec(["docker", *argv]))
    yield rec


def _no_proxy_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(coherence.socket, "create_connection", _refused)


def _user_proxy_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(coherence.socket, "create_connection", lambda *_args, **_kwargs: _closable())


def _flow_args(token_reduce: bool = False, mitm_web: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        token_reduce=token_reduce,
        mitm_web=mitm_web,
        target_worktree=".",
        dry_run=False,
        skip_push=False,
        skip_calibrate=False,
        max_review_iterations=None,
        ensemble_size=None,
        allow_overlap=False,
    )


def _flow_routing(token_reduce: bool = False, mitm_web: bool = False) -> list[tuple[bool, bool]]:
    """``(token_reduce, proxy_attach)`` from the resolver, ``FlowContext`` and the ``holon flow`` command path."""
    choice = resolve_flow_proxy_choice(token_reduce or mitm_web)
    context = FlowContext(intent_data=_INTENT)
    built = _build_flow_context(_flow_args(token_reduce, mitm_web), None, _INTENT, "codex", "gpt")
    return [
        (choice.spawn_sidecar, choice.attach_running_proxy),
        (bool(context.token_reduce), context.proxy_attach),
        (bool(built.token_reduce), built.proxy_attach),
    ]


@pytest.mark.parametrize("image", _COHERENCE_IMAGES)
def test_a_present_coherence_image_does_not_enable_token_reduction_on_the_flow_path(monkeypatch, recorder, image):
    _no_proxy_running(monkeypatch)
    recorder.present_image = image
    monkeypatch.setattr(coherence.shutil, "which", lambda cmd: "/usr/bin/docker" if cmd == "docker" else None)

    assert _flow_routing() == [(False, False)] * 3
    assert recorder.calls == []
    # Positive control: the stubs are the ones the image probe would hit, and they report the image as present.
    assert coherence._coherence_image_present() is True
    assert recorder.calls[0][:3] == ["docker", "image", "inspect"]


def test_the_coherence_cli_on_path_does_not_enable_token_reduction_on_the_flow_path(monkeypatch, recorder):
    _no_proxy_running(monkeypatch)
    monkeypatch.setattr(shutil, "which", lambda cmd: f"/usr/local/bin/{cmd}")

    assert _flow_routing() == [(False, False)] * 3
    assert recorder.calls == []
    assert coherence._coherence_binary_present() is True


@pytest.mark.parametrize("opt_out", _OPT_OUT_VALUES)
@pytest.mark.parametrize(("token_reduce", "mitm_web"), [(True, False), (False, True), (False, False)])
def test_an_explicit_opt_out_wins_over_every_detection_tier(monkeypatch, recorder, opt_out, token_reduce, mitm_web):
    monkeypatch.setenv("HOLON_TOKEN_REDUCE", opt_out)
    _user_proxy_running(monkeypatch)
    recorder.present_image = _COHERENCE_IMAGES[0]
    monkeypatch.setattr(shutil, "which", lambda cmd: f"/usr/local/bin/{cmd}")

    assert resolve_flow_proxy_choice(True) == _NO_CHOICE
    assert _flow_routing(token_reduce, mitm_web) == [(False, False)] * 3
    assert recorder.calls == []


@pytest.mark.parametrize("opt_out", _OPT_OUT_VALUES)
def test_an_explicit_opt_out_makes_the_launcher_neither_spawn_nor_attach(monkeypatch, recorder, opt_out):
    monkeypatch.setenv("HOLON_TOKEN_REDUCE", opt_out)
    monkeypatch.setattr(sidecar, "setup_token_reduction_proxy", _must_not_run("setup_token_reduction_proxy"))
    monkeypatch.setattr(proxy_attach, "_attach_external_proxy", _must_not_run("_attach_external_proxy"))

    assert sidecar.get_token_reduction_mounts_and_envs(token_reduce=True, proxy_attach=True) == ([], {})
    assert recorder.calls == []


def test_a_running_user_owned_proxy_is_attached_to_and_never_spawned(monkeypatch, recorder):
    _user_proxy_running(monkeypatch)
    monkeypatch.setattr(coherence, "_coherence_image_present", _must_not_run("the Docker image probe"))
    monkeypatch.setattr(coherence, "_coherence_binary_present", _must_not_run("the CLI binary probe"))

    assert _flow_routing() == [(False, True)] * 3

    dispatcher = MagicMock(return_value=0)
    context = FlowContext(intent_data=_INTENT, dispatcher=dispatcher)
    _dispatch_role(context, "planner", ["I-1-x/_"])
    assert dispatcher.call_args.kwargs["proxy_attach"] is True
    assert dispatcher.call_args.kwargs["token_reduce"] is False

    attached = (["-v", "ca:/ca:ro"], {"HTTP_PROXY": "http://host.docker.internal:18080"})
    attach = MagicMock(return_value=attached)
    monkeypatch.setattr(proxy_attach, "_attach_external_proxy", attach)
    monkeypatch.setattr(sidecar, "setup_token_reduction_proxy", _must_not_run("setup_token_reduction_proxy"))
    assert sidecar.get_token_reduction_mounts_and_envs(proxy_attach=True) == attached
    attach.assert_called_once_with()
    assert recorder.calls == []


def test_without_proxy_attach_and_opt_in_the_launcher_stays_on_direct_egress(monkeypatch, recorder):
    monkeypatch.setattr(proxy_attach, "_attach_external_proxy", _must_not_run("_attach_external_proxy"))
    monkeypatch.setattr(sidecar, "setup_token_reduction_proxy", _must_not_run("setup_token_reduction_proxy"))

    assert sidecar.get_token_reduction_mounts_and_envs() == ([], {})


def test_role_dispatch_forwards_proxy_attach_only_when_set(monkeypatch):
    launched = MagicMock(return_value=0)
    monkeypatch.setattr(role_dispatch.containers, "run_docker_container", launched)

    role_dispatch.dispatch_role_agent("planner", "codex", "gpt", ["I-1-x/_"], target_worktree=".")
    assert "proxy_attach" not in launched.call_args.kwargs
    role_dispatch.dispatch_role_agent("planner", "codex", "gpt", ["I-1-x/_"], target_worktree=".", proxy_attach=True)
    assert launched.call_args.kwargs["proxy_attach"] is True


@pytest.mark.parametrize(
    ("port_open", "installed", "explicit"),
    list(itertools.product((False, True), [(None, None), (None, "holon-coherence"), *_IMAGE_ONLY], (False, True))),
)
def test_flow_path_detection_never_starts_a_sidecar_container(monkeypatch, recorder, port_open, installed, explicit):
    image, binary = installed
    (_user_proxy_running if port_open else _no_proxy_running)(monkeypatch)
    recorder.present_image = image
    monkeypatch.setattr(shutil, "which", lambda cmd: f"/usr/local/bin/{cmd}" if cmd in ("docker", binary) else None)
    monkeypatch.setattr(proxy_attach, "_attach_external_proxy", lambda: ([], {}))

    choice = resolve_flow_proxy_choice(explicit)
    assert choice == FlowProxyChoice(spawn_sidecar=explicit, attach_running_proxy=port_open and not explicit)
    FlowContext(intent_data=_INTENT)
    if choice.attach_running_proxy:
        assert sidecar.get_token_reduction_mounts_and_envs(proxy_attach=True) == ([], {})

    assert recorder.calls == []
    assert not recorder.started_a_container()
    # Positive control: a sidecar spawn through the same stubs is recorded as a container start.
    proxy_paths._run_docker("network", "create", "holon-net-control")
    assert recorder.started_a_container()


def test_a_resume_without_the_flag_drops_a_stale_checkpointed_spawn(monkeypatch):
    _no_proxy_running(monkeypatch)
    stale = FlowContext(intent_data=_INTENT, token_reduce=True, proxy_attach=True)

    resumed = _build_flow_context(_flow_args(), stale, _INTENT, "codex", "gpt")

    assert resumed is stale
    assert (resumed.token_reduce, resumed.proxy_attach) == (False, False)


def test_a_resume_without_the_flag_drops_a_stale_checkpointed_mitm_web(monkeypatch, caplog):
    _no_proxy_running(monkeypatch)
    stale = FlowContext(intent_data=_INTENT, token_reduce=True, mitm_web=True)

    with caplog.at_level(logging.WARNING):
        resumed = _build_flow_context(_flow_args(), stale, _INTENT, "codex", "gpt")

    assert (resumed.token_reduce, resumed.mitm_web, resumed.proxy_attach) == (False, False, False)
    assert "dropped the stored token_reduce" in caplog.text
    assert "dropped the stored mitm_web" in caplog.text


def test_an_opt_out_on_a_fresh_run_drops_mitm_web_without_a_resume_warning(monkeypatch, caplog):
    monkeypatch.setenv("HOLON_TOKEN_REDUCE", "0")

    with caplog.at_level(logging.WARNING):
        built = _build_flow_context(_flow_args(mitm_web=True), None, _INTENT, "codex", "gpt")

    assert (built.token_reduce, built.mitm_web, built.proxy_attach) == (False, False, False)
    assert "Resume dropped" not in caplog.text


def test_an_opt_out_on_a_resume_with_mitm_web_reports_the_override(monkeypatch, caplog):
    monkeypatch.setenv("HOLON_TOKEN_REDUCE", "0")
    stale = FlowContext(intent_data=_INTENT, token_reduce=True, mitm_web=True)

    with caplog.at_level(logging.WARNING):
        resumed = _build_flow_context(_flow_args(mitm_web=True), stale, _INTENT, "codex", "gpt")

    assert (resumed.token_reduce, resumed.mitm_web, resumed.proxy_attach) == (False, False, False)
    assert "Resume dropped" not in caplog.text
    assert "opt-out overrides the stored mitm_web" in caplog.text
    assert "opt-out overrides the stored token_reduce" in caplog.text


def test_a_resume_with_mitm_web_keeps_the_dashboard_sidecar(monkeypatch):
    _no_proxy_running(monkeypatch)
    stale = FlowContext(intent_data=_INTENT, mitm_web=True)

    resumed = _build_flow_context(_flow_args(mitm_web=True), stale, _INTENT, "codex", "gpt")

    assert (resumed.token_reduce, resumed.mitm_web, resumed.proxy_attach) == (True, True, False)


def test_a_resume_with_the_flag_keeps_the_spawn(monkeypatch):
    _no_proxy_running(monkeypatch)
    stale = FlowContext(intent_data=_INTENT, token_reduce=False)

    resumed = _build_flow_context(_flow_args(token_reduce=True), stale, _INTENT, "codex", "gpt")

    assert (resumed.token_reduce, resumed.proxy_attach) == (True, False)


def test_the_port_probe_stub_is_the_one_the_resolver_uses(monkeypatch):
    probed: list[Any] = []
    monkeypatch.setattr(coherence.socket, "create_connection", lambda address, **_kw: probed.append(address))
    assert socket.create_connection is coherence.socket.create_connection

    resolve_flow_proxy_choice()

    assert probed == [("127.0.0.1", 18080)]
