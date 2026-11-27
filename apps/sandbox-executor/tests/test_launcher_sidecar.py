import logging
import os
import socket
import stat
from types import SimpleNamespace

import pytest
from sandbox_executor import cli
from sandbox_executor.cli import (
    CONTAINER_CA_BUNDLE_PATH,
    _container_ca_path,
    get_token_reduction_mounts_and_envs,
    setup_token_reduction_proxy,
    teardown_token_reduction_proxy,
)
from sandbox_executor.launcher import (
    containers,
    proxy_image,
    proxy_readiness,
    sidecar,
)

from tests.fake_docker import fake_docker_runs
from tests.leak_assertions import assert_no_fragment, make_probe
from tests.sidecar_fixtures import (
    FakeDocker,
    _completed,
    clean_sidecar_state,
    isolate_host_paths,
)


@pytest.fixture(autouse=True)
def _clean_sidecar_state():
    yield from clean_sidecar_state()


@pytest.fixture
def host_paths(tmp_path, monkeypatch):
    return isolate_host_paths(tmp_path, monkeypatch)


def test_setup_proxy_uses_the_coherence_image_and_mounts_no_addon(host_paths, monkeypatch):
    monkeypatch.setattr(proxy_readiness, "wait_for_proxy", lambda *args, **kwargs: True)
    fake = FakeDocker()
    fake_docker_runs(monkeypatch, fake)

    setup_token_reduction_proxy()

    run_cmd = next(call for call in fake.calls if call[:2] == ["docker", "run"])
    joined_run = " ".join(run_cmd)
    assert "mitm_addon" not in joined_run
    assert "/tmp/src" not in joined_run
    assert "PYTHONPATH" not in joined_run
    assert "mitmdump" not in joined_run
    assert "mitmproxy/mitmproxy:" not in joined_run
    assert "-e HOLON_IN_CONTAINER=1" in joined_run
    image_index = run_cmd.index(proxy_image.GHCR_COHERENCE_IMAGE)
    assert run_cmd[image_index + 1 :] == ["start", "--port", "8080"]
    # why: with no dashboard password the spawn inherits the environment
    assert fake.envs[fake.calls.index(run_cmd)] is None


def test_setup_proxy_warns_about_the_cache_control_ttl_order_issue(host_paths, monkeypatch, caplog):
    monkeypatch.setattr(proxy_readiness, "wait_for_proxy", lambda *args, **kwargs: True)
    fake_docker_runs(monkeypatch, FakeDocker())

    with caplog.at_level(logging.WARNING, logger="sandbox_executor.launcher"):
        setup_token_reduction_proxy()

    assert "ttl='1h'" in caplog.text
    assert "HTTP 400" in caplog.text
    assert "Re-run without --token-reduce" in caplog.text


def assert_started_sidecar_removed(fake: FakeDocker) -> None:
    assert "docker rm -f" in fake.joined()
    assert "docker network rm" in fake.joined()


def test_setup_proxy_spawn_failure_raises_and_injects_no_dead_proxy(host_paths, monkeypatch):
    fake = FakeDocker(spawn=_completed(returncode=125, stderr="docker: error: image not found"))
    fake_docker_runs(monkeypatch, fake)

    with pytest.raises(RuntimeError) as excinfo:
        setup_token_reduction_proxy()

    message = str(excinfo.value)
    assert "image not found" in message
    assert "Re-run without --token-reduce" in message
    assert_started_sidecar_removed(fake)


def test_setup_proxy_spawn_failure_redacts_dashboard_credentials(host_paths, monkeypatch):
    pw_sample = make_probe("mitmweb")
    login_sample = make_probe("logintoken")
    monkeypatch.setenv("HOLON_MITM_WEB_PASSWORD", pw_sample)
    # why: hermetic, the real host port 8081 may already be bound, which would fail before docker runs.
    monkeypatch.setattr(socket.socket, "connect_ex", lambda *args, **kwargs: 1)
    stderr = (
        f"Web server listening at http://127.0.0.1:8081/?token={login_sample}\n"
        f"error: invalid option web_password={pw_sample}\nbad value {pw_sample}"
    )
    fake = FakeDocker(spawn=_completed(returncode=1, stderr=stderr))
    fake_docker_runs(monkeypatch, fake)

    with pytest.raises(RuntimeError) as excinfo:
        setup_token_reduction_proxy(mitm_web=True)

    message = str(excinfo.value)
    assert_no_fragment(message, pw_sample)
    assert_no_fragment(message, login_sample)
    assert "http://127.0.0.1:8081/?token=" in message


def test_setup_proxy_readiness_failure_raises_without_proxy_envs(host_paths, monkeypatch):
    monkeypatch.setattr(proxy_readiness, "wait_for_proxy", lambda *args, **kwargs: False)
    fake = FakeDocker()
    fake_docker_runs(monkeypatch, fake)

    with pytest.raises(RuntimeError) as excinfo:
        setup_token_reduction_proxy()

    assert "never answered an HTTP proxy request" in str(excinfo.value)
    assert "Re-run without --token-reduce" in str(excinfo.value)
    assert_started_sidecar_removed(fake)


def test_setup_proxy_missing_published_port_raises(host_paths, monkeypatch):
    fake = FakeDocker(port_stdout="")
    fake_docker_runs(monkeypatch, fake)

    with pytest.raises(RuntimeError) as excinfo:
        setup_token_reduction_proxy()

    assert "published no host loopback port" in str(excinfo.value)


def test_setup_proxy_success_mounts_only_narrow_ro_cache(host_paths, monkeypatch):
    monkeypatch.setattr(proxy_readiness, "wait_for_proxy", lambda *args, **kwargs: True)
    fake = FakeDocker()
    fake_docker_runs(monkeypatch, fake)

    mounts, envs = setup_token_reduction_proxy()

    run_cmd = next(call for call in fake.calls if call[:2] == ["docker", "run"])
    joined_run = " ".join(run_cmd)

    # why: C1, only the narrow proxy cache is shared, read-only; never ~/.holon wholesale.
    assert f"{host_paths / 'home' / '.holon' / 'proxy-cache'}:/home/mitmproxy/.holon/proxy-cache:ro" in joined_run
    assert ":/home/mitmproxy/.holon " not in joined_run
    assert "holon-root-ca.key" not in joined_run

    # why: I11, mitmproxy needs the CA private key to sign leaves, so exactly the two files it expects
    # why: are mounted read-only into /home/mitmproxy/.mitmproxy, never the whole certificate dir.
    proxy_ca_dir = host_paths / "home" / ".holon" / "proxy-ca"
    assert f"{proxy_ca_dir / 'mitmproxy-ca.pem'}:/home/mitmproxy/.mitmproxy/mitmproxy-ca.pem:ro" in joined_run
    assert f"{proxy_ca_dir / 'mitmproxy-ca-cert.pem'}:/home/mitmproxy/.mitmproxy/mitmproxy-ca-cert.pem:ro" in joined_run
    assert f"{proxy_ca_dir}:/home/mitmproxy" not in joined_run
    assert f"{proxy_ca_dir / 'mitmproxy-ca.pem'}:/home/mitmproxy/.mitmproxy:ro" not in joined_run
    combined = (proxy_ca_dir / "mitmproxy-ca.pem").read_text()
    cert_only = (proxy_ca_dir / "mitmproxy-ca-cert.pem").read_text()
    assert "BEGIN PRIVATE KEY" in combined and "BEGIN CERTIFICATE" in combined
    assert "BEGIN PRIVATE KEY" not in cert_only
    assert stat.S_IMODE(os.stat(proxy_ca_dir / "mitmproxy-ca.pem").st_mode) == 0o600
    assert stat.S_IMODE(os.stat(proxy_ca_dir).st_mode) == 0o700

    # why: I3 requires a contained sidecar posture.
    for flag in ["--memory=256m", "--cpus=0.5", "max-size=5m", "max-file=2", "--restart=no"]:
        assert flag in joined_run

    # why: I2 requires per-run resource names.
    assert "--network" in mounts
    network_name = mounts[mounts.index("--network") + 1]
    assert network_name.startswith("holon-net-")
    assert network_name not in ("holon-net",)
    assert envs["HTTP_PROXY"].startswith(f"http://holon-proxy-{os.getpid()}")
    assert envs["HTTPS_PROXY"] == envs["HTTP_PROXY"]
    # why: C6, the trust-store overrides point at the merged bundle, never at the single-cert mount.
    assert envs["SSL_CERT_FILE"] == CONTAINER_CA_BUNDLE_PATH
    assert envs["NODE_EXTRA_CA_CERTS"] == _container_ca_path(str(host_paths / "certs" / "holon-root-ca.crt"))
    assert cli._sidecar_state.network_created is True


def test_setup_proxy_network_already_exists_is_not_owned(host_paths, monkeypatch):
    monkeypatch.setattr(proxy_readiness, "wait_for_proxy", lambda *args, **kwargs: True)
    fake = FakeDocker(network_stderr="Error response from daemon: network with name x already exists")
    fake_docker_runs(monkeypatch, fake)

    setup_token_reduction_proxy()

    assert cli._sidecar_state.network_created is False
    teardown_token_reduction_proxy()
    assert "docker network rm" not in fake.joined()
    assert "docker rm -f" in fake.joined()


def test_teardown_is_noop_when_this_run_created_nothing(monkeypatch):
    fake = FakeDocker()
    fake_docker_runs(monkeypatch, fake)

    teardown_token_reduction_proxy()

    assert fake.calls == []


def test_flag_opt_in_sidecar_failure_degrades_to_direct_egress(host_paths, monkeypatch, caplog):
    fake = FakeDocker(spawn=_completed(returncode=125, stderr="Unable to find image 'holon-coherence:latest' locally"))
    fake_docker_runs(monkeypatch, fake)

    with caplog.at_level(logging.ERROR, logger="sandbox_executor.launcher"):
        mounts, envs = get_token_reduction_mounts_and_envs(token_reduce=True)

    assert (mounts, envs) == ([], {})
    assert "DIRECT egress" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "docker rm -f" in fake.joined()


def _stub_run_preconditions(monkeypatch, teardowns: list[bool]) -> None:
    monkeypatch.setattr(
        containers, "shutil", SimpleNamespace(which=lambda name: "/usr/bin/docker" if name == "docker" else None)
    )
    monkeypatch.setattr("sandbox_executor.launcher.mounts.find_github_token", lambda: None)
    monkeypatch.setattr(cli.git_flow, "sandbox_worktree_mounts", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "sandbox_executor.launcher.sidecar.get_token_reduction_mounts_and_envs",
        lambda **kwargs: (["--network", "holon-net-x"], {"HTTP_PROXY": "http://holon-proxy-x:8080"}),
    )
    monkeypatch.setattr(sidecar, "teardown_token_reduction_proxy", lambda: teardowns.append(True))


def test_run_docker_container_tears_down_sidecar_on_early_return(monkeypatch, tmp_path):
    teardowns: list[bool] = []
    _stub_run_preconditions(monkeypatch, teardowns)
    fake_docker_runs(monkeypatch, lambda *a, **k: pytest.fail("docker must not run"))

    rc = cli.run_docker_container(
        "intent-creator",
        "holon/orchestrator",
        [],
        agent_id="antigravity",
        intent_file=str(tmp_path / "missing-intent.json"),
    )

    assert rc == 1
    assert teardowns == [True]


def test_run_docker_container_tears_down_sidecar_when_body_raises(monkeypatch):
    teardowns: list[bool] = []
    _stub_run_preconditions(monkeypatch, teardowns)

    def boom(agent_id):
        raise RuntimeError("session mount exploded")

    monkeypatch.setattr("sandbox_executor.launcher.mounts.get_agent_session_mounts", boom)

    with pytest.raises(RuntimeError, match="session mount exploded"):
        cli.run_docker_container("planner", "holon/agent-pi", [], agent_id="pi")

    assert teardowns == [True]


def test_run_docker_container_tears_down_sidecar_after_the_run(monkeypatch):
    teardowns: list[bool] = []
    _stub_run_preconditions(monkeypatch, teardowns)
    fake_docker_runs(monkeypatch, lambda *a, **k: _completed(returncode=7))

    assert cli.run_docker_container("planner", "holon/agent-pi", [], agent_id="pi") == 7
    assert teardowns == [True]


def test_cli_token_reduction_mounts(monkeypatch, tmp_path):
    temp_dir = str(tmp_path)
    monkeypatch.setattr(
        "sandbox_executor.launcher.sidecar.setup_token_reduction_proxy",
        lambda: (
            ["--network", "holon-net", "-v", f"{temp_dir}/ca.crt:/container/ca.crt:ro"],
            {
                "HTTP_PROXY": "http://holon-proxy:8080",
                "HTTPS_PROXY": "http://holon-proxy:8080",
            },
        ),
    )

    mounts, envs = get_token_reduction_mounts_and_envs(token_reduce=True)
    assert "--network" in mounts
    assert envs["HTTP_PROXY"] == "http://holon-proxy:8080"
    assert envs["HTTPS_PROXY"] == "http://holon-proxy:8080"


def test_cli_mitm_web_configuration(host_paths, monkeypatch):
    monkeypatch.setattr(proxy_readiness, "wait_for_proxy", lambda *args, **kwargs: True)
    monkeypatch.setattr(socket.socket, "connect_ex", lambda *args, **kwargs: 1)
    fake = FakeDocker()
    fake_docker_runs(monkeypatch, fake)

    setup_token_reduction_proxy(mitm_web=True)
    run_cmd = next(call for call in fake.calls if call[:2] == ["docker", "run"])
    joined_run = " ".join(run_cmd)

    assert run_cmd[-6:] == ["start", "--port", "8080", "--web", "--web-port", "8081"]
    assert "mitmweb" not in joined_run
    assert "127.0.0.1:8081:8081" in joined_run
    assert "-e WIRE_LOG_DIR=/tmp/wire_logs" in joined_run
    assert ":/tmp/wire_logs" in joined_run
    assert "-e CACHE_DIR=/tmp/cache" in joined_run
    assert ":/tmp/cache" in joined_run
    assert "PYTHONPATH" not in joined_run
    assert ":/tmp/src:ro" not in joined_run
    assert "-e HOLON_IN_CONTAINER=1" in joined_run
    teardown_token_reduction_proxy()


def test_cli_mitm_web_custom_port(host_paths, monkeypatch):
    monkeypatch.setattr(proxy_readiness, "wait_for_proxy", lambda *args, **kwargs: True)
    monkeypatch.setattr(socket.socket, "connect_ex", lambda *args, **kwargs: 1)
    monkeypatch.setenv("HOLON_MITM_WEB_PORT", "9090")
    fake = FakeDocker()
    fake_docker_runs(monkeypatch, fake)

    setup_token_reduction_proxy(mitm_web=True)
    run_cmd = next(call for call in fake.calls if call[:2] == ["docker", "run"])
    joined_run = " ".join(run_cmd)

    assert "127.0.0.1:9090:9090" in joined_run
    assert "--web-port 9090" in joined_run
    teardown_token_reduction_proxy()


def test_cli_directory_permissions_0o775_and_socket_conflict(host_paths, monkeypatch):
    chmod_calls = []
    monkeypatch.setattr(os, "chmod", lambda path, mode: chmod_calls.append((path, mode)))
    monkeypatch.setattr(proxy_readiness, "wait_for_proxy", lambda *args, **kwargs: True)
    fake = FakeDocker()
    fake_docker_runs(monkeypatch, fake)

    setup_token_reduction_proxy(mitm_web=False)
    teardown_token_reduction_proxy()

    wire_and_cache_modes = [mode for path, mode in chmod_calls if "mitm_wire_logs" in path or "cache" in path]
    assert len(wire_and_cache_modes) >= 2
    for mode in wire_and_cache_modes:
        assert mode == 0o775

    class InUseSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, timeout):
            pass

        def connect_ex(self, addr):
            port_in_use = 0
            return port_in_use

    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: InUseSocket())
    with pytest.raises(RuntimeError, match="already in use"):
        setup_token_reduction_proxy(mitm_web=True)


def test_setup_token_reduction_proxy_survives_chmod_oserror(host_paths, monkeypatch):
    original_chmod = os.chmod

    def fake_chmod(path, mode):
        if "mitm_wire_logs" in str(path) or "cache" in str(path):
            raise OSError("Permission denied (mocked)")
        return original_chmod(path, mode)

    monkeypatch.setattr(os, "chmod", fake_chmod)
    monkeypatch.setattr(proxy_readiness, "wait_for_proxy", lambda *args, **kwargs: True)
    fake = FakeDocker()
    fake_docker_runs(monkeypatch, fake)

    mounts, envs = setup_token_reduction_proxy(mitm_web=False)
    teardown_token_reduction_proxy()
    assert mounts is not None
    assert "HTTP_PROXY" in envs


def test_setup_token_reduction_proxy_relative_paths(host_paths, monkeypatch):
    monkeypatch.chdir(host_paths)
    monkeypatch.setenv("WIRE_LOG_DIR", "custom/rel_wire_logs")
    monkeypatch.setenv("CACHE_DIR", "custom/rel_cache")
    monkeypatch.setattr(proxy_readiness, "wait_for_proxy", lambda *args, **kwargs: True)
    fake = FakeDocker()
    fake_docker_runs(monkeypatch, fake)

    setup_token_reduction_proxy(mitm_web=False)
    teardown_token_reduction_proxy()

    run_cmd = next(call for call in fake.calls if call[:2] == ["docker", "run"])
    wire_mounts = [arg for arg in run_cmd if arg.endswith(":/tmp/wire_logs")]
    cache_mounts = [arg for arg in run_cmd if arg.endswith(":/tmp/cache")]
    assert len(wire_mounts) == 1
    assert len(cache_mounts) == 1
    host_wire = wire_mounts[0].split(":")[0]
    host_cache = cache_mounts[0].split(":")[0]
    assert os.path.isabs(host_wire)
    assert host_wire == str(host_paths / "custom" / "rel_wire_logs")
    assert os.path.isabs(host_cache)
    assert host_cache == str(host_paths / "custom" / "rel_cache")


def test_mitm_web_password_reaches_the_container_through_the_spawn_env_not_argv(host_paths, monkeypatch):
    from sandbox_executor.cli import setup_token_reduction_proxy, teardown_token_reduction_proxy

    monkeypatch.setenv("HOLON_MITM_WEB_PASSWORD", "secretpass123")
    monkeypatch.setattr(proxy_readiness, "wait_for_proxy", lambda *args, **kwargs: True)
    monkeypatch.setattr(socket.socket, "connect_ex", lambda *args, **kwargs: 1)
    fake = FakeDocker()
    fake_docker_runs(monkeypatch, fake)

    setup_token_reduction_proxy(mitm_web=True)
    teardown_token_reduction_proxy()

    run_cmd = next(call for call in fake.calls if call[:2] == ["docker", "run"])
    assert all("secretpass123" not in arg for arg in run_cmd)
    env_index = run_cmd.index("HOLON_COHERENCE_WEB_PASSWORD")
    assert run_cmd[env_index - 1] == "-e"
    spawn_env = fake.envs[fake.calls.index(run_cmd)]
    assert spawn_env["HOLON_COHERENCE_WEB_PASSWORD"] == "secretpass123"
