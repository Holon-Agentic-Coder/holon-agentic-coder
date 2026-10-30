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
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TextIO
from urllib.parse import unquote, urlparse, urlsplit, urlunsplit

from sandbox_executor import local_llm
from sandbox_executor.agent_defaults import (
    AGENT_IMAGE_MAPPING as AGENT_IMAGE_MAPPING,  # re-exported: the mapping moved to agent_defaults
)
from sandbox_executor.agent_defaults import (
    FALLBACK_AGENT,
    Resolution,
    UnmappedAgentRunnerError,
    image_for_agent,
    normalise_agent_id,
    resolve_agent_runner,
    resolve_model,
)
from sandbox_executor.budgets import stage_budget_handdown_env
from sandbox_executor.calibration import run_calibrate
from sandbox_executor.entrypoint.executor import OVER_BUDGET_EXIT_CODE
from sandbox_executor.flow import (
    SKIP_PUSH_DISPATCH_ERROR,
    FlowContext,
    FlowStage,
    format_decomposition_summary,
    load_checkpoint,
    run_flow_pipeline,
)
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
    starts_with_url_authority,
    trusted_url_authority,
    url_userinfo_end,
)
from sandbox_executor.review_ensemble import DEFAULT_ENSEMBLE_SIZE, ENSEMBLE_SIZE_HELP
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

from sandbox_executor.cli.options import (  # noqa: E402
    _AGENT_HELP,
    _MODEL_HELP,
    _SKIP_PREFLIGHT_HELP,
    _TOKEN_REDUCE_HELP,
    add_preflight_option,
    add_repo_dir_option,
    add_runner_options,
    add_token_reduction_options,
)

__all__ = [
    "AGENT_IMAGE_MAPPING",
    "BLOCKED_METADATA_HOSTS",
    "DEFAULT_ENSEMBLE_SIZE",
    "ENSEMBLE_SIZE_HELP",
    "GIT_CREDENTIAL_HELPER",
    "OVER_BUDGET_EXIT_CODE",
    "PACKAGE_LOGGER_NAME",
    "SKIP_PUSH_DISPATCH_ERROR",
    "_AGENT_HELP",
    "_MODEL_HELP",
    "_SKIP_PREFLIGHT_HELP",
    "_TOKEN_REDUCE_HELP",
    "ActionIndexDatabase",
    "ConsultEngine",
    "FlowContext",
    "TelemetryCollector",
    "_CurrentStderrHandler",
    "_attach_external_proxy",
    "_find_git_root",
    "_proxy_host_port",
    "_sidecar_state",
    "add_preflight_option",
    "add_repo_dir_option",
    "add_runner_options",
    "add_token_reduction_options",
    "append_action_event",
    "build_parser",
    "configure_cli_logging",
    "credential_host",
    "detect_holon_coherence",
    "dispatch_role_agent",
    "env_file_dir",
    "find_github_token",
    "format_consult_results_table",
    "format_decomposition_summary",
    "format_query_results_table",
    "format_stats_dashboard",
    "get_agent_session_mounts",
    "get_host_proxy_envs",
    "get_token_reduction_mounts_and_envs",
    "init_project",
    "is_secret_env_item",
    "lift_veto",
    "list_vetoes",
    "load_checkpoint",
    "main",
    "normalise_agent_id",
    "normalize_repo_remote",
    "preflight_gate",
    "print_encodable",
    "promote_pending_candidates",
    "query_ledger",
    "reconcile_records",
    "redact_cli_error",
    "report_budgets_and_envelope",
    "resolve_role_image",
    "resolve_target_repo",
    "run_calibrate",
    "run_docker_container",
    "run_doctor",
    "run_flow_pipeline",
    "run_preflight_probe",
    "setup_token_reduction_proxy",
    "strip_url_credentials",
    "sweep_stale_env_files",
    "teardown_token_reduction_proxy",
    "url_userinfo_end",
    "write_env_file",
]


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
        try:
            port = parsed.port
        except ValueError:
            # urlparse's message quotes the bad port, which may be part of a password.
            raise ValueError(
                "Unable to parse the remote URL; its value is withheld because it may hold a credential."
            ) from None
        if port and port not in (22, 443):
            host = f"{host}:{port}"
        path = parsed.path.lstrip("/")
    else:
        m = re.match(r"^(?:(?P<user>[^@]+)@)?(?P<host>[^:]+):(?P<path>.+)$", clean_url)
        if not m:
            raise ValueError("Unable to parse the remote URL; its value is withheld because it may hold a credential.")
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
        URL. A password holding a raw `/`, `?` or `#` is stripped whole (see
        `redaction.url_userinfo_end`, the rule the echo redactor and env-file routing share).
    """
    if not url.lower().startswith(("https://", "http://")):
        return url, None
    scheme_end = url.index("://") + 3
    rest = url[scheme_end:]
    at = url_userinfo_end(rest)
    if at is None:
        return url, None
    userinfo = rest[:at]
    clean_url = urlunsplit(urlsplit(url[:scheme_end] + rest[at + 1 :]))
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
    return clean_url, credential


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


def get_agent_session_mounts(agent_id: str) -> list[str]:  # noqa: C901
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


_URL_ENV_NAME_RE = re.compile(r"(?:URL|URI|PROXY)$", re.IGNORECASE)
_URL_DELIMITER_SPLIT_RE = re.compile(r"[/?#]")


def _url_env_credential_parts() -> set[str]:
    """Return the userinfo credential of every URL-valued environment variable, whole and in pieces.

    A parse error can quote only the part of a password before its first raw `/`, `?` or `#`, which
    no shape-based redactor recognises, so each delimiter-separated piece of at least 4 characters
    is returned alongside the whole credential.
    """
    parts: set[str] = set()
    for name, value in os.environ.items():
        if not value or not _URL_ENV_NAME_RE.search(name):
            continue
        url = value if starts_with_url_authority(value) else f"//{value}"
        rest = url.partition("//")[2]
        at = url_userinfo_end(rest)
        if at is None:
            continue
        userinfo = rest[:at]
        user, colon, password = userinfo.partition(":")
        credential = password if colon else user
        for piece in (userinfo, credential, *_URL_DELIMITER_SPLIT_RE.split(credential)):
            if len(piece) >= 4:
                parts.add(piece)
    return parts


def redact_cli_error(message: object) -> str:
    """Redact an exception message before a top-level CLI handler prints or logs it.

    Applies `redact_agent_secrets`, then masks the userinfo credential of every URL-valued
    environment variable (and each piece of it between raw URL delimiters), so a parse error that
    quotes part of a configured password, such as `urlparse`'s "Port could not be cast" message,
    cannot echo it.

    Args:
        message: The exception (or any object) whose string form is printed.

    Returns:
        The redacted message.
    """
    text = redact_agent_secrets(str(message))
    for part in sorted(_url_env_credential_parts(), key=len, reverse=True):
        text = text.replace(part, "*******")
    return text


def detect_holon_coherence(token_reduce: bool = False) -> bool:  # noqa: C901, PLR0911, PLR0912
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
        if host_port is None:
            # Never echo the value: an unparseable URL has no reliable userinfo boundary to mask.
            logger.warning("HOLON_PROXY_URL is not a valid proxy URL; skipping the proxy port probe.")
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


def setup_token_reduction_proxy(mitm_web: bool = False) -> tuple[list[str], dict[str, str]]:  # noqa: C901, PLR0912, PLR0915
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

    addon_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "token_reduction", "mitm_addon.py"
    )
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

    src_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".."))

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
        # Never echo the value: an unparseable URL has no reliable userinfo boundary to mask.
        logger.warning(
            "holon-coherence proxy probe failed: HOLON_PROXY_URL is not a valid proxy URL; ignoring it. "
            "Proceeding with DIRECT egress fallback."
        )
        logger.error(
            "HOLON_TOKEN_REDUCE is enabled but HOLON_PROXY_URL is not a valid proxy URL; ignoring it. "
            "This run continues with DIRECT egress (no token reduction)."
        )
        return [], {}

    # Probe before generating: an unreachable proxy must not leave a fresh CA behind on a host that
    # is about to run with direct egress.
    ready = False
    probe_host = "127.0.0.1" if host_port[0] in ("host.docker.internal", "172.17.0.1", "localhost") else host_port[0]
    # Messages show the target only when its host was validated after the userinfo was removed;
    # otherwise the parsed host and port may be part of a credential, so only the variable is named.
    trusted = trusted_url_authority(proxy_url) is not None
    probe_target = f"{probe_host}:{host_port[1]}" if trusted else "the HOLON_PROXY_URL target"
    proxy_target = f"{host_port[0]}:{host_port[1]}" if trusted else "the HOLON_PROXY_URL target"
    try:
        ready = _wait_for_proxy(probe_host, host_port[1], timeout=PROXY_ATTACH_TIMEOUT_SECONDS)
    except (OSError, TimeoutError) as exc:
        logger.warning(
            "holon-coherence proxy probe failed at %s. Proceeding with DIRECT egress fallback.",
            probe_target,
        )
        logger.error(
            "HOLON_TOKEN_REDUCE is enabled but proxy probe failed at %s (%s: %s). "
            "This run continues with DIRECT egress (no token reduction).",
            probe_target,
            type(exc).__name__,
            redact_cli_error(exc),
        )
        return [], {}

    if not ready:
        logger.warning(
            "holon-coherence proxy probe failed at %s. Proceeding with DIRECT egress fallback.",
            proxy_target,
        )
        logger.error(
            "HOLON_TOKEN_REDUCE is enabled but no proxy accepted a TCP connection at %s. Start the proxy or "
            "point HOLON_PROXY_URL at it; this run continues with DIRECT egress (no token reduction).",
            proxy_target,
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
            redact_cli_error(exc),
        )
        logger.error(
            "Token reduction is enabled but could not be configured (%s: %s). This run continues with DIRECT "
            "egress (no TLS interception, no token reduction).",
            type(exc).__name__,
            redact_cli_error(exc),
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


def is_secret_env_item(key: str, value: str) -> bool:
    """Decide whether a container variable must travel in the 0600 `--env-file`, not on the argv.

    Args:
        key: The variable name.
        value: The variable value.

    Returns:
        True for a credential-bearing name, a `KEY=VALUE` item the command redactor would mask, or a
        value with a URL password (a proxy URL in any scheme, or none), whatever its name.
    """
    item = f"{key}={value}"
    return is_secret_env_name(key) or redact_command([item]) != item or carries_url_password(value)


def run_docker_container(  # noqa: C901, PLR0911, PLR0912, PLR0913, PLR0915
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
        print(redact_cli_error(exc), file=sys.stderr)
        return 1

    tty_flag = ["-it"] if sys.stdin.isatty() else ["-i"]
    docker_cmd = ["docker", "run", "--rm", *tty_flag]

    # Credentials never go on the docker command line, where `ps` and /proc/<pid>/cmdline can read
    # them: they are collected here and handed over in a 0600 --env-file just before the run. That
    # includes any value with a URL password (a proxy URL in any scheme, or none), whatever its name.
    secret_env: dict[str, str] = {}

    def add_env(key: str, value: str) -> None:
        if is_secret_env_item(key, value):
            secret_env[key] = value
        else:
            docker_cmd.extend(["-e", f"{key}={value}"])

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

    # Inside a timed host stage (holon flow), hand the container the time this stage has left so its own ceiling fires
    # first (Bean 0139); this replaces any forwarded host HOLON_STAGE_BUDGET_SECONDS. Every role-container dispatch goes
    # through here, so no caller has to pass it.
    env_to_forward.update(stage_budget_handdown_env())

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
                print_encodable(f"Error: {redact_cli_error(exc)}", file=sys.stderr)
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
            try:
                sweep_stale_env_files(env_file_dir())
                env_file = write_env_file(secret_env)
            except ValueError as exc:
                print(f"Error: {redact_cli_error(exc)}.", file=sys.stderr)
                return 1
            except OSError as exc:
                # An unwritable or unreadable ~/.holon/tmp/env-files: fail closed with a clear message
                # rather than a traceback, and never fall back to passing the credentials on the argv.
                print(f"Error: could not write the credential env-file: {redact_cli_error(exc)}.", file=sys.stderr)
                return 1
            # Right after `docker run`. Docker reads --env-file on the client side, so the host path
            # needs no bind mount.
            docker_cmd[2:2] = ["--env-file", env_file]

        # A forwarded HOLON_* value may hold a non-UTF-8 byte (surrogateescape); it must not crash the run here.
        print_encodable(f"Executing: {redact_command(docker_cmd)}")
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


def print_encodable(text: str, file: TextIO | None = None) -> None:
    """``print(text, file=file)`` that cannot raise ``UnicodeEncodeError``.

    Characters the stream's encoding cannot represent (a lone surrogate, a non-UTF-8 byte that ``os.environ`` decoded
    with ``surrogateescape``, or non-ASCII text on an ASCII terminal) are written backslash-escaped; text the stream
    can encode is written unchanged.
    """
    stream = sys.stdout if file is None else file
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        text = text.encode(encoding, "backslashreplace").decode(encoding)
    except LookupError:
        text = text.encode("ascii", "backslashreplace").decode("ascii")
    print(text, file=stream)


def _doctor_section_failed(section: str, exc: BaseException) -> str:
    """The warning line for a doctor section that raised unexpectedly: section and exception class only, no values."""
    return f"WARNING: doctor {section} check failed unexpectedly ({type(exc).__name__}); details withheld"


def report_budgets_and_envelope(fetch: local_llm.Fetcher | None = None, out: TextIO | None = None) -> int:
    """``holon doctor`` environment section: print the effective budgets and the local-LLM model envelope, and probe
    the local gateway (Bean 0140).

    Prints ``model=<id> context=<n> max_tokens=<n> source=host|synthesised`` and
    ``reasoning=<mode> source=env|host|default`` per model, then ``prefill_cache=off|cold|warm`` with memory figures
    once per distinct host-local server, warning when a model's emitted ``maxTokens`` or ``contextWindow`` exceeds
    the ``effective_defaults.max_output_tokens`` or ``max_prompt_tokens`` of the server that serves it. Nothing is
    written; only loopback or allow-listed endpoints are probed.

    Each part (budgets, envelope, base-URL check, gateway probe) is isolated: an invalid configuration prints an
    ``Error:`` line, and any unexpected exception becomes a value-free ``WARNING: doctor <part> check failed
    unexpectedly (<ExceptionClass>)`` line; either makes the result 1, and the doctor never raises from here.
    """
    env = os.environ
    out = out or sys.stdout
    code = 0
    try:
        _report_budgets(env, out)
    except Exception as exc:  # The doctor reports; it never crashes (#94 C21).
        print(_doctor_section_failed("budgets", exc), file=out)
        code = 1

    if env.get(local_llm.ENV_LOCAL_MODE, "").strip().lower() not in ("1", "true", "yes", "on"):
        print(f"local_llm=disabled (set {local_llm.ENV_LOCAL_MODE}=1 to inspect the model envelope)", file=out)
        return code

    try:
        allow_list = local_llm.host_local_allow_list()
        # One validated loader (local_llm.load_models_json): a malformed file is a LocalLLMConfigError naming it.
        host_config = local_llm.host_models_json()
        config, envelopes = local_llm.build_envelope_config(host_config, allow_list, env)
    except local_llm.LocalLLMConfigError as exc:
        # The same top-level redaction as the run path (#96); these lines are also the `--json` environment section.
        print(f"Error: {redact_cli_error(exc)}", file=out)
        return 1
    except Exception as exc:
        print(_doctor_section_failed("model envelope", exc), file=out)
        return 1

    for envelope in envelopes:
        print(envelope.envelope_line(), file=out)
        print(envelope.reasoning_line(), file=out)

    try:
        for source in local_llm.unparseable_base_url_sources(host_config, env):
            # Named by its source only (#96 message rule); like any non-probeable endpoint it is skipped, not fatal.
            print(f"WARNING: {source} is not a parseable URL, so its server is not probed (value withheld)", file=out)
    except Exception as exc:
        print(_doctor_section_failed("base URL", exc), file=out)
        code = 1

    try:
        _report_gateways(host_config, config, envelopes, env, allow_list, fetch, out)
    except Exception as exc:
        print(_doctor_section_failed("gateway probe", exc), file=out)
        code = 1
    return code


def _report_budgets(env: Mapping[str, str], out: TextIO) -> None:
    from sandbox_executor.budgets import load_budgets

    budgets = load_budgets(env)
    stage_minutes = " ".join(
        f"{name}={budgets.stage_seconds(name) / 60.0:g}min"
        for name in ("intent", "plan", "execute", "review_iteration", "calibration")
    )
    print(
        f"budgets turn={budgets.turn.turn_seconds:g}s stall={budgets.turn.stall_seconds:g}s {stage_minutes}",
        file=out,
    )


def _report_gateways(  # noqa: PLR0913
    host_config: dict | None,
    config: dict,
    envelopes: list[local_llm.ModelEnvelope],
    env: Mapping[str, str],
    allow_list: set[str],
    fetch: local_llm.Fetcher | None,
    out: TextIO,
) -> None:
    probed = False
    # One probe and one line per distinct server; limits are compared only with the models that server serves.
    for base_url, providers in local_llm.host_gateways(host_config, config, env, allow_list):
        health = local_llm.probe_gateway_health(base_url, fetch=fetch, allow_list=allow_list)
        if health is None:
            continue
        probed = True
        print(f"gateway={health.url} {health.cache_line()}", file=out)
        served = [envelope for envelope in envelopes if envelope.provider in providers]
        if health.max_output_tokens is not None:
            for envelope in served:
                if envelope.max_tokens > health.max_output_tokens:
                    print(
                        f"WARNING: model={envelope.model_id} max_tokens={envelope.max_tokens} exceeds the server's "
                        f"effective_defaults.max_output_tokens={health.max_output_tokens}",
                        file=out,
                    )
        if health.max_prompt_tokens is not None:
            for envelope in served:
                if envelope.context_window > health.max_prompt_tokens:
                    print(
                        f"WARNING: model={envelope.model_id} context={envelope.context_window} exceeds the server's "
                        f"max_prompt_tokens={health.max_prompt_tokens}",
                        file=out,
                    )
    if not probed:
        print("prefill_cache=unknown (no loopback or allow-listed endpoint to probe)", file=out)


INTENT_CREATOR_ROLE: str = "intent-creator"
INTENT_CREATOR_IMAGE: str = "holon/orchestrator"
# The intent-creator entrypoint never invokes a runner, so its container keeps the historical antigravity
# mount profile whatever runner the caller resolved.
INTENT_CREATOR_AGENT_ID: str = "antigravity"


def resolve_role_image(role: str, agent_id: str) -> str:
    """Resolve the container image that hosts a role agent.

    Args:
        role: Role name ('intent-creator', 'planner' or 'executor').
        agent_id: Runner name or normalised agent id (see agent_defaults.normalise_agent_id).

    Returns:
        str: 'holon/orchestrator' for the intent creator, otherwise the mapped 'holon/agent-<runner>' image.

    Raises:
        UnmappedAgentRunnerError: If a planner or executor runner has no mapped image.
    """
    if role == INTENT_CREATOR_ROLE:
        return INTENT_CREATOR_IMAGE
    return image_for_agent(agent_id)


def dispatch_role_agent(  # noqa: PLR0913
    role: str,
    runner: str | None,
    model: str | None,
    positional_args: list[str],
    *,
    repo_dir: str | None = None,
    intent_file: str | None = None,
    token_reduce: bool = False,
    mitm_web: bool = False,
) -> int:
    """Launch the containerised role agent shared by 'holon intent|plan|execute' and 'holon flow'.

    This is the single dispatch path for the authoring roles: the per-stage commands and the flow
    stages both call it, so the two invocations launch the same image with the same arguments.

    Args:
        role: Role name ('intent-creator', 'planner' or 'executor').
        runner: Resolved runner name (e.g. 'claude-agent'); selects the 'holon/agent-<runner>' image.
        model: Resolved model name. Planner and executor receive it through positional_args; the
            intent creator does not run a model.
        positional_args: Container arguments (e.g. [plan_branch, runner, model] for the executor).
        repo_dir: Optional target repository directory used to resolve the origin URL.
        intent_file: Intent payload path, mandatory for the intent creator.
        token_reduce: Whether to route container traffic through the token reduction proxy.
        mitm_web: Whether to start the proxy with the mitmweb interface.

    Returns:
        int: Container exit code (non-zero on failure).
    """
    del model  # Carried in positional_args for the roles that run a model.
    if role == INTENT_CREATOR_ROLE:
        kwargs: dict[str, Any] = {"agent_id": INTENT_CREATOR_AGENT_ID, "intent_file": intent_file}
        if token_reduce:
            kwargs["token_reduce"] = True
    else:
        kwargs = {"agent_id": normalise_agent_id(runner or FALLBACK_AGENT), "token_reduce": token_reduce}
    if mitm_web:
        kwargs["mitm_web"] = True
    if repo_dir:
        kwargs["repo_dir"] = repo_dir
    image_name = resolve_role_image(role, kwargs["agent_id"])
    return run_docker_container(role, image_name, list(positional_args), **kwargs)


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


# Resumption points from which a flow still reaches a stage that needs the agent runner: plan and execute, and review,
# whose loop needs the runner for its reviewer turns (on a host, Stage 4 refuses them until routing into the
# review-loop container lands). A flow resumed at review must preflight the runner too; only calibration (git and
# local checks) has nothing for the probe to protect.
_AGENT_FLOW_STAGES = tuple(s.value for s in FlowStage if s not in (FlowStage.CALIBRATE, FlowStage.COMPLETED))


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


def run_doctor(args: argparse.Namespace, fetch: local_llm.Fetcher | None = None) -> int:
    """Implement ``holon doctor``: one readiness report for the runner, the model and the local environment.

    It resolves the agent runner and model and runs the pre-flight probe on them, then reports the effective turn and
    stage budgets and, when local-LLM mode is enabled, the model envelope and the local gateway probe
    (:func:`report_budgets_and_envelope`). With ``--json`` the environment section is carried under ``environment``
    as its report lines, with its ``WARNING:`` lines also listed under ``warnings``.

    Args:
        args: Parsed namespace with ``agent``, ``model``, ``json`` and ``timeout``.
        fetch: Optional HTTP fetcher for the gateway probe (tests).

    Returns:
        0 when the probe reports ``ok`` and the environment section found no configuration error, otherwise 1.

    Side effects:
        One ``docker run`` with a minimal prompt and, in local-LLM mode, a health request to loopback or allow-listed
        gateways. Writes only to stdout/stderr; never to the repository or ledgers.
    """
    import io
    import json

    env_out = io.StringIO()
    try:
        env_code = report_budgets_and_envelope(fetch=fetch, out=env_out)
    except Exception as exc:  # Last resort; each part already isolates its own failures.
        print(_doctor_section_failed("environment", exc), file=env_out)
        env_code = 1
    env_lines = env_out.getvalue().splitlines()
    warnings = [line for line in env_lines if line.startswith("WARNING: ")]
    environment = {"ok": env_code == 0, "lines": env_lines, "warnings": warnings}

    try:
        # The text report prints both decisions on stdout, so the resolvers' INFO log would show them twice.
        agent_res = resolve_agent_runner(args.agent, log=False)
        model_res = resolve_model(agent_res.value, args.model, log=False)
    except UnmappedAgentRunnerError as exc:
        if args.json:
            print(json.dumps({"error": redact_cli_error(exc), "environment": environment}, indent=2))
        else:
            print_encodable(f"Error: {redact_cli_error(exc)}", file=sys.stderr)
            print_encodable("\n".join(env_lines))
        return 1

    result = run_preflight_probe(agent_res.value, model_res.value, timeout=resolve_timeout(args.timeout))
    if args.json:
        payload = {
            "agent": {"value": agent_res.value, "source": agent_res.source, "reason": agent_res.reason},
            "model": {"value": model_res.value, "source": model_res.source, "reason": model_res.reason},
            "preflight": result.to_dict(),
            "environment": environment,
        }
        print(json.dumps(payload, indent=2))
    else:
        # Encoding-safe: no configuration text can crash the report on a strict terminal (#94 C23).
        print_encodable(agent_res.describe("Agent runner"))
        print_encodable(model_res.describe("Model"))
        print_encodable(result.format_report())
        print_encodable("\n".join(env_lines))
    return 0 if result.ok and env_code == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser and register all discovered subcommands."""
    from sandbox_executor.cli.commands import discover

    try:
        pkg_version = importlib.metadata.version("holon")
    except importlib.metadata.PackageNotFoundError:
        pkg_version = "0.1.0"
    parser = argparse.ArgumentParser(
        prog="holon",
        description="Holon CLI: Host wrapper for running containerized Holon AI agent roles.",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {pkg_version}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for cmd in discover():
        cmd.register(subparsers)
    return parser


def main(argv: list[str] | None = None) -> None:
    """Run the Holon CLI."""
    configure_cli_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(args, "_run"):
        ret = args._run(args)
        if ret is not None:
            sys.exit(ret)
        return
    sys.exit(0)

