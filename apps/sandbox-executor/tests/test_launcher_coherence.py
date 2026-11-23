"""Tests for ``launcher.coherence`` (holon-coherence detection and opt-in), ``launcher.proxy_attach`` (attaching to
a running proxy) and ``launcher.proxy_image`` (which holon-coherence image the sidecar runs).
"""

import logging
import shutil
import socket
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sandbox_executor import cli
from sandbox_executor.cli import (
    get_token_reduction_mounts_and_envs,
)
from sandbox_executor.launcher import (
    proxy_attach,
    proxy_image,
    proxy_readiness,
    root_ca,
)

from tests.leak_assertions import assert_no_fragment, make_probe
from tests.sidecar_fixtures import (
    _DIGIT_HEAD_PROXY_FORMS,
    _RAW_DELIMITER_PROXY_FORMS,
    _assert_value_withheld,
    _digit_head,
    clean_sidecar_state,
    isolate_host_paths,
)


@pytest.fixture(autouse=True)
def _clean_sidecar_state():
    yield from clean_sidecar_state()


@pytest.fixture
def host_paths(tmp_path, monkeypatch):
    """Keep every host-side write inside tmp_path and hand out a real CA (see `isolate_host_paths`)."""
    return isolate_host_paths(tmp_path, monkeypatch)


def test_host_proxy_env_alone_never_rewrites_sandbox_networking(monkeypatch):
    monkeypatch.delenv("HOLON_TOKEN_REDUCE", raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://unrelated-host-proxy:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://unrelated-host-proxy:3128")

    assert get_token_reduction_mounts_and_envs(token_reduce=False) == ([], {})


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_env_var_opt_in_attempts_configuration(monkeypatch, value):
    monkeypatch.setenv("HOLON_TOKEN_REDUCE", value)
    calls = []
    monkeypatch.setattr(proxy_attach, "_attach_external_proxy", lambda: calls.append(True) or (["--network", "x"], {}))

    mounts, envs = get_token_reduction_mounts_and_envs(token_reduce=False)

    assert calls == [True]
    assert mounts == ["--network", "x"]
    assert envs == {}


@pytest.mark.parametrize("value", ["", "0", "false", "off", "http_proxy"])
def test_env_var_opt_in_requires_truthy_value(monkeypatch, value):
    monkeypatch.setenv("HOLON_TOKEN_REDUCE", value)
    monkeypatch.setattr(proxy_attach, "_attach_external_proxy", lambda: pytest.fail("must not configure"))

    assert get_token_reduction_mounts_and_envs(token_reduce=False) == ([], {})


def test_env_var_opt_in_unreachable_proxy_degrades_to_direct_egress(host_paths, monkeypatch, caplog):
    monkeypatch.setenv("HOLON_TOKEN_REDUCE", "1")
    monkeypatch.setenv("HOLON_PROXY_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(proxy_readiness, "wait_for_proxy", lambda *args, **kwargs: False)

    with caplog.at_level(logging.ERROR, logger="sandbox_executor.launcher"):
        mounts, envs = get_token_reduction_mounts_and_envs(token_reduce=False)

    assert (mounts, envs) == ([], {})
    assert "DIRECT egress" in caplog.text
    assert "127.0.0.1" in caplog.text


def test_attach_external_proxy_probes_before_generating_a_ca(host_paths, monkeypatch):
    """An unreachable proxy must not leave a freshly generated CA behind on a direct-egress run."""
    events: list[str] = []
    monkeypatch.setenv("HOLON_PROXY_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(root_ca, "generate_root_ca", lambda: events.append("generate") or ("/c.crt", "/c.key"))
    monkeypatch.setattr(proxy_readiness, "wait_for_proxy", lambda *args, **kwargs: events.append("probe") or False)

    assert cli._attach_external_proxy() == ([], {})
    assert events == ["probe"]


def test_invalid_proxy_url_warning_masks_userinfo(host_paths, monkeypatch, caplog):
    """An unparseable HOLON_PROXY_URL is echoed with its userinfo credential masked."""
    sample = make_probe()
    monkeypatch.setenv("HOLON_PROXY_URL", f"http://alice:{sample}@")
    monkeypatch.setattr(root_ca, "generate_root_ca", lambda: pytest.fail("must not generate a CA"))

    with caplog.at_level(logging.WARNING, logger="sandbox_executor.launcher"):
        assert cli._attach_external_proxy() == ([], {})

    assert "not a valid proxy URL" in caplog.text
    assert_no_fragment(caplog.text, sample)


@pytest.mark.parametrize("form", ["alice:{v}@", "socks5://alice:{v}@", "socks5h://alice:{v}@:notaport"])
def test_invalid_proxy_url_warning_masks_userinfo_in_any_scheme(host_paths, monkeypatch, caplog, form):
    """A schemeless or non-http HOLON_PROXY_URL is echoed with its userinfo password masked."""
    sample = make_probe()
    monkeypatch.setenv("HOLON_PROXY_URL", form.format(v=sample))
    monkeypatch.setattr(root_ca, "generate_root_ca", lambda: pytest.fail("must not generate a CA"))

    with caplog.at_level(logging.WARNING, logger="sandbox_executor.launcher"):
        assert cli._attach_external_proxy() == ([], {})

    assert "not a valid proxy URL" in caplog.text
    assert_no_fragment(caplog.text, sample)


@pytest.mark.parametrize("opt_out_val", ["0", "false", "no", "off"])
def test_detect_holon_coherence_explicit_opt_out(monkeypatch, opt_out_val):
    """Explicit opt-out must return False immediately even if socket is active."""
    monkeypatch.setenv("HOLON_TOKEN_REDUCE", opt_out_val)
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: True)
    assert cli.detect_holon_coherence(token_reduce=True) is False
    assert cli.detect_holon_coherence(token_reduce=False) is False


def test_detect_holon_coherence_tcp_listening(monkeypatch):
    """When proxy is listening on TCP, detection returns True unless opted out."""
    monkeypatch.delenv("HOLON_TOKEN_REDUCE", raising=False)
    monkeypatch.delenv("HOLON_PASSIVE_MONITORING", raising=False)
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: True)
    assert cli.detect_holon_coherence(token_reduce=False) is True


def test_detect_holon_coherence_docker_image(monkeypatch):
    """When TCP socket fails but docker image is present, returns True."""
    monkeypatch.delenv("HOLON_TOKEN_REDUCE", raising=False)
    monkeypatch.delenv("HOLON_PASSIVE_MONITORING", raising=False)
    monkeypatch.setattr(socket, "create_connection", MagicMock(side_effect=OSError("connection refused")))
    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/docker" if cmd == "docker" else None)

    def mock_run(cmd, *args, **kwargs):
        if "inspect" in cmd and "holon-coherence:latest" in cmd:
            return SimpleNamespace(returncode=0, stdout="[{}]", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="error")

    monkeypatch.setattr(subprocess, "run", mock_run)
    assert cli.detect_holon_coherence(token_reduce=False) is True


def test_detect_holon_coherence_cli_binary(monkeypatch):
    """When TCP and docker fail but CLI binary is found on PATH, returns True."""
    monkeypatch.delenv("HOLON_TOKEN_REDUCE", raising=False)
    monkeypatch.delenv("HOLON_PASSIVE_MONITORING", raising=False)
    monkeypatch.setattr(socket, "create_connection", MagicMock(side_effect=OSError("connection refused")))
    monkeypatch.setattr(
        shutil, "which", lambda cmd: "/usr/local/bin/holon-coherence" if cmd == "holon-coherence" else None
    )
    assert cli.detect_holon_coherence(token_reduce=False) is True


def test_detect_holon_coherence_all_negative(monkeypatch):
    """When all tiers fail, returns False."""
    monkeypatch.delenv("HOLON_TOKEN_REDUCE", raising=False)
    monkeypatch.delenv("HOLON_PASSIVE_MONITORING", raising=False)
    monkeypatch.setattr(socket, "create_connection", MagicMock(side_effect=OSError("connection refused")))
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    assert cli.detect_holon_coherence(token_reduce=False) is False


@pytest.mark.parametrize("form", _RAW_DELIMITER_PROXY_FORMS)
def test_detect_holon_coherence_raw_delimiter_password_never_raises_or_leaks(monkeypatch, caplog, capsys, form):
    """`holon flow` / `review-loop` Tier 3 probes the real host and prints no part of the password."""
    first, second = make_probe("a"), make_probe("b")
    monkeypatch.delenv("HOLON_TOKEN_REDUCE", raising=False)
    monkeypatch.delenv("HOLON_PASSIVE_MONITORING", raising=False)
    monkeypatch.setenv("HOLON_PROXY_URL", form.format(a=first, b=second))
    probed: list[tuple[str, int]] = []
    monkeypatch.setattr(socket, "create_connection", lambda host_port, **kwargs: probed.append(host_port) or True)

    with caplog.at_level(logging.DEBUG, logger="sandbox_executor"):
        assert cli.detect_holon_coherence(token_reduce=False) is True

    assert probed == [("127.0.0.1", 3128)]
    captured = capsys.readouterr()
    for text in (caplog.text, captured.out, captured.err):
        assert_no_fragment(text, first)
        assert_no_fragment(text, second)


@pytest.mark.parametrize("form", ["http://alice:{a}@proxy.example:{b}", "alice:{a}@proxy.example:{b}"])
def test_detect_holon_coherence_unparseable_proxy_url_warns_masked(monkeypatch, caplog, capsys, form):
    """An unparseable HOLON_PROXY_URL skips the port probe with a masked warning instead of raising."""
    first, second = make_probe("a"), make_probe("b")
    monkeypatch.delenv("HOLON_TOKEN_REDUCE", raising=False)
    monkeypatch.delenv("HOLON_PASSIVE_MONITORING", raising=False)
    monkeypatch.setenv("HOLON_PROXY_URL", form.format(a=first, b=second))
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("must not probe"))
    monkeypatch.setattr(shutil, "which", lambda cmd: None)

    with caplog.at_level(logging.WARNING, logger="sandbox_executor.launcher"):
        assert cli.detect_holon_coherence(token_reduce=False) is False

    assert "not a valid proxy URL" in caplog.text
    captured = capsys.readouterr()
    for text in (caplog.text, captured.out, captured.err):
        assert_no_fragment(text, first)


@pytest.mark.parametrize("digits", [6, 12])
@pytest.mark.parametrize("form", _DIGIT_HEAD_PROXY_FORMS)
def test_detect_holon_coherence_digit_head_password_warning_withholds_value(monkeypatch, caplog, capsys, form, digits):
    """An all-digit password head before a raw delimiter is never echoed by the Tier 3 warning."""
    head = _digit_head(digits)
    proxy_url = form.format(head=head, rest=make_probe("r"))
    monkeypatch.delenv("HOLON_TOKEN_REDUCE", raising=False)
    monkeypatch.delenv("HOLON_PASSIVE_MONITORING", raising=False)
    monkeypatch.setenv("HOLON_PROXY_URL", proxy_url)
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("must not probe"))
    monkeypatch.setattr(shutil, "which", lambda cmd: None)

    with caplog.at_level(logging.DEBUG, logger="sandbox_executor"):
        assert cli.detect_holon_coherence(token_reduce=False) is False

    assert "HOLON_PROXY_URL is not a valid proxy URL" in caplog.text
    captured = capsys.readouterr()
    for text in (caplog.text, captured.out, captured.err):
        _assert_value_withheld(text, head, proxy_url)


@pytest.mark.parametrize("digits", [6, 12])
@pytest.mark.parametrize("form", _DIGIT_HEAD_PROXY_FORMS)
def test_attach_external_proxy_digit_head_password_messages_withhold_value(  # noqa: PLR0913
    host_paths, monkeypatch, caplog, capsys, form, digits
):
    """The token-reduce warning and error for an unparseable proxy URL never echo the value."""
    head = _digit_head(digits)
    proxy_url = form.format(head=head, rest=make_probe("r"))
    monkeypatch.setenv("HOLON_PROXY_URL", proxy_url)
    monkeypatch.setattr(root_ca, "generate_root_ca", lambda: pytest.fail("must not generate a CA"))
    monkeypatch.setattr(proxy_readiness, "wait_for_proxy", lambda *args, **kwargs: pytest.fail("must not probe"))

    with caplog.at_level(logging.DEBUG, logger="sandbox_executor"):
        assert cli._attach_external_proxy() == ([], {})

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("HOLON_PROXY_URL is not a valid proxy URL" in m for m in warnings)
    assert any("HOLON_PROXY_URL is not a valid proxy URL" in m for m in errors)
    captured = capsys.readouterr()
    for text in (caplog.text, captured.out, captured.err):
        _assert_value_withheld(text, head, proxy_url)


@pytest.mark.parametrize(
    "form", ["http://127.0.0.1:3128", "http://alice:{a}@127.0.0.1:3128", "alice:{a}@127.0.0.1:3128"]
)
def test_valid_proxy_url_is_probed_without_an_invalid_url_warning(monkeypatch, caplog, form):
    """A valid HOLON_PROXY_URL still probes its real host and port and logs no invalid-URL warning."""
    monkeypatch.delenv("HOLON_TOKEN_REDUCE", raising=False)
    monkeypatch.delenv("HOLON_PASSIVE_MONITORING", raising=False)
    monkeypatch.setenv("HOLON_PROXY_URL", form.format(a=make_probe("a")))
    probed: list[tuple[str, int]] = []
    monkeypatch.setattr(socket, "create_connection", lambda host_port, **kwargs: probed.append(host_port) or True)

    with caplog.at_level(logging.WARNING, logger="sandbox_executor.launcher"):
        assert cli.detect_holon_coherence(token_reduce=False) is True

    assert probed == [("127.0.0.1", 3128)]
    assert "not a valid proxy URL" not in caplog.text


def test_proxy_unreachable_fallback_direct_egress(host_paths, monkeypatch, caplog):
    """When proxy TCP probe fails, get_token_reduction_mounts_and_envs returns empty mounts/envs and logs warning."""
    monkeypatch.setenv("HOLON_TOKEN_REDUCE", "1")
    monkeypatch.setenv("HOLON_PROXY_URL", "http://127.0.0.1:8080")
    monkeypatch.setattr(proxy_readiness, "wait_for_proxy", MagicMock(side_effect=OSError("unreachable")))

    with caplog.at_level(logging.WARNING, logger="sandbox_executor.launcher"):
        mounts, envs = cli.get_token_reduction_mounts_and_envs(token_reduce=False)

    assert mounts == []
    assert envs == {}
    assert "DIRECT egress fallback" in caplog.text


def _inspect_finds(*present: str):
    """A `subprocess.run` stand-in whose `docker image inspect` succeeds only for ``present`` images."""
    calls: list[list[str]] = []

    def run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0 if cmd[-1] in present else 1, stdout="", stderr="")

    run.calls = calls
    return run


def test_coherence_proxy_image_env_override_wins(monkeypatch):
    monkeypatch.setenv("HOLON_COHERENCE_IMAGE", "  registry.example/holon-coherence@sha256:abc  ")
    monkeypatch.setattr(proxy_image, "local_coherence_image", lambda: pytest.fail("override must skip the probe"))

    assert proxy_image.coherence_proxy_image() == "registry.example/holon-coherence@sha256:abc"


def test_coherence_proxy_image_prefers_the_local_build(monkeypatch):
    monkeypatch.setenv("HOLON_COHERENCE_IMAGE", "   ")
    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/docker" if cmd == "docker" else None)
    run = _inspect_finds(proxy_image.LOCAL_COHERENCE_IMAGE, proxy_image.GHCR_COHERENCE_IMAGE)
    monkeypatch.setattr(proxy_image, "subprocess", SimpleNamespace(run=run, SubprocessError=subprocess.SubprocessError))

    assert proxy_image.coherence_proxy_image() == "holon-coherence:latest"
    assert run.calls == [["docker", "image", "inspect", "holon-coherence:latest"]]


def test_coherence_proxy_image_uses_a_pulled_ghcr_image(monkeypatch):
    monkeypatch.delenv("HOLON_COHERENCE_IMAGE", raising=False)
    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/docker" if cmd == "docker" else None)
    run = _inspect_finds(proxy_image.GHCR_COHERENCE_IMAGE)
    monkeypatch.setattr(proxy_image, "subprocess", SimpleNamespace(run=run, SubprocessError=subprocess.SubprocessError))

    assert proxy_image.local_coherence_image() == "ghcr.io/holon-agentic-coder/holon-coherence:latest"


def test_coherence_proxy_image_falls_back_to_ghcr_without_a_local_image(monkeypatch):
    monkeypatch.delenv("HOLON_COHERENCE_IMAGE", raising=False)
    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/docker" if cmd == "docker" else None)

    def timeout(cmd, *args, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 2.0)

    monkeypatch.setattr(
        proxy_image, "subprocess", SimpleNamespace(run=timeout, SubprocessError=subprocess.SubprocessError)
    )

    assert proxy_image.coherence_proxy_image() == "ghcr.io/holon-agentic-coder/holon-coherence:latest"


def test_coherence_proxy_image_without_docker_skips_the_probe(monkeypatch):
    monkeypatch.delenv("HOLON_COHERENCE_IMAGE", raising=False)
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    monkeypatch.setattr(proxy_image, "subprocess", SimpleNamespace(run=lambda *a, **k: pytest.fail("no docker")))

    assert proxy_image.local_coherence_image() is None
    assert proxy_image.coherence_proxy_image() == proxy_image.GHCR_COHERENCE_IMAGE


def test_detect_holon_coherence_ignores_a_plain_mitmproxy_image(monkeypatch):
    """A plain mitmproxy image cannot serve the sidecar, so its presence must not auto-enable token reduction."""
    monkeypatch.delenv("HOLON_TOKEN_REDUCE", raising=False)
    monkeypatch.delenv("HOLON_PASSIVE_MONITORING", raising=False)
    monkeypatch.setattr(socket, "create_connection", MagicMock(side_effect=OSError("connection refused")))
    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/docker" if cmd == "docker" else None)
    run = _inspect_finds("mitmproxy/mitmproxy:12.2.3")
    monkeypatch.setattr(proxy_image, "subprocess", SimpleNamespace(run=run, SubprocessError=subprocess.SubprocessError))

    assert cli.detect_holon_coherence(token_reduce=False) is False
    assert [call[-1] for call in run.calls] == list(proxy_image.COHERENCE_IMAGES)
