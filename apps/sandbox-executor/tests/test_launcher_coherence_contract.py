"""Contract between the sidecar `docker run` argv and the pinned ``holon-coherence`` submodule's ``start`` command.

The launcher no longer ships its own addon: it runs the holon-coherence image's entrypoint and relies on that image to
honour the flags and env names below. These checks read the submodule sources, so a renamed flag or env var there
fails here instead of leaving the sidecar (or its dashboard) silently broken. Skipped when the submodule is not
checked out.
"""

import re
from pathlib import Path

import pytest
from sandbox_executor.launcher.proxy_paths import PROXY_LISTEN_PORT
from sandbox_executor.launcher.sidecar_plan import COHERENCE_WEB_PASSWORD_ENV, _sidecar_command, _SidecarPlan

COHERENCE_SRC = Path(__file__).resolve().parents[3] / "holon-coherence" / "src" / "holon_coherence"

pytestmark = pytest.mark.skipif(
    not (COHERENCE_SRC / "cli.py").is_file(), reason="holon-coherence submodule is not checked out"
)


def _source(relative: str) -> str:
    return (COHERENCE_SRC / relative).read_text(encoding="utf-8")


def _in_container_branch() -> str:
    """The body of ``start``'s in-container branch: the code the sidecar actually runs."""
    start = _source("commands/start.py")
    begin = start.index("if in_container():")
    return start[begin : start.index("# On host:", begin)]


def _plan(mitm_web: bool) -> _SidecarPlan:
    return _SidecarPlan(
        container_name="holon-proxy-x",
        network_name="holon-net-x",
        image="ghcr.io/holon-agentic-coder/holon-coherence:latest",
        proxy_cache_dir="/c",
        mitm_ca_combined="/ca.pem",
        mitm_ca_cert="/ca-cert.pem",
        host_wire_log_dir="/w",
        host_cache_dir="/h",
        web_port=9090,
        mitm_web=mitm_web,
    )


def test_image_entrypoint_is_the_holon_coherence_cli():
    assert 'ENTRYPOINT ["holon-coherence"]' in (COHERENCE_SRC.parents[1] / "Dockerfile").read_text(encoding="utf-8")


def test_every_start_flag_the_sidecar_passes_is_declared_by_the_start_parser():
    argv = _sidecar_command(_plan(mitm_web=True))
    start_args = argv[argv.index("start") + 1 :]
    assert start_args == ["--port", str(PROXY_LISTEN_PORT), "--web", "--web-port", "9090"]
    cli_source = _source("cli.py")
    for flag in (arg for arg in start_args if arg.startswith("--")):
        assert re.search(rf'start_parser\.add_argument\(\s*"{flag}"', cli_source), flag


def test_in_container_flag_makes_start_run_mitmproxy_directly():
    assert "-e HOLON_IN_CONTAINER=1" in " ".join(_sidecar_command(_plan(mitm_web=False)))
    assert 'os.environ.get("HOLON_IN_CONTAINER") == "1"' in _source("commands/start.py")


def test_in_container_dashboard_binds_all_interfaces_on_the_requested_port():
    # The sidecar publishes only 127.0.0.1:<port> on the host, so mitmweb must listen on 0.0.0.0 inside the container.
    branch = _in_container_branch()
    assert re.search(r'"--web-host",\s*"0\.0\.0\.0",\s*"--web-port",\s*str\(args\.web_port\)', branch)


def test_dashboard_password_env_is_the_one_holon_coherence_reads():
    assert f'os.getenv("{COHERENCE_WEB_PASSWORD_ENV}")' in _source("commands/web_token.py")
    assert "resolve_pwd(" in _in_container_branch()
