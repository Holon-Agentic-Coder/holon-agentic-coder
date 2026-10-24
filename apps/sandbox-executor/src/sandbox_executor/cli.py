#!/usr/bin/env python3
"""Host wrapper CLI for running Holon Docker sandbox roles (intent, plan, execute)."""

import argparse
import contextlib
import importlib.metadata
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from urllib.parse import unquote, urlparse, urlsplit, urlunsplit

from sandbox_executor import local_llm
from sandbox_executor.agent_defaults import (
    AGENT_IMAGE_MAPPING as AGENT_IMAGE_MAPPING,  # re-exported: the mapping moved to agent_defaults
)
from sandbox_executor.agent_defaults import (
    Resolution,
    UnmappedAgentRunnerError,
    image_for_agent,
    normalise_agent_id,
    resolve_agent_runner,
    resolve_model,
)
from sandbox_executor.calibration import run_calibrate
from sandbox_executor.flow import FlowContext, FlowStage, load_checkpoint, run_flow_pipeline
from sandbox_executor.knowledge import (
    ActionIndexDatabase,
    ConsultEngine,
    TelemetryCollector,
    append_action_event,
    lift_veto,
    list_vetoes,
    promote_pending_candidates,
    reconcile_records,
)
from sandbox_executor.knowledge.consult import format_consult_results_table
from sandbox_executor.knowledge.stats import format_stats_dashboard
from sandbox_executor.ledger import format_query_results_table, query_ledger
from sandbox_executor.preflight import (
    PreflightResult,
    preflight_skipped_by_env,
    resolve_timeout,
    run_preflight_probe,
)
from sandbox_executor.redaction import (
    COMMAND_ECHO_ENV_NAMES,
    carries_url_password,
    is_secret_env_name,
    redact_agent_secrets,
    redact_command,
    redact_env_literals,
    redact_url_value,
)
from sandbox_executor.scaffold import init_project
from sandbox_executor.token_reduction import generate_root_ca

logger = logging.getLogger(__name__)

# Root of Holon's own logger hierarchy; the CLI routes its INFO records to stderr (see configure_cli_logging).
PACKAGE_LOGGER_NAME = "sandbox_executor"


class _CurrentStderrHandler(logging.StreamHandler):
    """Stream handler that always writes to the current ``sys.stderr`` rather than the one bound at creation."""

    @property
    def stream(self):  # type: ignore[override]
        return sys.stderr

    @stream.setter
    def stream(self, _value) -> None:
        pass


def configure_cli_logging() -> None:
    """Send Holon's own INFO-and-above log records to stderr as plain messages.

    Only the ``sandbox_executor`` logger hierarchy is configured, so third-party libraries keep their own levels and
    stdout stays untouched. Idempotent: a second call adds no further handler.
    """
    package_logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    if any(isinstance(handler, _CurrentStderrHandler) for handler in package_logger.handlers):
        return
    handler = _CurrentStderrHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    package_logger.addHandler(handler)
    if package_logger.level == logging.NOTSET:
        package_logger.setLevel(logging.INFO)


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
PROXY_READY_TIMEOUT_SECONDS = 15.0
PROXY_ATTACH_TIMEOUT_SECONDS = 3.0
PROXY_POLL_INTERVAL_SECONDS = 0.5
PROXY_CONNECT_TIMEOUT_SECONDS = 0.5
_TRUTHY_ENV_VALUES = ("1", "true", "yes", "on")
_EXPLICIT_OPT_OUT_VALUES = {"0", "false", "no", "off"}

_TOKEN_REDUCE_HELP = (
    "Cut agent token usage by routing sandbox egress through a locally-owned mitmproxy sidecar "
    "(context deduplication, local caching, and provider prompt cache optimisation). "
    "Requires 'docker' and 'openssl' and performs LOCAL TLS INTERCEPTION using an auto-generated "
    "Holon Root CA. All wire transactions and credentials are sanitised prior to logging."
)


@dataclass
class _SidecarState:
    """Tracks the proxy resources created by THIS run so teardown never touches foreign ones."""

    container_name: str | None = None
    network_name: str | None = None
    network_created: bool = False


_sidecar_state = _SidecarState()

BLOCKED_METADATA_HOSTS: list[str] = [
    "169.254.169.254",
    "metadata.google.internal",
    "instance-data",
]
SINKHOLE_IP: str = "127.0.0.1"
_AGENT_HELP = (
    "Agent runner to execute (e.g. antigravity-agent, pi-agent, claude-agent, codex-agent). "
    "Default: HOLON_AGENT, then the detected harness agent, then antigravity-agent."
)
_MODEL_HELP = "Model name to pass to agent. Default: HOLON_MODEL, then the runner's default model."
_SKIP_PREFLIGHT_HELP = (
    "Skip the pre-flight readiness probe (one minimal real model call). HOLON_SKIP_PREFLIGHT=1 does the same."
)


def build_network_isolation_args(
    token_reduce: bool = False,
    network_mode: str | None = None,
) -> list[str]:
    """Construct Docker CLI arguments enforcing container network isolation and egress controls.

    Drops NET_RAW capability to prevent packet sniffing and port scanning, and sinkholes cloud
    metadata endpoints to 127.0.0.1.
    If network_mode is provided and token_reduce is False, sets --network.
    When token_reduce is True, container network routing is managed by the proxy sidecar.

    Args:
        token_reduce: Whether token reduction proxy network is active.
        network_mode: Optional Docker network mode (e.g. bridge, host) when token_reduce is False.

    Returns:
        List of Docker CLI argument strings.
    """
    args = [
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
    ]

    for host in BLOCKED_METADATA_HOSTS:
        args.extend(["--add-host", f"{host}:{SINKHOLE_IP}"])

    if network_mode and not token_reduce:
        args.extend(["--network", network_mode])

    return args


def find_github_token() -> str | None:
    """Auto-detect GitHub token from environment variables or gh CLI."""
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        return token
    if shutil.which("gh"):
        try:
            res = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception as e:
            logger.debug("Failed to retrieve token via gh CLI: %s", e)
    return None


def normalize_repo_remote(remote_url: str) -> tuple[str, str]:
    """Normalize a git remote URL into (host, path_with_git).

    Handles SSH SCP-like syntax (git@host:org/repo.git), URI-style SSH
    (ssh://git@host/org/repo.git), and HTTPS/HTTP remotes (https://host/org/repo.git).

    Returns:
        A tuple of (host, repo_path) where repo_path always ends with .git.

    Raises:
        ValueError: If remote_url cannot be parsed.
    """
    clean_url = remote_url.strip()
    if "://" in clean_url:
        parsed = urlparse(clean_url)
        host = parsed.hostname or parsed.netloc.split("@")[-1].split(":")[0]
        if parsed.port and parsed.port not in (22, 443):
            host = f"{host}:{parsed.port}"
        path = parsed.path.lstrip("/")
    else:
        m = re.match(r"^(?:(?P<user>[^@]+)@)?(?P<host>[^:]+):(?P<path>.+)$", clean_url)
        if not m:
            raise ValueError(f"Unable to parse remote URL: {redact_command([remote_url])}")
        host = m.group("host")
        path = m.group("path").lstrip("/")

    if path.endswith(".git"):
        path = path[:-4]
    path_with_git = f"{path}.git"
    return host, path_with_git


# A GitHub token used as the bare user name of an HTTPS URL (`https://<token>@host/...`). Any other
# bare user name is a login name, not a credential.
_GITHUB_TOKEN_USER_RE = re.compile(r"(?:gh[opsu]_|github_pat_)[A-Za-z0-9_-]{8,}")

# The fixed password GitHub documents for the `https://<token>:x-oauth-basic@host/...` form.
_OAUTH_BASIC_PASSWORD = "x-oauth-basic"


def strip_url_credentials(url: str) -> tuple[str, str | None]:
    """Remove the userinfo (`user:password@`) from an `http(s)://` URL.

    Only HTTP(S) userinfo is a credential. SSH remotes, both `ssh://git@host/org/repo.git` and the
    SCP-style `git@host:org/repo.git`, carry the SSH login user (conventionally `git`), not a token,
    and are returned unchanged: stripping it would make ssh log in as the local user and fail.

    Args:
        url: A remote URL.

    Returns:
        The URL without userinfo, and the credential it carried, percent-decoded (as git decodes
        it), decided in this order:

        1. the user name in the GitHub `<token>:x-oauth-basic` form (password compared
           case-insensitively); the literal `x-oauth-basic` is never the credential, so with an
           empty user name (`:x-oauth-basic@`) there is none;
        2. otherwise any non-empty password (`x-access-token:<token>@`, `user:password@`);
        3. otherwise a bare user name only when it is shaped like a GitHub token (GitHub accepts a
           token as a bare user name).

        A plain bare user name is a login name, not a credential: it is stripped and the credential
        is None. The credential is also None when the URL carried no userinfo or is not an HTTP(S)
        URL.
    """
    if not url.lower().startswith(("https://", "http://")):
        return url, None
    parts = urlsplit(url)
    userinfo, sep, hostport = parts.netloc.rpartition("@")
    if not sep:
        return url, None
    user, _, password = (unquote(part) for part in userinfo.partition(":"))
    if password.lower() == _OAUTH_BASIC_PASSWORD:
        # The placeholder password is never the credential: the user name is, and an empty user name
        # leaves no credential at all.
        credential: str | None = user or None
    elif password:
        credential = password
    elif _GITHUB_TOKEN_USER_RE.fullmatch(user):
        credential = user
    else:
        credential = None
    return urlunsplit(parts._replace(netloc=hostport)), credential


def credential_host(repo_url: str) -> str | None:
    """Return the `host[:port]` a git credential helper should be scoped to, or None.

    Only HTTPS remotes carry a token through a credential helper; SSH remotes authenticate through
    the forwarded agent socket and plain HTTP must never be sent a credential.

    Args:
        repo_url: A credential-free remote URL.

    Returns:
        The lower-cased host, with its port when one is present, for an HTTPS URL; otherwise None.
    """
    if not repo_url.lower().startswith("https://"):
        return None
    hostport = urlsplit(repo_url).netloc.rpartition("@")[2].lower()
    return hostport or None


def _resolve_target_repo(repo_dir: str | None = None) -> tuple[str, str | None, str | None]:
    """Resolve the credential-free target repository URL and any credential its source embedded.

    Returns:
        The credential-free URL, the credential stripped from it (or None), and the name of the
        source that carried the credential (`HOLON_REPO_URL` or `remote.origin.url`), or None.

    Raises:
        RuntimeError: If remote.origin.url cannot be resolved or is empty.
    """
    override = os.getenv("HOLON_REPO_URL")
    if override and override.strip():
        clean_url, embedded = strip_url_credentials(override.strip())
        return clean_url, embedded, "HOLON_REPO_URL" if embedded else None

    target_dir = os.path.abspath(repo_dir) if repo_dir else os.getcwd()
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    proc = subprocess.run(
        ["git", "-C", target_dir, "config", "--get", "remote.origin.url"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(
            f"Error: Unable to resolve git remote 'origin' in '{target_dir}'. "
            "Ensure the command is run inside a git repository with an 'origin' remote configured, "
            "or set HOLON_REPO_URL explicitly."
        )

    raw_url, embedded = strip_url_credentials(proc.stdout.strip())
    source = "remote.origin.url" if embedded else None
    if find_github_token():
        host, repo_path = normalize_repo_remote(raw_url)
        return f"https://{host}/{repo_path}", embedded, source

    return raw_url, embedded, source


def resolve_target_repo(repo_dir: str | None = None) -> tuple[str, str | None]:
    """Resolve the target git repository URL without credentials, and the credential it carried.

    Checks the HOLON_REPO_URL environment variable first. If unset, queries git remote.origin.url in
    repo_dir (or cwd). Any HTTP(S) userinfo in either source is stripped. When it carries a
    credential (see `strip_url_credentials`; a plain user name does not), a warning names the source
    but never the value, and `run_docker_container` hands that credential to git inside the
    container through a runtime credential helper, in preference to an ambient host token. When a
    GitHub token is found, an origin remote is normalised to a plain HTTPS URL the same way.
    Otherwise the remote URL is returned as is, for SSH agent forwarding. A credential stripped from
    a URL that stays plain HTTP is discarded, since no credential helper is configured for it.

    Returns:
        The credential-free URL, and the credential stripped from it (None when there was none or
        it was discarded).

    Raises:
        RuntimeError: If remote.origin.url cannot be resolved or is empty.
    """
    clean_url, embedded, source = _resolve_target_repo(repo_dir)
    if source and credential_host(clean_url) is None:
        # Plain HTTP is never sent a credential, so no helper will use it: discard it rather than
        # forward an unused secret into the container.
        print(
            f"Warning: {source} contains embedded credentials; they are stripped from the URL and "
            "discarded, because plain HTTP is never sent a credential. Use an HTTPS or SSH remote.",
            file=sys.stderr,
        )
        return clean_url, None
    if source:
        print(
            f"Warning: {source} contains embedded credentials; they are stripped from the URL and the "
            "token is supplied to git inside the container through a credential helper instead.",
            file=sys.stderr,
        )
    return clean_url, embedded


def resolve_target_repo_url(repo_dir: str | None = None) -> str:
    """Resolve the target git repository URL, always without embedded credentials.

    See `resolve_target_repo`, which also returns the credential the URL carried.

    Raises:
        RuntimeError: If remote.origin.url cannot be resolved or is empty.
    """
    return resolve_target_repo(repo_dir)[0]


def get_ssh_auth_mounts() -> tuple[list[str], dict[str, str]]:
    """Determine SSH agent socket volume mounts and env vars based on OS."""
    mounts = []
    env_vars = {}

    if sys.platform == "darwin":
        # macOS Docker Desktop magic socket path
        mounts.extend(["-v", "/run/host-services/ssh-auth.sock:/run/host-services/ssh-auth.sock"])
        env_vars["SSH_AUTH_SOCK"] = "/run/host-services/ssh-auth.sock"
    else:
        ssh_sock = os.getenv("SSH_AUTH_SOCK")
        if ssh_sock and os.path.exists(ssh_sock):
            mounts.extend(["-v", f"{ssh_sock}:/run/ssh-agent"])
            env_vars["SSH_AUTH_SOCK"] = "/run/ssh-agent"

    return mounts, env_vars


def get_agent_session_mounts(agent_id: str) -> list[str]:
    """Auto-detect and construct session mounts for supported agents."""
    mounts = []
    home = os.path.expanduser("~")

    # Handle Antigravity specific mounts
    if agent_id.lower() in ("antigravity", "antigravity-agent"):
        dedicated_session = os.getenv(
            "HOLON_ANTIGRAVITY_SESSION_DIR",
            os.path.join(home, ".holon", "sessions", "antigravity"),
        )

        # If HOLON_AGENT_KEY is supplied, session mount is optional
        has_key = bool(os.getenv("HOLON_AGENT_KEY"))

        # On macOS, ~/.holon/sessions/antigravity is required if not using HOLON_AGENT_KEY
        if sys.platform == "darwin":
            if os.path.exists(dedicated_session):
                mounts.extend(["-v", f"{dedicated_session}:/home/holon/.gemini/antigravity-cli:ro"])
                mounts.extend(["--tmpfs", "/home/holon/.gemini/config:uid=1000,gid=1000"])
                mounts.extend(["--tmpfs", "/home/holon/.gemini/antigravity-cli/log:uid=1000,gid=1000"])
                mounts.extend(["--tmpfs", "/home/holon/.gemini/antigravity-cli/cache:uid=1000,gid=1000"])
                return mounts
            elif not has_key:
                print(
                    "Error: Missing Antigravity sandbox session directory on macOS.\n"
                    f"The session directory '{dedicated_session}' does not exist.\n\n"
                    "Please initialise the Antigravity session in an interactive TTY by running:\n\n"
                    f"  mkdir -p {dedicated_session}\n"
                    "  docker run -it "
                    f"-v {dedicated_session}:/home/holon/.gemini/antigravity-cli:rw holon/agent-antigravity agy\n\n"
                    "After completing interactive authentication, rerun this command.",
                    file=sys.stderr,
                )
                sys.exit(1)

        # On Linux and other platforms, mount ~/.holon/sessions/antigravity or host ~/.gemini/antigravity-cli
        if os.path.exists(dedicated_session):
            mounts.extend(["-v", f"{dedicated_session}:/home/holon/.gemini/antigravity-cli:ro"])
            mounts.extend(["--tmpfs", "/home/holon/.gemini/config:uid=1000,gid=1000"])
            mounts.extend(["--tmpfs", "/home/holon/.gemini/antigravity-cli/log:uid=1000,gid=1000"])
            mounts.extend(["--tmpfs", "/home/holon/.gemini/antigravity-cli/cache:uid=1000,gid=1000"])
            return mounts

        host_agy_cli = os.path.join(home, ".gemini", "antigravity-cli")
        if os.path.exists(host_agy_cli):
            mounts.extend(["-v", f"{host_agy_cli}:/home/holon/.gemini/antigravity-cli:ro"])
            mounts.extend(["--tmpfs", "/home/holon/.gemini/config:uid=1000,gid=1000"])
            mounts.extend(["--tmpfs", "/home/holon/.gemini/antigravity-cli/log:uid=1000,gid=1000"])
            mounts.extend(["--tmpfs", "/home/holon/.gemini/antigravity-cli/cache:uid=1000,gid=1000"])

        return mounts

    session_mapping = {
        "claude": [
            (os.path.join(home, ".config/claude"), "/home/holon/.config/claude"),
        ],
        "codex": [
            (os.path.join(home, ".codex"), "/home/holon/.codex"),
        ],
        # Only pi's models.json is mounted, never the whole agent directory: that directory also holds auth.json,
        # sessions/ and run-history.jsonl, none of which a sandboxed agent needs.
        "pi": [
            (os.path.join(home, ".pi/agent/models.json"), "/home/holon/.pi/agent/models.json"),
            (os.path.join(home, ".config/pi/models.json"), "/home/holon/.config/pi/models.json"),
        ],
        "gemini": [
            (os.path.join(home, ".config/gcloud"), "/home/holon/.config/gcloud"),
        ],
    }

    norm_agent_id = local_llm.normalize_agent_id(agent_id)
    dirs = session_mapping.get(norm_agent_id, [])
    # In host-local model mode the container receives a generated agent directory whose base URLs are reachable from it,
    # so the host's own provider config is deliberately not mounted.
    if norm_agent_id == "pi" and local_llm.local_llm_requested():
        return mounts
    for host_path, container_path in dirs:
        if os.path.exists(host_path):
            mounts.extend(["-v", f"{host_path}:{container_path}:ro"])
            if norm_agent_id == "codex" and container_path == "/home/holon/.codex":
                mounts.extend(["--tmpfs", "/home/holon/.codex/log:uid=1000,gid=1000"])
                mounts.extend(["--tmpfs", "/home/holon/.codex/cache:uid=1000,gid=1000"])

    return mounts


def _run_docker(*args: str) -> subprocess.CompletedProcess[str]:
    """Run a docker command without raising, capturing stdout/stderr for diagnostics."""
    try:
        return subprocess.run(["docker", *args], capture_output=True, text=True, check=False)
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
    """Split a proxy URL into ``(host, port)``; None when it cannot be parsed."""
    parsed = urlparse(proxy_url if "//" in proxy_url else f"//{proxy_url}")
    if not parsed.hostname:
        return None
    return parsed.hostname, parsed.port or PROXY_LISTEN_PORT


def detect_holon_coherence(token_reduce: bool = False) -> bool:
    """Detect whether holon-coherence proxy is available or explicitly requested.

    Evaluation hierarchy:
    - Tier 1 (Explicit Opt-Out): If HOLON_TOKEN_REDUCE is in ("0", "false", "no", "off"),
      return False immediately.
    - Tier 2 (Explicit Opt-In): If token_reduce is True, or HOLON_TOKEN_REDUCE /
      HOLON_PASSIVE_MONITORING is truthy, return True.
    - Tier 3 (TCP Port Probe): Determine target proxy host and port from HOLON_PROXY_URL,
      or 127.0.0.1 and HOLON_PROXY_PORT (default 8080). Attempt a quick non-blocking TCP socket
      connection with a 0.2-second timeout. If successful, return True.
    - Tier 4 (Docker Image Probe): If docker CLI exists on host PATH, probe if either
      'holon-coherence:latest' or 'ghcr.io/holon-agentic-coder/holon-coherence:latest' is present
      locally via docker image inspect. If found, return True.
    - Tier 5 (CLI Binary Probe): Check if 'holon-coherence' executable exists on PATH.
      If found, return True.
    - If all tiers evaluate negative, return False.
    """
    env_reduce = os.getenv("HOLON_TOKEN_REDUCE", "").strip().lower()
    if env_reduce in _EXPLICIT_OPT_OUT_VALUES:
        return False

    if token_reduce:
        return True
    if env_reduce in _TRUTHY_ENV_VALUES:
        return True
    if os.getenv("HOLON_PASSIVE_MONITORING", "").strip().lower() in _TRUTHY_ENV_VALUES:
        return True

    # Tier 3: TCP Port Probe
    proxy_url = os.getenv("HOLON_PROXY_URL")
    if proxy_url:
        host_port = _proxy_host_port(proxy_url)
    else:
        try:
            port = int(os.getenv("HOLON_PROXY_PORT", str(PROXY_LISTEN_PORT)))
        except ValueError:
            port = PROXY_LISTEN_PORT
        host_port = ("127.0.0.1", port)

    if host_port is not None:
        try:
            sock = socket.create_connection(host_port, timeout=0.2)
            if hasattr(sock, "close"):
                sock.close()
            return True
        except (OSError, TimeoutError):
            pass

    # Tier 4: Docker Image Probe (checks for coherence or mitmproxy images that sidecar runner supports)
    if shutil.which("docker"):
        for img in (
            "holon-coherence:latest",
            "ghcr.io/holon-agentic-coder/holon-coherence:latest",
            "mitmproxy/mitmproxy:12.2.3",
        ):
            try:
                proc = subprocess.run(
                    ["docker", "image", "inspect", img],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=2.0,
                )
                if proc.returncode == 0:
                    return True
            except (subprocess.SubprocessError, OSError):
                pass

    # Tier 5: CLI Binary Probe
    return bool(shutil.which("holon-coherence"))


def _token_reduce_opt_in(token_reduce: bool = False) -> bool:
    """True only on explicit opt-in; respects opt-out."""
    env_reduce = os.getenv("HOLON_TOKEN_REDUCE", "").strip().lower()
    if env_reduce in _EXPLICIT_OPT_OUT_VALUES:
        return False
    if token_reduce:
        return True
    if env_reduce in _TRUTHY_ENV_VALUES:
        return True
    return os.getenv("HOLON_PASSIVE_MONITORING", "").strip().lower() in _TRUTHY_ENV_VALUES


def _wait_for_proxy(host: str, port: int, timeout: float = PROXY_READY_TIMEOUT_SECONDS) -> bool:
    """Poll a TCP endpoint until it accepts a connection; return False if it never does."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            with socket.create_connection((host, port), timeout=PROXY_CONNECT_TIMEOUT_SECONDS):
                return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(PROXY_POLL_INTERVAL_SECONDS)


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
    """Traverse upwards to find the git repository root containing .git, or fallback to start_dir / getcwd."""
    for base in (start_dir, os.getcwd(), os.path.dirname(os.path.abspath(__file__))):
        if not base:
            continue
        cur = os.path.abspath(base)
        while True:
            if os.path.exists(os.path.join(cur, ".git")):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:
                break
    return os.path.abspath(start_dir or os.getcwd())


def setup_token_reduction_proxy(mitm_web: bool = False) -> tuple[list[str], dict[str, str]]:
    """Start this run's mitmproxy sidecar and return the sandbox mounts and env vars.

    Resources are named per run (pid + uuid suffix) and recorded in ``_sidecar_state`` so teardown
    only ever removes what this run created.

    Raises:
        FileNotFoundError: If the mitmproxy addon script is missing.
        RuntimeError: If Docker networking, the sidecar spawn, or the readiness probe fails.
    """
    run_suffix = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    container_name = f"holon-proxy-{run_suffix}"
    network_name = f"holon-net-{run_suffix}"

    addon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token_reduction", "mitm_addon.py")
    if not os.path.isfile(addon_path):
        raise FileNotFoundError(
            f"mitmproxy addon script not found at '{addon_path}'. Token reduction cannot start its proxy "
            "without it; re-run without --token-reduce to execute with direct egress."
        )

    ca_cert_path, ca_key_path = generate_root_ca()

    # Share ONLY a narrow proxy cache dir, read-only. Never mount ~/.holon: that subtree holds the
    # Root CA private key (~/.holon/certs) and the agent auth session stores (~/.holon/sessions).
    proxy_cache_dir = os.path.join(os.path.expanduser(os.path.join("~", ".holon")), "proxy-cache")
    os.makedirs(proxy_cache_dir, exist_ok=True)

    # mitmproxy needs the CA private key to sign leaves (see _mitm_proxy_ca_paths); hand it exactly
    # the two files it expects, read-only, instead of the whole certificate directory.
    mitm_ca_combined, mitm_ca_cert = _mitm_proxy_ca_paths(ca_cert_path, ca_key_path)

    repo_root = _find_git_root()
    wire_dir_env = os.getenv("WIRE_LOG_DIR")
    if wire_dir_env:
        host_wire_log_dir = (
            wire_dir_env if os.path.isabs(wire_dir_env) else os.path.abspath(os.path.join(repo_root, wire_dir_env))
        )
    else:
        host_wire_log_dir = os.path.abspath(os.path.join(repo_root, "todo", "mitm_wire_logs"))
    os.makedirs(host_wire_log_dir, exist_ok=True)

    cache_dir_env = os.getenv("CACHE_DIR")
    if cache_dir_env:
        host_cache_dir = (
            cache_dir_env if os.path.isabs(cache_dir_env) else os.path.abspath(os.path.join(repo_root, cache_dir_env))
        )
    else:
        host_cache_dir = os.path.abspath(os.path.join(repo_root, "todo", "cache"))
    os.makedirs(host_cache_dir, exist_ok=True)

    # Ensure non-root container UID 1000 can write without permission errors on Linux hosts.
    # Note: On shared multi-user hosts or environments where host UID/GID != 1000, setting
    # HOLON_MITM_USER="$(id -u):$(id -g)" is strongly recommended, allowing the host
    # directories to use restrictive 0o700 or 0o750 permissions rather than 0o775.
    for d in (host_wire_log_dir, host_cache_dir):
        try:
            os.chmod(d, 0o775)
        except OSError as e:
            logger.debug("Could not chmod 0o775 on %s: %s", d, e)

    try:
        web_port = int(os.getenv("HOLON_MITM_WEB_PORT", "8081"))
    except ValueError:
        logger.warning("Invalid HOLON_MITM_WEB_PORT; falling back to 8081.")
        web_port = 8081

    if mitm_web:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(PROXY_CONNECT_TIMEOUT_SECONDS)
            if s.connect_ex(("127.0.0.1", web_port)) == 0:
                raise RuntimeError(
                    f"Port {web_port} is already in use on 127.0.0.1. "
                    "Specify a different dashboard port using HOLON_MITM_WEB_PORT=<port>."
                )

    _sidecar_state.network_name = network_name
    _sidecar_state.network_created = _ensure_network(network_name)
    _sidecar_state.container_name = container_name

    src_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

    docker_run_proxy = [
        "docker",
        "run",
        "-d",
        "--name",
        container_name,
        "--network",
        network_name,
        "--memory=256m",
        "--cpus=0.5",
        "--log-opt",
        "max-size=5m",
        "--log-opt",
        "max-file=2",
        "--restart=no",
        # Loopback-only publish so the host can run a real TCP readiness probe.
        "-p",
        f"127.0.0.1::{PROXY_LISTEN_PORT}",
    ]
    mitm_user = os.getenv("HOLON_MITM_USER")
    if mitm_user:
        docker_run_proxy.extend(["--user", mitm_user])
    if mitm_web:
        docker_run_proxy.extend(["-p", f"127.0.0.1:{web_port}:{web_port}"])

    passive_val = "1" if os.getenv("HOLON_PASSIVE_MONITORING", "").strip().lower() in _TRUTHY_ENV_VALUES else "0"
    docker_run_proxy.extend(
        [
            "-e",
            "PYTHONPATH=/tmp/src",
            "-e",
            "WIRE_LOG_DIR=/tmp/wire_logs",
            "-e",
            "CACHE_DIR=/tmp/cache",
            "-e",
            f"HOLON_PASSIVE_MONITORING={passive_val}",
            "-v",
            f"{src_dir}:/tmp/src:ro",
            "-v",
            f"{proxy_cache_dir}:/home/mitmproxy/.holon/proxy-cache:ro",
            "-v",
            f"{host_cache_dir}:/tmp/cache",
            "-v",
            f"{mitm_ca_combined}:{MITM_PROXY_CA_DIR}/mitmproxy-ca.pem:ro",
            "-v",
            f"{mitm_ca_cert}:{MITM_PROXY_CA_DIR}/mitmproxy-ca-cert.pem:ro",
            "-v",
            f"{addon_path}:/tmp/mitm_addon.py:ro",
            "-v",
            f"{host_wire_log_dir}:/tmp/wire_logs",
            "mitmproxy/mitmproxy:12.2.3",
            "mitmweb" if mitm_web else "mitmdump",
            "-s",
            "/tmp/mitm_addon.py",
            "--listen-port",
            str(PROXY_LISTEN_PORT),
            "--set",
            "stream_large_bodies=1m",
        ]
    )
    if mitm_web:
        docker_run_proxy.extend(["--web-host", "0.0.0.0", "--web-port", str(web_port)])
        web_password = os.getenv("HOLON_MITM_WEB_PASSWORD")
        if web_password:
            docker_run_proxy.extend(["--set", f"web_password={web_password}"])

    logger.info(
        "Starting mitmproxy sidecar '%s'; the first run has to pull the 'mitmproxy/mitmproxy:12.2.3' "
        "image, which can take a while before the container appears.",
        container_name,
    )
    proxy_spawn = subprocess.run(docker_run_proxy, capture_output=True, text=True, check=False)
    if proxy_spawn.returncode != 0:
        # The sidecar's output can echo the mitmweb `?token=` login URL or the `web_password=` setting.
        stderr = redact_env_literals(
            redact_agent_secrets((proxy_spawn.stderr or "").strip() or (proxy_spawn.stdout or "").strip()),
            names=COMMAND_ECHO_ENV_NAMES,
        )
        teardown_token_reduction_proxy()
        raise RuntimeError(
            f"mitmproxy sidecar '{container_name}' failed to start: {stderr or 'unknown docker error'}. "
            "Re-run without --token-reduce to execute with direct egress."
        )

    host_port = _published_loopback_port(container_name)
    if host_port is None:
        teardown_token_reduction_proxy()
        raise RuntimeError(
            f"mitmproxy sidecar '{container_name}' published no host loopback port, so its readiness cannot be "
            "verified. Re-run without --token-reduce to execute with direct egress."
        )
    if not _wait_for_proxy("127.0.0.1", host_port):
        teardown_token_reduction_proxy()
        raise RuntimeError(
            f"mitmproxy sidecar '{container_name}' never accepted connections on 127.0.0.1:{host_port} within "
            f"{PROXY_READY_TIMEOUT_SECONDS}s (the addon likely crashed on startup). "
            "Re-run without --token-reduce to execute with direct egress."
        )

    if mitm_web:
        logger.info("🌐 mitmweb dashboard active at http://127.0.0.1:%s", web_port)

    mounts = ["--network", network_name, *_ca_mount_args(ca_cert_path)]
    return mounts, _build_proxy_envs(ca_cert_path, f"http://{container_name}:{PROXY_LISTEN_PORT}")


def teardown_token_reduction_proxy() -> None:
    """Remove only the sidecar container and network THIS run created (no-op otherwise)."""
    if _sidecar_state.container_name:
        result = _run_docker("rm", "-f", _sidecar_state.container_name)
        if result.returncode != 0:
            logger.debug(
                "docker rm -f %s exited %s: %s",
                _sidecar_state.container_name,
                result.returncode,
                (result.stderr or "").strip(),
            )
        _sidecar_state.container_name = None

    if _sidecar_state.network_created and _sidecar_state.network_name:
        result = _run_docker("network", "rm", _sidecar_state.network_name)
        if result.returncode != 0:
            logger.debug(
                "docker network rm %s exited %s: %s",
                _sidecar_state.network_name,
                result.returncode,
                (result.stderr or "").strip(),
            )

    _sidecar_state.network_name = None
    _sidecar_state.network_created = False


def _attach_external_proxy() -> tuple[list[str], dict[str, str]]:
    """Attach the sandbox to an already-running proxy (``HOLON_PROXY_URL`` or host gateway).

    Used for the ``HOLON_TOKEN_REDUCE`` opt-in path: the proxy is owned by the user, so this run
    neither starts nor tears it down. An unreachable proxy degrades to direct egress.
    """
    proxy_url = os.getenv("HOLON_PROXY_URL") or _proxy_gateway_url()
    host_port = _proxy_host_port(proxy_url)
    if host_port is None:
        # Parsed as a `//` authority when it has no scheme, so a schemeless password is masked too.
        shown_url = redact_url_value(proxy_url)
        logger.warning(
            "holon-coherence proxy probe failed: HOLON_PROXY_URL='%s' is not a valid proxy URL. "
            "Proceeding with DIRECT egress fallback.",
            shown_url,
        )
        logger.error(
            "HOLON_TOKEN_REDUCE is enabled but HOLON_PROXY_URL='%s' is not a valid proxy URL. "
            "This run continues with DIRECT egress (no token reduction).",
            shown_url,
        )
        return [], {}

    # Probe before generating: an unreachable proxy must not leave a fresh CA behind on a host that
    # is about to run with direct egress.
    ready = False
    probe_host = "127.0.0.1" if host_port[0] in ("host.docker.internal", "172.17.0.1", "localhost") else host_port[0]
    try:
        ready = _wait_for_proxy(probe_host, host_port[1], timeout=PROXY_ATTACH_TIMEOUT_SECONDS)
    except (OSError, TimeoutError) as exc:
        logger.warning(
            "holon-coherence proxy probe failed at %s:%s. Proceeding with DIRECT egress fallback.",
            probe_host,
            host_port[1],
        )
        logger.error(
            "HOLON_TOKEN_REDUCE is enabled but proxy probe failed at %s:%s (%s: %s). "
            "This run continues with DIRECT egress (no token reduction).",
            probe_host,
            host_port[1],
            type(exc).__name__,
            exc,
        )
        return [], {}

    if not ready:
        logger.warning(
            "holon-coherence proxy probe failed at %s:%s. Proceeding with DIRECT egress fallback.",
            host_port[0],
            host_port[1],
        )
        logger.error(
            "HOLON_TOKEN_REDUCE is enabled but no proxy accepted a TCP connection at %s:%s. Start the proxy or "
            "point HOLON_PROXY_URL at it; this run continues with DIRECT egress (no token reduction).",
            host_port[0],
            host_port[1],
        )
        return [], {}

    ca_cert_path, _ = generate_root_ca()

    # No gateway --add-host here: run_docker_container adds it once for every agent container, and emitting it from
    # both places produced a duplicate flag whenever an externally attached proxy was in use.
    return _ca_mount_args(ca_cert_path), _build_proxy_envs(ca_cert_path, proxy_url)


def get_token_reduction_mounts_and_envs(
    token_reduce: bool = False,
    mitm_web: bool = False,
) -> tuple[list[str], dict[str, str]]:
    """Build token-reduction mounts/env vars for an explicitly opted-in or auto-detected run.

    Opt-in is strictly ``--token-reduce``, ``--mitm-web``, ``HOLON_TOKEN_REDUCE`` in ``("1", "true", "yes",
    "on")``, or auto-detected holon-coherence proxy. Host ``HTTP_PROXY``/``HTTPS_PROXY`` alone never
    change sandbox networking. Any failure degrades to direct egress (empty mounts/envs) with an
    actionable warning log.
    """
    if not _token_reduce_opt_in(token_reduce or mitm_web):
        return [], {}

    try:
        is_passive = os.getenv("HOLON_PASSIVE_MONITORING", "").strip().lower() in _TRUTHY_ENV_VALUES
        if token_reduce or mitm_web or is_passive:
            if mitm_web:
                return setup_token_reduction_proxy(mitm_web=True)
            return setup_token_reduction_proxy()
        return _attach_external_proxy()
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        logger.warning(
            "holon-coherence proxy configuration failed (%s: %s). Proceeding with DIRECT egress fallback.",
            type(exc).__name__,
            exc,
        )
        logger.error(
            "Token reduction is enabled but could not be configured (%s: %s). This run continues with DIRECT "
            "egress (no TLS interception, no token reduction).",
            type(exc).__name__,
            exc,
        )
        return [], {}


def get_host_proxy_envs() -> dict[str, str]:
    """Return environment variables for host-side processes to route through an active coherence proxy.

    Only returns proxy env vars if a proxy is actively listening on localhost or HOLON_PROXY_URL,
    and the root CA certificate exists on the host. Otherwise returns empty dict (direct egress).
    """
    env_reduce = os.getenv("HOLON_TOKEN_REDUCE", "").strip().lower()
    if env_reduce in _EXPLICIT_OPT_OUT_VALUES:
        return {}

    proxy_url = os.getenv("HOLON_PROXY_URL")
    if proxy_url:
        host_port = _proxy_host_port(proxy_url)
    else:
        try:
            port = int(os.getenv("HOLON_PROXY_PORT", str(PROXY_LISTEN_PORT)))
        except ValueError:
            port = PROXY_LISTEN_PORT
        host_port = ("127.0.0.1", port)
        proxy_url = f"http://127.0.0.1:{port}"

    if host_port is None:
        return {}

    # Probe if proxy is actively accepting connections
    try:
        with socket.create_connection(host_port, timeout=0.2):
            pass
    except (OSError, TimeoutError):
        return {}

    # Check for host CA cert
    ca_dir = os.path.join(os.path.expanduser(os.path.join("~", ".holon")), "proxy-ca")
    ca_cert_path = os.path.join(ca_dir, "mitmproxy-ca-cert.pem")
    if not os.path.exists(ca_cert_path):
        try:
            ca_cert_path, _ = generate_root_ca()
        except Exception:
            return {}

    envs = {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
    }
    if os.path.exists(ca_cert_path):
        envs["NODE_EXTRA_CA_CERTS"] = ca_cert_path
        envs["SSL_CERT_FILE"] = ca_cert_path
        envs["REQUESTS_CA_BUNDLE"] = ca_cert_path

    return envs


# Credential env-files live in an owner-only directory, apart from ~/.holon/tmp itself, which also
# holds directories that are bind-mounted into the container.
ENV_FILE_PREFIX = "holon-env-"
_ENV_FILE_STALE_SECONDS = 24 * 60 * 60

# A git credential helper that answers `get` from the container's own environment. It holds only the
# `$GITHUB_TOKEN` reference, never the value; docker passes --env-file values verbatim (no quote or
# `$` processing), so the reference is expanded by the helper's shell at call time.
GIT_CREDENTIAL_HELPER = (
    '!f() { if [ "$1" = get ]; then printf "username=x-access-token\\npassword=%s\\n" "$GITHUB_TOKEN"; fi; }; f'
)


def env_file_dir() -> str:
    """Return the directory for temporary credential env-files, creating it owner-only (0700).

    Returns:
        The absolute path of `~/.holon/tmp/env-files`. Looser permission bits on an existing
        directory are tightened back to 0700.
    """
    path = os.path.expanduser(os.path.join("~", ".holon", "tmp", "env-files"))
    os.makedirs(path, mode=0o700, exist_ok=True)
    if os.stat(path).st_mode & 0o077:
        os.chmod(path, 0o700)
    return path


def sweep_stale_env_files(directory: str, max_age_seconds: float = _ENV_FILE_STALE_SECONDS) -> None:
    """Best-effort removal of credential env-files left behind by a killed run.

    Args:
        directory: The env-file directory to sweep.
        max_age_seconds: Files whose modification time is older than this are removed.
    """
    cutoff = time.time() - max_age_seconds
    try:
        entries = os.listdir(directory)
    except OSError:
        return
    for name in entries:
        if not name.startswith(ENV_FILE_PREFIX):
            continue
        path = os.path.join(directory, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                logger.debug("Removed stale credential env-file %s", path)
        except OSError as exc:
            logger.debug("Could not sweep env-file %s: %s", path, exc)


def write_env_file(env: dict[str, str]) -> str:
    """Write credential variables to a new 0600 docker `--env-file`, one `KEY=VALUE` line each.

    Docker reads the file on the client side and applies no quoting, so a value containing a line
    break would be cut short or smuggle in a second variable; such values are rejected.

    Args:
        env: The variables to write, which are emitted in sorted key order.

    Returns:
        The path of the new file. The caller owns it and must remove it.

    Raises:
        ValueError: If a value contains a line break. The message names the key, never the value.
    """
    for key, value in env.items():
        if "\n" in value or "\r" in value:
            raise ValueError(f"the value of {key} contains a line break and cannot be passed to the container")
    fd, path = tempfile.mkstemp(prefix=ENV_FILE_PREFIX, dir=env_file_dir())
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.writelines(f"{key}={value}\n" for key, value in sorted(env.items()))
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)
        raise
    return path


def run_docker_container(
    role: str,
    image_name: str,
    container_args: list[str],
    agent_id: str,
    intent_file: str | None = None,
    token_reduce: bool = False,
    mitm_web: bool = False,
    repo_dir: str | None = None,
) -> int:
    """Constructs docker run command with auto-discovered credentials and executes it."""
    if not shutil.which("docker"):
        print("Error: 'docker' CLI command not found. Please install Docker.", file=sys.stderr)
        return 1

    try:
        resolved_repo_url, embedded_token = resolve_target_repo(repo_dir)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    tty_flag = ["-it"] if sys.stdin.isatty() else ["-i"]
    docker_cmd = ["docker", "run", "--rm", *tty_flag]

    # Credentials never go on the docker command line, where `ps` and /proc/<pid>/cmdline can read
    # them: they are collected here and handed over in a 0600 --env-file just before the run. That
    # includes any value with a URL password (a proxy URL in any scheme, or none), whatever its name.
    secret_env: dict[str, str] = {}

    def add_env(key: str, value: str) -> None:
        item = f"{key}={value}"
        if is_secret_env_name(key) or redact_command([item]) != item or carries_url_password(value):
            secret_env[key] = value
        else:
            docker_cmd.extend(["-e", item])

    # Forward all host environment variables prefixed with HOLON_ and GITHUB_TOKEN
    env_to_forward = {}
    override_url = (os.getenv("HOLON_REPO_URL") or "").strip()
    # A credential embedded in the repository URL (explicit override or origin) is scoped to that
    # remote and wins over an ambient host token, which may belong to a different host.
    gh_token = embedded_token or find_github_token()

    for key, value in os.environ.items():
        if key.startswith("HOLON_") or key == "GITHUB_TOKEN":
            env_to_forward[key] = value

    # Set after the host forwarding loop so a host GITHUB_TOKEN cannot displace the chosen token.
    if gh_token:
        env_to_forward["GITHUB_TOKEN"] = gh_token

    env_to_forward["HOLON_REPO_URL"] = resolved_repo_url

    norm_agent_id = local_llm.normalize_agent_id(agent_id)

    # Ensure explicit role parameter takes strict precedence over host environment
    env_to_forward["HOLON_ROLE"] = role
    # The host already ran the readiness probe; nested invocations inside the container must not repeat it.
    env_to_forward["HOLON_SKIP_PREFLIGHT"] = "1"

    # Coherence for local mode: default HOLON_AGENT_PROVIDER from HOLON_LOCAL_PROVIDER if unset (pi runner only)
    if local_llm.local_llm_requested() and norm_agent_id == "pi":
        local_provider = env_to_forward.get(local_llm.ENV_LOCAL_PROVIDER, "").strip()
        if "HOLON_AGENT_PROVIDER" not in env_to_forward and local_provider:
            env_to_forward["HOLON_AGENT_PROVIDER"] = local_provider

    for key, value in sorted(env_to_forward.items()):
        add_env(key, value)

    # The token reaches in-container git through runtime-only config (GIT_CONFIG_COUNT, git 2.31+),
    # never through the URL or a file. It is scoped to the one host the token used to be embedded
    # for: a remote built from origin, or an override URL that carried it. Entry 0 resets any
    # inherited helpers for that host. The entries go through the same classifier as every other
    # variable: the helper's literal `password=` text sends it to the env-file, although it holds only
    # the `$GITHUB_TOKEN` reference.
    helper_host = credential_host(resolved_repo_url)
    if gh_token and helper_host and (not override_url or embedded_token):
        helper_key = f"credential.https://{helper_host}.helper"
        add_env("GIT_CONFIG_COUNT", "2")
        add_env("GIT_CONFIG_KEY_0", helper_key)
        add_env("GIT_CONFIG_VALUE_0", "")
        add_env("GIT_CONFIG_KEY_1", helper_key)
        add_env("GIT_CONFIG_VALUE_1", GIT_CREDENTIAL_HELPER)

    # SSH Agent Socket Mounts
    ssh_mounts, ssh_envs = get_ssh_auth_mounts()
    docker_cmd.extend(ssh_mounts)
    for k, v in ssh_envs.items():
        add_env(k, v)

    # Containers reach the host only through the gateway name; make it resolvable on Linux as it already is on
    # Docker Desktop, so agent base URLs rewritten to host.docker.internal work on every platform.
    docker_cmd.extend(_gateway_host_args())

    # Token Reduction Proxy & CA Mounts. From this point on the sidecar (and its network) may exist,
    # so every remaining exit path — early returns included — must run teardown, not just the final
    # subprocess.run.
    local_agent_dir: str | None = None
    env_file: str | None = None
    tr_mounts, tr_envs = get_token_reduction_mounts_and_envs(token_reduce=token_reduce, mitm_web=mitm_web)
    try:
        docker_cmd.extend(tr_mounts)

        # Host-local inference servers (Bean 0049): loopback and the host's own LAN address are unreachable from the
        # container namespace, so hand the agent a generated config whose authorities resolve via the gateway. The
        # artifact is a pi agent directory, so it is produced for pi only; other runners still need real credentials.
        if local_llm.local_llm_requested() and norm_agent_id != "pi" and role in ("planner", "executor"):
            print(
                f"Warning: host-local model mode applies to the pi runner only; ignoring it for '{agent_id}'.",
                file=sys.stderr,
            )
        if local_llm.local_llm_requested() and norm_agent_id == "pi":
            try:
                base_tmp = os.path.expanduser("~/.holon/tmp")
                os.makedirs(base_tmp, mode=0o755, exist_ok=True)
                local_agent_dir = tempfile.mkdtemp(prefix="holon-pi-agent-", dir=base_tmp)
                local_config = local_llm.prepare_agent_dir(
                    local_agent_dir,
                    host_config=local_llm.host_models_json(),
                    allow_list=local_llm.host_local_allow_list(),
                )
                # If provider was unset, determine whether to use the single distinct provider or "local"
                if "HOLON_AGENT_PROVIDER" not in env_to_forward:
                    providers = local_config.get("providers", {})
                    if len(providers) == 1:
                        chosen = next(iter(providers.keys()))
                    elif "local" in providers:
                        chosen = "local"
                    else:
                        raise local_llm.LocalLLMConfigError(
                            f"Multiple host-local providers found ({', '.join(sorted(providers))}). "
                            "Please set HOLON_AGENT_PROVIDER or HOLON_LOCAL_PROVIDER to select one."
                        )
                    add_env("HOLON_AGENT_PROVIDER", chosen)
            except local_llm.LocalLLMConfigError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            docker_cmd.extend(local_llm.container_mount_args(local_agent_dir))
            for key, value in local_llm.container_env(local_agent_dir).items():
                add_env(key, value)
            # That endpoint now points at the gateway, so it would otherwise be intercepted by the sidecar.
            local_hosts = local_llm.rewritten_local_hosts(local_config)
            if local_hosts and tr_envs.get("NO_PROXY"):
                exempted = list(local_hosts)
                # Some NO_PROXY implementations compare only the hostname, so a port-qualified entry can miss. Adding
                # the bare gateway name is safe when the sidecar is addressed by container name, but not when an
                # external proxy is reached through host.docker.internal itself.
                proxy_url = tr_envs.get("HTTP_PROXY", "")
                if (
                    token_reduce or mitm_web or bool(_sidecar_state.container_name)
                ) and local_llm.GATEWAY_HOST not in proxy_url:
                    exempted.insert(0, local_llm.GATEWAY_HOST)
                existing_no_proxy = [e.strip() for e in tr_envs.get("NO_PROXY", "").split(",") if e.strip()]
                joined = ",".join(dict.fromkeys(existing_no_proxy + exempted))
                tr_envs["NO_PROXY"] = joined
                tr_envs["no_proxy"] = joined
                print(
                    f"Note: host-local endpoint(s) {', '.join(local_hosts)} are exempt from token reduction "
                    "(no interception, caching, or wire telemetry for that traffic).",
                    file=sys.stderr,
                )

        for k, v in tr_envs.items():
            add_env(k, v)

        # Intent file mount for intent-creator role
        if role == "intent-creator":
            if not intent_file:
                print("Error: Intent file is mandatory for intent creation.", file=sys.stderr)
                return 1
            abs_intent = os.path.abspath(intent_file)
            if not os.path.exists(abs_intent):
                print(f"Error: Intent file '{intent_file}' does not exist.", file=sys.stderr)
                return 1
            docker_cmd.extend(["-v", f"{abs_intent}:/tmp/intent.json"])

        # Auto-detect Session Mounts
        session_mounts = get_agent_session_mounts(agent_id)
        docker_cmd.extend(session_mounts)

        # Network isolation and egress controls (Bean 0092)
        network_isolation_args = build_network_isolation_args(token_reduce=token_reduce)
        docker_cmd.extend(network_isolation_args)

        # Image and args
        docker_cmd.append(image_name)
        docker_cmd.extend(container_args)

        if secret_env:
            sweep_stale_env_files(env_file_dir())
            try:
                env_file = write_env_file(secret_env)
            except ValueError as exc:
                print(f"Error: {exc}.", file=sys.stderr)
                return 1
            # Right after `docker run`. Docker reads --env-file on the client side, so the host path
            # needs no bind mount.
            docker_cmd[2:2] = ["--env-file", env_file]

        print(f"Executing: {redact_command(docker_cmd)}")
        result = subprocess.run(docker_cmd)
        return result.returncode
    finally:
        if env_file:
            with contextlib.suppress(FileNotFoundError):
                os.remove(env_file)
        teardown_token_reduction_proxy()
        if local_agent_dir:
            shutil.rmtree(local_agent_dir, ignore_errors=True)
        try:
            from sandbox_executor.agent_runner import get_runner

            runner = get_runner(agent_id)
            if hasattr(runner, "cleanup_ephemeral_credentials"):
                runner.cleanup_ephemeral_credentials()
        except Exception:
            pass


def resolve_runner_and_model(agent: str | None, model: str | None) -> tuple[Resolution, Resolution]:
    """Resolve the agent runner and model for a command.

    The resolvers log each decision at INFO, which :func:`configure_cli_logging` routes to stderr.

    Args:
        agent: Raw ``--agent`` value (None when omitted).
        model: Raw ``--model`` value (None when omitted).

    Returns:
        ``(agent resolution, model resolution)``.

    Raises:
        UnmappedAgentRunnerError: If the selected runner has no container image.
    """
    agent_res = resolve_agent_runner(agent)
    model_res = resolve_model(agent_res.value, model)
    return agent_res, model_res


# Resumption points from which a flow still reaches the plan and execute stages, the ones that need the agent runner.
# Review (the native review loop) and calibration run only git, gh and local checks, so a flow resumed at either of
# them has nothing for the probe to protect.
_AGENT_FLOW_STAGES = ("intent", "plan", "execute")


def flow_dispatches_agent(from_stage: str | None) -> bool:
    """Return True when a flow starting at ``from_stage`` still reaches a stage that dispatches the agent runner.

    Args:
        from_stage: Resumption stage name, or None for a run from the start.

    Returns:
        False only when the flow resumes at a stage after the last agent-dispatching stage.
    """
    if not from_stage:
        return True
    return str(from_stage).strip().lower() in _AGENT_FLOW_STAGES


def preflight_gate(agent: str, model: str, skip: bool = False) -> PreflightResult | None:
    """Run the readiness probe unless skipped and print the diagnosis when it fails.

    Must be called before any branch, checkpoint or ledger write.

    Args:
        agent: Resolved runner name.
        model: Resolved model name.
        skip: True when ``--skip-preflight`` was given.

    Returns:
        The probe result, or None when the probe was skipped by flag or ``HOLON_SKIP_PREFLIGHT``.
    """
    if skip or preflight_skipped_by_env():
        logger.info("Pre-flight probe skipped (--skip-preflight or HOLON_SKIP_PREFLIGHT).")
        return None
    result = run_preflight_probe(agent, model, timeout=resolve_timeout())
    if not result.ok:
        print(result.format_report(), file=sys.stderr)
        print(
            "Aborting before any branch, checkpoint or ledger write. Use --skip-preflight to bypass this check.",
            file=sys.stderr,
        )
    return result


def run_doctor(args: argparse.Namespace) -> int:
    """Implement ``holon doctor``: resolve the runner and model, probe them and report.

    Args:
        args: Parsed namespace with ``agent``, ``model``, ``json`` and ``timeout``.

    Returns:
        0 when the probe reports ``ok``, otherwise 1.

    Side effects:
        One ``docker run`` with a minimal prompt. Writes only to stdout/stderr; never to the repository or ledgers.
    """
    import json

    try:
        # The text report prints both decisions on stdout, so the resolvers' INFO log would show them twice.
        agent_res = resolve_agent_runner(args.agent, log=False)
        model_res = resolve_model(agent_res.value, args.model, log=False)
    except UnmappedAgentRunnerError as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1

    result = run_preflight_probe(agent_res.value, model_res.value, timeout=resolve_timeout(args.timeout))
    if args.json:
        payload = {
            "agent": {"value": agent_res.value, "source": agent_res.source, "reason": agent_res.reason},
            "model": {"value": model_res.value, "source": model_res.source, "reason": model_res.reason},
            "preflight": result.to_dict(),
        }
        print(json.dumps(payload, indent=2))
    else:
        print(agent_res.describe("Agent runner"))
        print(model_res.describe("Model"))
        print(result.format_report())
    return 0 if result.ok else 1


def main() -> None:
    configure_cli_logging()
    parser = argparse.ArgumentParser(
        prog="holon",
        description="Holon CLI: Host wrapper for running containerized Holon AI agent roles.",
    )
    try:
        pkg_version = importlib.metadata.version("holon")
    except importlib.metadata.PackageNotFoundError:
        pkg_version = "0.1.0"
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {pkg_version}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: intent
    intent_parser = subparsers.add_parser("intent", help="Run Intent Creator to initialise a new intent branch.")
    intent_parser.add_argument("intent_file", help="Path to local intent JSON/YAML file (e.g. intents/my-task.json)")
    intent_parser.add_argument(
        "--repo-dir",
        default=None,
        help="Target repository directory path (default: current directory).",
    )
    intent_parser.add_argument(
        "--token-reduce",
        action="store_true",
        help=_TOKEN_REDUCE_HELP,
    )
    intent_parser.add_argument(
        "--mitm-web",
        action="store_true",
        help=(
            "Launch mitmweb dashboard on port 8081 (or HOLON_MITM_WEB_PORT) for real-time traffic inspection. "
            "For shared staging environments, pass HOLON_MITM_WEB_PASSWORD to restrict access."
        ),
    )

    # Subcommand: plan
    plan_parser = subparsers.add_parser("plan", help="Run Plan Generator to create a detailed markdown plan branch.")
    plan_parser.add_argument("intent_branch", help="Target intent branch name (e.g. I-1784983150-build-execution/_)")
    plan_parser.add_argument("--agent", default=None, help=_AGENT_HELP)
    plan_parser.add_argument("--model", default=None, help=_MODEL_HELP)
    plan_parser.add_argument("--skip-preflight", action="store_true", help=_SKIP_PREFLIGHT_HELP)
    plan_parser.add_argument(
        "--token-reduce",
        action="store_true",
        help=_TOKEN_REDUCE_HELP,
    )
    plan_parser.add_argument(
        "--mitm-web",
        action="store_true",
        help=(
            "Launch mitmweb dashboard on port 8081 (or HOLON_MITM_WEB_PORT) for real-time traffic inspection. "
            "For shared staging environments, pass HOLON_MITM_WEB_PASSWORD to restrict access."
        ),
    )
    plan_parser.add_argument(
        "--repo-dir",
        default=None,
        help="Target repository directory path (default: current directory).",
    )

    # Subcommand: execute
    exec_parser = subparsers.add_parser("execute", help="Run Sandbox Executor to execute code changes for a plan.")
    exec_parser.add_argument("plan_branch", help="Target plan branch name")
    exec_parser.add_argument("--agent", default=None, help=_AGENT_HELP)
    exec_parser.add_argument("--model", default=None, help=_MODEL_HELP)
    exec_parser.add_argument("--skip-preflight", action="store_true", help=_SKIP_PREFLIGHT_HELP)
    exec_parser.add_argument(
        "--token-reduce",
        action="store_true",
        help=_TOKEN_REDUCE_HELP,
    )
    exec_parser.add_argument(
        "--mitm-web",
        action="store_true",
        help=(
            "Launch mitmweb dashboard on port 8081 (or HOLON_MITM_WEB_PORT) for real-time traffic inspection. "
            "For shared staging environments, pass HOLON_MITM_WEB_PASSWORD to restrict access."
        ),
    )
    exec_parser.add_argument(
        "--repo-dir",
        default=None,
        help="Target repository directory path (default: current directory).",
    )

    # Subcommand: init
    init_parser = subparsers.add_parser(
        "init",
        help="Scaffold Holon configuration and knowledge directories in a target project.",
    )
    init_parser.add_argument(
        "target_dir",
        nargs="?",
        default=".",
        help="Target directory to initialise (default: current directory).",
    )
    init_parser.add_argument(
        "--template",
        choices=["python", "generic"],
        default="python",
        help="Project template type (default: python).",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing configuration files (ledger files are never overwritten).",
    )

    # Subcommand: calibrate
    calibrate_parser = subparsers.add_parser(
        "calibrate",
        help="Run post-execution plan calibration analysis on an execution branch.",
    )
    calibrate_parser.add_argument(
        "execution_branch",
        help="Target execution branch name (e.g. I-.../P-.../E-.../_)",
    )
    calibrate_parser.add_argument(
        "--json",
        action="store_true",
        help="Output calibration metrics as JSON to stdout.",
    )
    calibrate_parser.add_argument(
        "--repo-dir",
        default=".",
        help="Target repository directory path (default: current directory).",
    )
    calibrate_parser.add_argument(
        "--no-commit",
        dest="skip_commit",
        action="store_true",
        help="Generate report without creating a git branch or committing.",
    )

    # Subcommand: flow
    flow_parser = subparsers.add_parser(
        "flow",
        help="Run the automated 5-stage Holon Flow lifecycle pipeline.",
    )
    flow_parser.add_argument(
        "intent_file",
        nargs="?",
        default=None,
        help="Path to local intent JSON file (e.g. intents/my-task.json)",
    )
    flow_parser.add_argument(
        "--from-stage",
        choices=["intent", "plan", "execute", "review", "calibrate"],
        default=None,
        help="Resume pipeline from a specific stage.",
    )
    flow_parser.add_argument(
        "--checkpoint",
        default=None,
        help="Optional path to a flow checkpoint file.",
    )
    flow_parser.add_argument(
        "--agent", default=None, help=f"{_AGENT_HELP} A --checkpoint resume keeps the checkpoint's runner by default."
    )
    flow_parser.add_argument(
        "--model",
        default=None,
        help=f"{_MODEL_HELP} A --checkpoint resume keeps the checkpoint's model unless --agent switches the runner.",
    )
    flow_parser.add_argument("--skip-preflight", action="store_true", help=_SKIP_PREFLIGHT_HELP)
    flow_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate stages and branches without executing external agents or git commits.",
    )
    flow_parser.add_argument(
        "--json",
        action="store_true",
        help="Output pipeline status and stage results in structured JSON format to stdout.",
    )
    flow_parser.add_argument(
        "--repo-dir",
        default=".",
        help="Target repository directory path (default: current directory).",
    )
    flow_parser.add_argument(
        "--skip-push",
        action="store_true",
        help="Skip pushing git branches to remote origin.",
    )
    flow_parser.add_argument(
        "--skip-review",
        action="store_true",
        help="Skip Stage 4 (PR review loop) and proceed to calibration.",
    )
    flow_parser.add_argument(
        "--skip-calibrate",
        action="store_true",
        help="Skip Stage 5 (post-execution calibration report generation).",
    )
    flow_parser.add_argument(
        "--max-review-iterations",
        type=int,
        default=25,
        help="Maximum number of autonomous review loop iterations in Stage 4 (must be >= 1, default: 25).",
    )
    flow_parser.add_argument(
        "--token-reduce",
        action="store_true",
        help=_TOKEN_REDUCE_HELP,
    )
    flow_parser.add_argument(
        "--mitm-web",
        action="store_true",
        help=(
            "Launch mitmweb dashboard on port 8081 (configurable via HOLON_MITM_WEB_PORT) "
            "for real-time traffic inspection. Automatically enables --token-reduce."
        ),
    )

    # Subcommand: review-loop (Bean 0118)
    review_parser = subparsers.add_parser(
        "review-loop",
        aliases=["review", "pr-review-loop"],
        help="Run autonomous native review loop on an execution branch or pull request (Bean 0118).",
    )
    review_parser.add_argument(
        "target",
        help="Target execution branch name, PR number, or PR URL.",
    )
    review_parser.add_argument(
        "--max-iterations",
        type=int,
        default=25,
        help="Maximum number of review loop iterations (default: 25).",
    )
    review_parser.add_argument(
        "--ensemble-size",
        type=int,
        default=3,
        help="Number of review models/heuristics in evaluation ensemble (default: 3).",
    )
    review_parser.add_argument(
        "--auto-pr",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Automatically create PR if only a branch exists (default: True).",
    )
    review_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run review evaluation without applying changes or git commits.",
    )
    review_parser.add_argument(
        "--repo-dir",
        default=".",
        help="Target repository directory path (default: current directory).",
    )
    review_parser.add_argument(
        "--json",
        action="store_true",
        help="Output review loop results in JSON format.",
    )
    review_parser.add_argument(
        "--token-reduce",
        action="store_true",
        help=_TOKEN_REDUCE_HELP,
    )
    review_parser.add_argument("--agent", default=None, help=_AGENT_HELP)
    review_parser.add_argument("--model", default=None, help=_MODEL_HELP)
    review_parser.add_argument(
        "--local",
        action="store_true",
        help="Run review loop directly on host rather than inside Docker container.",
    )
    review_parser.add_argument(
        "--skip-push",
        action="store_true",
        help="Skip pushing git commits or branches to remote.",
    )
    review_parser.add_argument(
        "--mitm-web",
        action="store_true",
        help=(
            "Launch mitmweb dashboard on port 8081 (or HOLON_MITM_WEB_PORT) for real-time traffic inspection. "
            "For shared staging environments, pass HOLON_MITM_WEB_PASSWORD to restrict access."
        ),
    )

    # Subcommand: doctor
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check that the selected agent runner is ready (one minimal real model call in its container).",
    )
    doctor_parser.add_argument("--agent", default=None, help=_AGENT_HELP)
    doctor_parser.add_argument("--model", default=None, help=_MODEL_HELP)
    doctor_parser.add_argument("--json", action="store_true", help="Output the resolution and probe result as JSON.")
    doctor_parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Probe timeout in seconds (default: HOLON_PREFLIGHT_TIMEOUT, then 60).",
    )

    # Subcommand: ledger
    ledger_parser = subparsers.add_parser(
        "ledger",
        help="Query and inspect Holon ledgers.",
    )
    ledger_subparsers = ledger_parser.add_subparsers(
        dest="ledger_command",
        required=True,
    )
    query_parser = ledger_subparsers.add_parser(
        "query",
        help="Query action and summary records from execution ledger.",
    )
    query_parser.add_argument(
        "--ledger-file",
        default="holon-knowledge/ledger/executions.jsonl",
        help="Path to execution ledger JSONL file (default: holon-knowledge/ledger/executions.jsonl).",
    )
    query_parser.add_argument(
        "--entry-type",
        choices=["action", "summary", "all"],
        default="all",
        help="Filter records by entry type (action, summary, or all; default: all).",
    )
    query_parser.add_argument(
        "--category",
        choices=["code", "doc", "config", "test", "build", "dependency"],
        default=None,
        help="Filter action records by category.",
    )
    query_parser.add_argument(
        "--labels",
        default=None,
        help="Filter records by comma-separated labels (matches if any label exists).",
    )
    query_parser.add_argument(
        "--target",
        default=None,
        help="Filter records by substring match on target or plan branch.",
    )
    query_parser.add_argument(
        "--outcome",
        choices=["success", "failure", "reverted", "blocked"],
        default=None,
        help="Filter records by outcome or status.",
    )
    query_parser.add_argument(
        "--origin",
        choices=["initial_implementation", "pr_review", "ci_fix", "calibration", "operator"],
        default=None,
        help="Filter records by action origin.",
    )
    query_parser.add_argument(
        "--review-severity",
        choices=["critical", "important", "nit"],
        default=None,
        help="Filter records by review severity.",
    )
    query_parser.add_argument(
        "--query",
        "-q",
        dest="query",
        default=None,
        help="Case-insensitive substring search across action descriptions, summaries, and error contexts.",
    )
    query_parser.add_argument(
        "--keyword",
        dest="keyword",
        default=None,
        help="Alias for --query.",
    )
    query_parser.add_argument(
        "--json",
        action="store_true",
        help="Output matching records in structured JSON format.",
    )
    query_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of records to return.",
    )
    query_parser.add_argument(
        "--repo-dir",
        default=".",
        help="Target repository directory path (default: current directory).",
    )

    # Subcommand: consult
    consult_parser = subparsers.add_parser(
        "consult",
        help="Consult knowledge records and wisdom using multi-label queries (Bean 0052).",
    )
    consult_parser.add_argument(
        "--all",
        dest="all_labels",
        nargs="+",
        default=None,
        help="Conjunctive filter: records matching all specified labels.",
    )
    consult_parser.add_argument(
        "--any",
        dest="any_labels",
        nargs="+",
        default=None,
        help="Disjunctive filter: records matching any specified labels.",
    )
    consult_parser.add_argument(
        "--not",
        dest="not_labels",
        nargs="+",
        default=None,
        help="Negated filter: exclude records matching any of these labels.",
    )
    consult_parser.add_argument(
        "--query",
        "-q",
        dest="query",
        default=None,
        help="Full-text FTS5 search query.",
    )
    consult_parser.add_argument(
        "--top-k",
        dest="top_k",
        type=int,
        default=6,
        help="Maximum records to retrieve (ceiling 6).",
    )
    consult_parser.add_argument(
        "--max-tokens",
        dest="max_tokens",
        type=int,
        default=1200,
        help="Maximum total payload tokens injected (default: 1200).",
    )
    consult_parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured JSON results.",
    )
    consult_parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (table or json; default: table).",
    )
    consult_parser.add_argument(
        "--repo-dir",
        default=".",
        help="Target repository directory path (default: current directory).",
    )
    consult_parser.add_argument(
        "--db-path",
        default=None,
        help="Override SQLite index database path.",
    )

    # Subcommand: record
    record_parser = subparsers.add_parser(
        "record",
        help="Record a discrete action event to the ledger with credential scrubbing (Bean 0052).",
    )
    record_parser.add_argument(
        "--action",
        required=True,
        help="Description of the action performed.",
    )
    record_parser.add_argument(
        "--category",
        default="code",
        help="Action category (code, doc, config, test, build, dependency).",
    )
    record_parser.add_argument(
        "--target",
        default="",
        help="Action target or affected component.",
    )
    record_parser.add_argument(
        "--verdict",
        default="attempted",
        help="Action outcome verdict (attempted, success, failure, unknown, inconclusive, abandoned).",
    )
    record_parser.add_argument(
        "--labels",
        default=None,
        help="Comma-separated labels associated with this action.",
    )
    record_parser.add_argument(
        "--context",
        default=None,
        help="Error context or execution details.",
    )
    record_parser.add_argument(
        "--ledger-path",
        default="holon-knowledge/ledger/executions.jsonl",
        help="Path to actions ledger JSONL file.",
    )
    record_parser.add_argument(
        "--repo-dir",
        default=".",
        help="Repository root directory.",
    )

    # Subcommand: reconcile
    reconcile_parser = subparsers.add_parser(
        "reconcile",
        help="Reconcile pending action outcomes and expire stale unknown records (Bean 0052).",
    )
    reconcile_parser.add_argument(
        "--action-id",
        default=None,
        help="Target action ID to reconcile.",
    )
    reconcile_parser.add_argument(
        "--verdict",
        default=None,
        help="New terminal verdict (success, failure, superseded).",
    )
    reconcile_parser.add_argument(
        "--reason",
        default=None,
        help="Reason for late verdict reconciliation.",
    )
    reconcile_parser.add_argument(
        "--expire-ttl",
        type=int,
        default=14,
        help="Days after which unknown actions expire to unresolved (default: 14).",
    )
    reconcile_parser.add_argument(
        "--ledger-path",
        default="holon-knowledge/ledger/executions.jsonl",
        help="Path to actions ledger JSONL file.",
    )
    reconcile_parser.add_argument(
        "--repo-dir",
        default=".",
        help="Repository root directory.",
    )

    # Subcommand: promote
    promote_parser = subparsers.add_parser(
        "promote",
        help="Evaluate and promote qualified knowledge to KB and WB (Bean 0052).",
    )
    promote_parser.add_argument(
        "--pending",
        action="store_true",
        help="Promote all qualified pending candidates.",
    )
    promote_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate promotion without persisting files.",
    )
    promote_parser.add_argument(
        "--min-trials",
        type=int,
        default=2,
        help="Minimum execution trials required for promotion consideration (default: 2).",
    )
    promote_parser.add_argument(
        "--repo-dir",
        default=".",
        help="Repository root directory.",
    )

    # Subcommand: veto
    veto_parser = subparsers.add_parser(
        "veto",
        help="Inspect and manage deterministic failure vetoes (Bean 0052).",
    )
    veto_subparsers = veto_parser.add_subparsers(
        dest="veto_command",
        required=True,
    )
    veto_list_parser = veto_subparsers.add_parser(
        "list",
        help="List active and shadowed failure vetoes.",
    )
    veto_list_parser.add_argument(
        "--json",
        action="store_true",
        help="Output in JSON format.",
    )
    veto_list_parser.add_argument(
        "--repo-dir",
        default=".",
        help="Repository root directory.",
    )
    veto_lift_parser = veto_subparsers.add_parser(
        "lift",
        help="Manually lift an active or shadowed failure veto.",
    )
    veto_lift_parser.add_argument(
        "--context",
        required=True,
        help="Context fingerprint to lift.",
    )
    veto_lift_parser.add_argument(
        "--reason",
        default="Manual operator override",
        help="Audit reason for lifting veto.",
    )
    veto_lift_parser.add_argument(
        "--repo-dir",
        default=".",
        help="Repository root directory.",
    )

    # Subcommand: stats
    stats_parser = subparsers.add_parser(
        "stats",
        help="Display operational self-metrics and consult telemetry (Bean 0052).",
    )
    stats_parser.add_argument(
        "--json",
        action="store_true",
        help="Output telemetry in JSON format.",
    )
    stats_parser.add_argument(
        "--repo-dir",
        default=".",
        help="Repository root directory.",
    )

    args = parser.parse_args()

    if args.command == "flow":
        try:
            if getattr(args, "max_review_iterations", None) is not None and args.max_review_iterations < 1:
                print("Error: --max-review-iterations must be at least 1.", file=sys.stderr)
                sys.exit(1)

            context = None
            if args.checkpoint:
                if not os.path.exists(args.checkpoint):
                    print(f"Error: Checkpoint file '{args.checkpoint}' does not exist.", file=sys.stderr)
                    sys.exit(1)
                checkpoint = load_checkpoint(args.checkpoint)
                context = checkpoint.context
                if not args.from_stage and checkpoint.current_stage != FlowStage.COMPLETED:
                    args.from_stage = checkpoint.current_stage.value

            if context is None:
                intent_data = {}
                if args.intent_file:
                    if not os.path.exists(args.intent_file):
                        print(f"Error: Intent file '{args.intent_file}' does not exist.", file=sys.stderr)
                        sys.exit(1)
                    with open(args.intent_file, encoding="utf-8") as f:
                        import json

                        intent_data = json.load(f)
                agent_res, model_res = resolve_runner_and_model(args.agent, args.model)
                flow_agent, flow_model = agent_res.value, model_res.value
            else:
                # A resumed checkpoint keeps its recorded attribution; only explicit flags override it. Switching the
                # runner with --agent alone resolves that runner's model (HOLON_MODEL, then its default), because the
                # checkpoint's model belongs to the old runner. The resolvers log the values they decide; only the
                # values kept from the checkpoint are logged here, so each decision is reported once with its source.
                from_checkpoint = f"recorded in {args.checkpoint}"
                if args.agent is not None:
                    flow_agent = resolve_agent_runner(args.agent).value
                else:
                    flow_agent = context.agent
                    logger.info(Resolution(flow_agent, "checkpoint", from_checkpoint).describe("Agent runner"))
                if args.model is not None or (
                    args.agent is not None and normalise_agent_id(flow_agent) != normalise_agent_id(context.agent)
                ):
                    flow_model = resolve_model(flow_agent, args.model).value
                else:
                    flow_model = context.model
                    logger.info(Resolution(flow_model, "checkpoint", from_checkpoint).describe("Model"))

            # Readiness probe before Stage 1: nothing has been branched, pushed, checkpointed or recorded yet.
            if args.dry_run or (context is not None and context.dry_run):
                logger.info("Pre-flight probe skipped for a dry-run flow (no agent is called).")
            elif not flow_dispatches_agent(args.from_stage):
                logger.info(
                    "Pre-flight probe skipped: no remaining stage from '%s' dispatches an agent runner.",
                    args.from_stage,
                )
            else:
                preflight = preflight_gate(flow_agent, flow_model, skip=args.skip_preflight)
                if preflight is not None and not preflight.ok:
                    sys.exit(1)
                    return

            effective_token_reduce = detect_holon_coherence(bool(args.token_reduce or args.mitm_web))
            if context is None:
                context = FlowContext(
                    repo_dir=args.repo_dir,
                    intent_data=intent_data,
                    agent=flow_agent,
                    model=flow_model,
                    dry_run=args.dry_run,
                    skip_push=args.skip_push,
                    skip_review=args.skip_review,
                    skip_calibrate=args.skip_calibrate,
                    auto_calibrate=not args.skip_calibrate,
                    max_review_iterations=args.max_review_iterations,
                    token_reduce=effective_token_reduce,
                    mitm_web=args.mitm_web,
                )
            else:
                if args.repo_dir and args.repo_dir != ".":
                    context.repo_dir = args.repo_dir
                context.agent = flow_agent
                context.model = flow_model
                if args.dry_run:
                    context.dry_run = True
                if args.skip_push:
                    context.skip_push = True
                if args.skip_review:
                    context.skip_review = True
                if args.skip_calibrate:
                    context.skip_calibrate = True
                    context.auto_calibrate = False
                if args.max_review_iterations is not None:
                    context.max_review_iterations = args.max_review_iterations
                if effective_token_reduce:
                    context.token_reduce = True
                if args.mitm_web:
                    context.mitm_web = True

            result_dict = run_flow_pipeline(
                context=context,
                from_stage=args.from_stage,
                checkpoint_path=args.checkpoint,
            )

            if args.json:
                import json

                print(json.dumps(result_dict, indent=2))
            else:
                print("\n================== Holon Flow Summary ==================")
                stage_order = ["intent", "plan", "execute", "review", "calibrate"]
                for st in stage_order:
                    res = context.stage_results.get(st)
                    status_str = res.status.value.upper() if res else "NOT_RUN"
                    print(f"  Stage {st.upper():<10}: {status_str}")
                    if res and res.status.value == "failed" and res.error:
                        print(f"    -> Error: {res.error}")
                print("========================================================\n")
                review_res = context.stage_results.get("review")
                if review_res and review_res.payload.get("halted_for_human"):
                    print(
                        "[BEAN 0034 SAFETY HALT] Pipeline safely halted for human review (autonomous merge prohibited)."
                    )
                    if review_res.payload.get("human_approval_command"):
                        print(f"To approve manually, run: {review_res.payload['human_approval_command']}\n")

            has_failure = any(res.status.value == "failed" for res in context.stage_results.values())
            sys.exit(1 if has_failure else 0)
        except Exception as e:
            print(f"Error during flow execution: {redact_agent_secrets(str(e))}", file=sys.stderr)
            sys.exit(1)

    if args.command in ("review-loop", "review", "pr-review-loop"):
        try:
            if getattr(args, "max_iterations", 25) < 1:
                print("Error: --max-iterations must be at least 1.", file=sys.stderr)
                sys.exit(1)
                return
            if getattr(args, "ensemble_size", 3) < 1:
                print("Error: --ensemble-size must be at least 1.", file=sys.stderr)
                sys.exit(1)
                return

            effective_token_reduce = detect_holon_coherence(
                bool(getattr(args, "token_reduce", False) or getattr(args, "mitm_web", False))
            )

            # Routing deliberately ignores the resolved runner: --agent used to default to a literal, so a bare
            # `holon review-loop` always went to a container, and resolving first must not change that.
            is_local = (
                getattr(args, "local", False)
                or getattr(args, "dry_run", False)
                or getattr(args, "json", False)
                or bool(os.getenv("HOLON_ROLE"))
                or bool(os.getenv("HOLON_IN_SANDBOX"))
            )
            # Resolution is for attribution and image selection only.
            agent_res, model_res = resolve_runner_and_model(getattr(args, "agent", None), getattr(args, "model", None))
            args.agent, args.model = agent_res.value, model_res.value

            if is_local:
                from sandbox_executor.flow import run_review_loop_cli

                exit_code = run_review_loop_cli(args, token_reduce=effective_token_reduce)
                sys.exit(exit_code)
            else:
                agent_id = normalise_agent_id(args.agent)
                image_name = image_for_agent(args.agent)
                container_args = [
                    args.target,
                    args.agent,
                    args.model,
                    f"--max-iterations={args.max_iterations}",
                    f"--ensemble-size={args.ensemble_size}",
                ]
                if not getattr(args, "auto_pr", True):
                    container_args.append("--no-auto-pr")
                if getattr(args, "skip_push", False):
                    container_args.append("--skip-push")
                if getattr(args, "json", False):
                    container_args.append("--json")
                kwargs = {
                    "agent_id": agent_id,
                    "token_reduce": effective_token_reduce,
                }
                if getattr(args, "mitm_web", False):
                    kwargs["mitm_web"] = True
                if getattr(args, "repo_dir", None):
                    kwargs["repo_dir"] = args.repo_dir
                sys.exit(run_docker_container("review-loop", image_name, container_args, **kwargs))
        except Exception as e:
            print(f"Error during review-loop execution: {redact_agent_secrets(str(e))}", file=sys.stderr)
            sys.exit(1)

    if args.command == "calibrate":
        try:
            run_calibrate(
                execution_branch=args.execution_branch,
                repo_dir=args.repo_dir,
                json_output=args.json,
                skip_commit=args.skip_commit,
            )
            sys.exit(0)
        except Exception as e:
            print(f"Error during calibration: {redact_agent_secrets(str(e))}", file=sys.stderr)
            sys.exit(1)

    if args.command == "init":
        sys.exit(
            init_project(
                target_dir=args.target_dir,
                template=args.template,
                force=args.force,
            )
        )

    if args.command == "ledger" and args.ledger_command == "query":
        ledger_file = args.ledger_file
        if not os.path.isabs(ledger_file):
            ledger_file = os.path.join(args.repo_dir, ledger_file)

        if not os.path.exists(ledger_file):
            print(f"Error: Ledger file '{ledger_file}' does not exist.", file=sys.stderr)
            sys.exit(1)

        search_query = args.query or args.keyword
        results = query_ledger(
            ledger_path=ledger_file,
            entry_type=args.entry_type,
            category=args.category,
            labels=args.labels,
            target=args.target,
            outcome=args.outcome,
            origin=args.origin,
            review_severity=args.review_severity,
            query=search_query,
            limit=args.limit,
        )

        if args.json:
            import json

            print(json.dumps(results, indent=2))
        else:
            table = format_query_results_table(results)
            print(table)
        sys.exit(0)

    if args.command == "consult":
        db_path = args.db_path
        if not db_path:
            db_path = os.path.join(args.repo_dir, "holon-knowledge/.index/action_index.db")
        engine = ConsultEngine(project_db_path=db_path)
        result = engine.consult_records(
            all_labels=args.all_labels,
            any_labels=args.any_labels,
            not_labels=args.not_labels,
            query_text=args.query,
            top_k=args.top_k,
            max_tokens=args.max_tokens,
        )
        if args.json or args.format == "json":
            import json

            print(json.dumps(result.to_dict(), indent=2))
        else:
            print(format_consult_results_table(result))
        sys.exit(0)

    if args.command == "record":
        ledger_path = args.ledger_path
        if not os.path.isabs(ledger_path):
            ledger_path = os.path.join(args.repo_dir, ledger_path)
        labels_list = [lbl.strip() for lbl in args.labels.split(",") if lbl.strip()] if args.labels else []
        record_data = {
            "action": args.action,
            "category": args.category,
            "target": args.target,
            "verdict": args.verdict,
            "labels": labels_list,
            "error_context": args.context,
        }
        res = append_action_event(record_data, ledger_path=ledger_path)
        print(f"Recorded action: {res['action_id']} [{res['verdict']}]")
        sys.exit(0)

    if args.command == "reconcile":
        ledger_path = args.ledger_path
        if not os.path.isabs(ledger_path):
            ledger_path = os.path.join(args.repo_dir, ledger_path)
        rec_stats = reconcile_records(
            ledger_path=ledger_path,
            max_age_days=args.expire_ttl,
            target_action_id=args.action_id,
            verdict=args.verdict,
            reason=args.reason,
        )
        print(
            f"Reconciled: {rec_stats['reconciled']}, Expired: {rec_stats['expired']}, "
            f"Scanned: {rec_stats['total_scanned']}"
        )
        sys.exit(0)

    if args.command == "promote":
        db_path = os.path.join(args.repo_dir, "holon-knowledge/.index/action_index.db")
        db = ActionIndexDatabase(db_path=db_path)
        promoted = promote_pending_candidates(
            index_db=db,
            kb_dir=os.path.join(args.repo_dir, "holon-knowledge/kb"),
            wb_dir=os.path.join(args.repo_dir, "holon-knowledge/wisdom"),
            min_trials=args.min_trials,
            dry_run=args.dry_run,
        )
        mode = "Dry-run: candidate" if args.dry_run else "Promoted"
        print(f"{mode} knowledge items: {len(promoted)}")
        for item in promoted:
            item_id = item.get("kb_id") or item.get("wb_id")
            item_type = item.get("kb_type") or item.get("wb_type")
            name = item.get("payload", {}).get("name", "")
            print(f" - {item_id} [{item_type}]: {name}")
        sys.exit(0)

    if args.command == "veto":
        db_path = os.path.join(args.repo_dir, "holon-knowledge/.index/action_index.db")
        db = ActionIndexDatabase(db_path=db_path)
        if args.veto_command == "list":
            vetoes = list_vetoes(db)
            if args.json:
                import json

                print(json.dumps(vetoes, indent=2))
            else:
                if not vetoes:
                    print("No active or shadowed failure vetoes recorded.")
                else:
                    for v in vetoes:
                        shadow_str = "SHADOW" if v.get("is_shadow") else "ENFORCED"
                        print(
                            f" - [{v.get('state')}/{shadow_str}] Fingerprint: {v.get('context_fingerprint')} "
                            f"(reproductions: {v.get('reproduction_count')}, decay: {v.get('decay_weight'):.2f})"
                        )
            sys.exit(0)
        elif args.veto_command == "lift":
            lifted = lift_veto(db, context_fingerprint=args.context, reason=args.reason)
            if lifted:
                print(f"Successfully lifted veto for fingerprint: {args.context}")
            else:
                print(f"No active veto found for fingerprint: {args.context}", file=sys.stderr)
            sys.exit(0)

    if args.command == "stats":
        db_path = os.path.join(args.repo_dir, "holon-knowledge/.index/action_index.db")
        db = ActionIndexDatabase(db_path=db_path)
        telemetry_stats = TelemetryCollector.get_telemetry_stats(db)
        if args.json:
            import json

            print(json.dumps(telemetry_stats, indent=2))
        else:
            print(format_stats_dashboard(telemetry_stats))
        sys.exit(0)

    if args.command == "doctor":
        sys.exit(run_doctor(args))

    if args.command in ("plan", "execute"):
        try:
            agent_res, model_res = resolve_runner_and_model(args.agent, args.model)
        except UnmappedAgentRunnerError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
            return
        args.agent, args.model = agent_res.value, model_res.value
        preflight = preflight_gate(args.agent, args.model, skip=args.skip_preflight)
        if preflight is not None and not preflight.ok:
            sys.exit(1)
            return
        agent_id = normalise_agent_id(args.agent)

    if args.command == "intent":
        image_name = "holon/orchestrator"
        kwargs = {"agent_id": "antigravity", "intent_file": args.intent_file}
        if getattr(args, "token_reduce", False):
            kwargs["token_reduce"] = True
        if getattr(args, "mitm_web", False):
            kwargs["mitm_web"] = True
        if getattr(args, "repo_dir", None):
            kwargs["repo_dir"] = args.repo_dir
        sys.exit(run_docker_container("intent-creator", image_name, [], **kwargs))

    elif args.command == "plan":
        image_name = image_for_agent(args.agent)
        container_args = [args.intent_branch, args.agent, args.model]
        kwargs = {"agent_id": agent_id, "token_reduce": getattr(args, "token_reduce", False)}
        if getattr(args, "mitm_web", False):
            kwargs["mitm_web"] = True
        if getattr(args, "repo_dir", None):
            kwargs["repo_dir"] = args.repo_dir
        sys.exit(
            run_docker_container(
                "planner",
                image_name,
                container_args,
                **kwargs,
            )
        )

    elif args.command == "execute":
        image_name = image_for_agent(args.agent)
        container_args = [args.plan_branch, args.agent, args.model]
        kwargs = {"agent_id": agent_id, "token_reduce": getattr(args, "token_reduce", False)}
        if getattr(args, "mitm_web", False):
            kwargs["mitm_web"] = True
        if getattr(args, "repo_dir", None):
            kwargs["repo_dir"] = args.repo_dir
        sys.exit(
            run_docker_container(
                "executor",
                image_name,
                container_args,
                **kwargs,
            )
        )


if __name__ == "__main__":
    main()
