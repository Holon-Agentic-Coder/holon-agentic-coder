"""Tests for the proxy helpers in ``launcher.proxy_paths``: gateway URL, CA mounts, proxy env vars and URL parsing."""

import sys

import pytest
from sandbox_executor import cli
from sandbox_executor.cli import (
    CONTAINER_CA_BUNDLE_PATH,
    NO_PROXY_HOSTS,
    _build_proxy_envs,
    _ca_mount_args,
    _container_ca_path,
    _gateway_host_args,
    _proxy_gateway_url,
)

from tests.leak_assertions import make_probe
from tests.sidecar_fixtures import (
    _RAW_DELIMITER_PROXY_FORMS,
    clean_sidecar_state,
)


@pytest.fixture(autouse=True)
def _clean_sidecar_state():
    yield from clean_sidecar_state()


def test_proxy_gateway_url_is_platform_correct(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert _proxy_gateway_url() == "http://host.docker.internal:8080"
    assert _gateway_host_args() == []

    monkeypatch.setattr(sys, "platform", "linux")
    assert _proxy_gateway_url() == "http://172.17.0.1:8080"
    assert _gateway_host_args() == ["--add-host", "host.docker.internal:host-gateway"]


def test_ca_mount_and_env_helpers_agree():
    host_ca = "/host/.holon/certs/holon-root-ca.crt"
    container_ca = _container_ca_path(host_ca)

    assert _ca_mount_args(host_ca) == ["-v", f"{host_ca}:{container_ca}:ro"]
    envs = _build_proxy_envs(host_ca, "http://holon-proxy-1:8080")
    assert envs["HTTP_PROXY"] == envs["HTTPS_PROXY"] == "http://holon-proxy-1:8080"
    assert envs["NODE_EXTRA_CA_CERTS"] == container_ca
    assert envs["REQUESTS_CA_BUNDLE"] == CONTAINER_CA_BUNDLE_PATH
    assert envs["CURL_CA_BUNDLE"] == CONTAINER_CA_BUNDLE_PATH
    assert envs["SSL_CERT_FILE"] == CONTAINER_CA_BUNDLE_PATH


def test_build_proxy_envs_never_replaces_the_trust_store_with_the_holon_ca():
    """SSL_CERT_FILE/REQUESTS_CA_BUNDLE replace the store, so they must point at the merged bundle."""
    host_ca = "/host/.holon/certs/holon-root-ca.crt"
    single_cert_mount = _container_ca_path(host_ca)
    envs = _build_proxy_envs(host_ca, "http://holon-proxy-1:8080")

    for name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        assert envs[name] == CONTAINER_CA_BUNDLE_PATH
        assert envs[name] != single_cert_mount
        assert not envs[name].startswith("/usr/local/share/ca-certificates")

    # NODE_EXTRA_CA_CERTS augments Node's built-in roots, so the single-cert mount is correct there.
    assert envs["NODE_EXTRA_CA_CERTS"] == single_cert_mount


def test_build_proxy_envs_emits_lowercase_proxy_vars_and_no_proxy():
    envs = _build_proxy_envs("/host/.holon/certs/holon-root-ca.crt", "http://holon-proxy-1:8080")

    for name in ("http_proxy", "https_proxy"):
        assert envs[name] == "http://holon-proxy-1:8080"

    assert envs["NO_PROXY"] == NO_PROXY_HOSTS
    assert envs["no_proxy"] == NO_PROXY_HOSTS
    for host in ("localhost", "127.0.0.1", "::1", "169.254.169.254"):
        assert host in envs["NO_PROXY"]
        assert host in envs["no_proxy"]


@pytest.mark.parametrize("form", _RAW_DELIMITER_PROXY_FORMS)
def test_proxy_host_port_skips_a_password_with_raw_delimiters(form):
    """A raw `/`, `?` or `#` in the password is userinfo, never read as the host or port."""
    proxy_url = form.format(a=make_probe("a"), b=make_probe("b"))

    assert cli._proxy_host_port(proxy_url) == ("127.0.0.1", 3128)


@pytest.mark.parametrize("proxy_url", ["http://proxy.example:notaport", "//proxy.example:99999", "alice@:x"])
def test_proxy_host_port_returns_none_for_an_unparseable_port(proxy_url):
    """An invalid port makes the URL unparseable instead of raising urlparse's quoting ValueError."""
    assert cli._proxy_host_port(proxy_url) is None
