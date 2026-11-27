import logging
import os
import shutil
import subprocess
import sys
from urllib.parse import urlparse

from sandbox_executor.redaction import starts_with_url_authority, url_userinfo_end

logger = logging.getLogger(__name__)

# why: sandbox runs as unprivileged holon, so update-ca-certificates cannot run and entrypoint merges this mount.
CONTAINER_CA_DIR = "/usr/local/share/ca-certificates"
# why: SSL_CERT_FILE/REQUESTS_CA_BUNDLE/CURL_CA_BUNDLE replace the trust store, so they need the merged bundle.
CONTAINER_CA_BUNDLE_PATH = "/home/holon/.holon-ca-bundle.crt"
# why: loopback tooling and link-local metadata endpoints must never be force-proxied.
NO_PROXY_HOSTS = "localhost,127.0.0.1,::1,169.254.169.254"
MITM_PROXY_CA_DIR = "/home/mitmproxy/.mitmproxy"
PROXY_LISTEN_PORT = 8080
PROXY_ATTACH_TIMEOUT_SECONDS = 3.0
PROXY_CONNECT_TIMEOUT_SECONDS = 0.5
DEFAULT_WIRE_LOG_SUBDIR = "mitm_wire_logs"
DEFAULT_CACHE_SUBDIR = "cache"

_PRIVATE_DIR_MODE = 0o700
_PRIVATE_KEY_FILE_MODE = 0o600
_PUBLIC_CERT_FILE_MODE = 0o644
_DOCKER_DESKTOP_PLATFORMS = ("darwin", "win32")
_PROXY_CA_SUBDIR = "proxy-ca"
_MITMPROXY_COMBINED_CA_FILENAME = "mitmproxy-ca.pem"
_MITMPROXY_CA_CERT_FILENAME = "mitmproxy-ca-cert.pem"


def _user_state_dir() -> str:
    return os.path.expanduser(os.path.join("~", ".holon"))


def _default_wire_log_dir() -> str:
    return os.path.join(_user_state_dir(), DEFAULT_WIRE_LOG_SUBDIR)


def _default_cache_dir() -> str:
    return os.path.join(_user_state_dir(), DEFAULT_CACHE_SUBDIR)


def _run_docker(*args: str) -> subprocess.CompletedProcess[str]:
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
    return f"{CONTAINER_CA_DIR}/{os.path.basename(ca_cert_path)}"


def _ca_mount_args(ca_cert_path: str) -> list[str]:
    return ["-v", f"{ca_cert_path}:{_container_ca_path(ca_cert_path)}:ro"]


def _build_proxy_envs(ca_cert_path: str, proxy_url: str) -> dict[str, str]:
    container_ca = _container_ca_path(ca_cert_path)
    return {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "NO_PROXY": NO_PROXY_HOSTS,
        "no_proxy": NO_PROXY_HOSTS,
        "NODE_EXTRA_CA_CERTS": container_ca,
        "SSL_CERT_FILE": CONTAINER_CA_BUNDLE_PATH,
        "REQUESTS_CA_BUNDLE": CONTAINER_CA_BUNDLE_PATH,
        "CURL_CA_BUNDLE": CONTAINER_CA_BUNDLE_PATH,
    }


def _write_private_combined_ca(combined_path: str, key_blob: str, cert_blob: str) -> None:
    # why: 0o600 at creation avoids any world-readable window regardless of umask; chmod tightens prior runs.
    fd = os.open(combined_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _PRIVATE_KEY_FILE_MODE)
    with os.fdopen(fd, "w") as handle:
        handle.write(f"{key_blob}{cert_blob}")
    os.chmod(combined_path, _PRIVATE_KEY_FILE_MODE)


def _mitm_proxy_ca_paths(ca_cert_path: str, ca_key_path: str) -> tuple[str, str]:
    ca_dir = os.path.join(_user_state_dir(), _PROXY_CA_SUBDIR)
    os.makedirs(ca_dir, exist_ok=True)
    os.chmod(ca_dir, _PRIVATE_DIR_MODE)

    combined_path = os.path.join(ca_dir, _MITMPROXY_COMBINED_CA_FILENAME)
    cert_only_path = os.path.join(ca_dir, _MITMPROXY_CA_CERT_FILENAME)

    with open(ca_cert_path) as cert_handle:
        cert_blob = cert_handle.read()
    with open(ca_key_path) as key_handle:
        key_blob = key_handle.read()

    _write_private_combined_ca(combined_path, key_blob, cert_blob)

    shutil.copyfile(ca_cert_path, cert_only_path)
    os.chmod(cert_only_path, _PUBLIC_CERT_FILE_MODE)

    return combined_path, cert_only_path


def _proxy_gateway_url(port: int = PROXY_LISTEN_PORT) -> str:
    if sys.platform in _DOCKER_DESKTOP_PLATFORMS:
        return f"http://host.docker.internal:{port}"
    return f"http://172.17.0.1:{port}"


def _gateway_host_args() -> list[str]:
    if sys.platform in _DOCKER_DESKTOP_PLATFORMS:
        return []
    return ["--add-host", "host.docker.internal:host-gateway"]


def _strip_url_userinfo(proxy_url: str) -> str:
    url = proxy_url if starts_with_url_authority(proxy_url) else f"//{proxy_url}"
    head, sep, rest = url.partition("//")
    at = url_userinfo_end(rest)
    return head + sep + (rest[at + 1 :] if at is not None else rest)


def _proxy_host_port(proxy_url: str) -> tuple[str, int] | None:
    parsed = urlparse(_strip_url_userinfo(proxy_url))
    try:
        port = parsed.port
    except ValueError:
        return None
    if not parsed.hostname:
        return None
    return parsed.hostname, port or PROXY_LISTEN_PORT


def _ensure_network(network_name: str) -> bool:
    result = _run_docker("network", "create", network_name)
    stderr = (result.stderr or "").strip()
    if result.returncode == 0:
        return True
    logger.debug("docker network create %s exited %s: %s", network_name, result.returncode, stderr)
    if "already exists" in stderr.lower():
        return False
    raise RuntimeError(f"could not create Docker network '{network_name}': {stderr or 'unknown docker error'}")


def _published_loopback_port(container_name: str) -> int | None:
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
