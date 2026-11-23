"""Proxy constants and Docker helpers shared by the token reduction sidecar and the container launch."""

import logging
import os
import shutil
import subprocess
import sys
from urllib.parse import urlparse

from sandbox_executor.redaction import starts_with_url_authority, url_userinfo_end

logger = logging.getLogger(__name__)

# Directory the Root CA certificate is mounted into inside the sandbox. The sandbox image runs as
# the unprivileged `holon` user, so update-ca-certificates can never run there; the entrypoint
# instead concatenates this mount with the image's system bundle into CONTAINER_CA_BUNDLE_PATH.
CONTAINER_CA_DIR = "/usr/local/share/ca-certificates"
# Merged trust bundle (image system roots + Holon Root CA) materialised by the sandbox entrypoint.
# SSL_CERT_FILE / REQUESTS_CA_BUNDLE / CURL_CA_BUNDLE REPLACE the trust store of the clients that
# read them, so they must point here and never at the single-cert Holon mount.
CONTAINER_CA_BUNDLE_PATH = "/home/holon/.holon-ca-bundle.crt"
# Loopback tooling and link-local metadata endpoints must never be force-proxied.
NO_PROXY_HOSTS = "localhost,127.0.0.1,::1,169.254.169.254"
# mitmproxy loads its signing CA from this directory inside the sidecar image.
MITM_PROXY_CA_DIR = "/home/mitmproxy/.mitmproxy"
PROXY_LISTEN_PORT = 8080
PROXY_ATTACH_TIMEOUT_SECONDS = 3.0
PROXY_CONNECT_TIMEOUT_SECONDS = 0.5


def _run_docker(*args: str) -> subprocess.CompletedProcess[str]:
    """Run a docker command without raising, capturing stdout/stderr for diagnostics."""
    try:
        return subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "DOCKER_CLI_HINTS": "false"},
        )
    except OSError as e:
        return subprocess.CompletedProcess(args=["docker", *args], returncode=127, stdout="", stderr=str(e))


def _container_ca_path(ca_cert_path: str) -> str:
    """Map a host Root CA path onto its read-only in-container location."""
    return f"{CONTAINER_CA_DIR}/{os.path.basename(ca_cert_path)}"


def _ca_mount_args(ca_cert_path: str) -> list[str]:
    """Docker args mounting the host Root CA certificate read-only into the sandbox."""
    return ["-v", f"{ca_cert_path}:{_container_ca_path(ca_cert_path)}:ro"]


def _build_proxy_envs(ca_cert_path: str, proxy_url: str) -> dict[str, str]:
    """Env vars that route the sandbox through ``proxy_url`` and make it trust the Holon Root CA.

    ``NODE_EXTRA_CA_CERTS`` *augments* Node's built-in roots, so it may point straight at the
    read-only Holon CA mount. ``SSL_CERT_FILE`` / ``REQUESTS_CA_BUNDLE`` / ``CURL_CA_BUNDLE``
    *replace* the trust store, so they point at the merged bundle the sandbox entrypoint writes at
    startup (system roots + Holon CA). Pointing them at the single-cert mount would make every
    legitimate HTTPS endpoint (github.com, api.openai.com, the agent's own LLM) fail verification.
    """
    container_ca = _container_ca_path(ca_cert_path)
    return {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        # curl and many CLIs only ever read the lowercase spellings.
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "NO_PROXY": NO_PROXY_HOSTS,
        "no_proxy": NO_PROXY_HOSTS,
        "NODE_EXTRA_CA_CERTS": container_ca,
        "SSL_CERT_FILE": CONTAINER_CA_BUNDLE_PATH,
        "REQUESTS_CA_BUNDLE": CONTAINER_CA_BUNDLE_PATH,
        # CURL_CA_BUNDLE is honoured by `requests`, not by the curl binary itself.
        "CURL_CA_BUNDLE": CONTAINER_CA_BUNDLE_PATH,
    }


def _mitm_proxy_ca_paths(ca_cert_path: str, ca_key_path: str) -> tuple[str, str]:
    """Materialise the mitmproxy CA pair under ``~/.holon/proxy-ca`` and return the host paths.

    A MITM proxy inherently requires the CA **private key**: without it the sidecar cannot sign the
    forged leaf certificates that make interception work, and it would fall back to its own
    ephemeral CA that the sandbox does not trust. Exposure is therefore narrowed to exactly two
    files (combined key+cert and cert-only) mounted read-only into ``/home/mitmproxy/.mitmproxy``.
    The private key is never mounted into the *agent* container, which only receives the public
    certificate.
    """
    ca_dir = os.path.join(os.path.expanduser(os.path.join("~", ".holon")), "proxy-ca")
    os.makedirs(ca_dir, exist_ok=True)
    os.chmod(ca_dir, 0o700)  # this directory holds the CA private key

    combined_path = os.path.join(ca_dir, "mitmproxy-ca.pem")
    cert_only_path = os.path.join(ca_dir, "mitmproxy-ca-cert.pem")

    with open(ca_cert_path) as cert_handle:
        cert_blob = cert_handle.read()
    with open(ca_key_path) as key_handle:
        key_blob = key_handle.read()

    # Created with 0o600 up front so the combined key+cert file is never world-readable, whatever
    # the caller's umask is, and re-chmodded in case a previous run left a looser mode behind.
    fd = os.open(combined_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(f"{key_blob}{cert_blob}")
    os.chmod(combined_path, 0o600)

    shutil.copyfile(ca_cert_path, cert_only_path)
    os.chmod(cert_only_path, 0o644)

    return combined_path, cert_only_path


def _proxy_gateway_url(port: int = PROXY_LISTEN_PORT) -> str:
    """URL of a proxy listening on the Docker host, correct for the current platform.

    ``172.17.0.1`` is the Linux bridge gateway only; Docker Desktop (macOS/Windows) does not route
    it, so ``host.docker.internal`` is used there instead.
    """
    if sys.platform in ("darwin", "win32"):
        return f"http://host.docker.internal:{port}"
    return f"http://172.17.0.1:{port}"


def _gateway_host_args() -> list[str]:
    """Docker args that make ``host.docker.internal`` resolvable on Linux."""
    if sys.platform in ("darwin", "win32"):
        return []
    return ["--add-host", "host.docker.internal:host-gateway"]


def _proxy_host_port(proxy_url: str) -> tuple[str, int] | None:
    """Split a proxy URL into ``(host, port)``; None when it cannot be parsed.

    The userinfo is removed first with the shared `redaction.url_userinfo_end` rule, so a password
    holding a raw `/`, `?` or `#` is never read as the host or port. A port that is not a valid
    integer makes the URL unparseable (None) rather than raising, because `urlparse`'s `ValueError`
    quotes the offending text, while the callers' "not a valid proxy URL" messages never echo the value.
    """
    url = proxy_url if starts_with_url_authority(proxy_url) else f"//{proxy_url}"
    head, sep, rest = url.partition("//")
    at = url_userinfo_end(rest)
    parsed = urlparse(head + sep + (rest[at + 1 :] if at is not None else rest))
    try:
        port = parsed.port
    except ValueError:
        return None
    if not parsed.hostname:
        return None
    return parsed.hostname, port or PROXY_LISTEN_PORT


def _ensure_network(network_name: str) -> bool:
    """Create a per-run bridge network; returns True when THIS run created it."""
    result = _run_docker("network", "create", network_name)
    stderr = (result.stderr or "").strip()
    if result.returncode == 0:
        return True
    logger.debug("docker network create %s exited %s: %s", network_name, result.returncode, stderr)
    if "already exists" in stderr.lower():
        return False
    raise RuntimeError(f"could not create Docker network '{network_name}': {stderr or 'unknown docker error'}")


def _published_loopback_port(container_name: str) -> int | None:
    """Read the host loopback port Docker published for the sidecar's proxy port."""
    result = _run_docker("port", container_name, f"{PROXY_LISTEN_PORT}/tcp")
    if result.returncode != 0:
        logger.debug("docker port %s exited %s: %s", container_name, result.returncode, (result.stderr or "").strip())
        return None
    for line in (result.stdout or "").splitlines():
        candidate = line.strip().rsplit(":", 1)[-1]
        if candidate.isdigit():
            return int(candidate)
    return None


def _find_git_root(start_dir: str | None = None) -> str:
    """Walk upwards to the nearest directory containing .git, or fall back to start_dir / getcwd.

    Each walk ends at the filesystem root, so the search always terminates.
    """
    for base in (start_dir, os.getcwd(), os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
        if not base:
            continue
        cur = os.path.abspath(base)
        while True:
            if os.path.exists(os.path.join(cur, ".git")):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    return os.path.abspath(start_dir or os.getcwd())
