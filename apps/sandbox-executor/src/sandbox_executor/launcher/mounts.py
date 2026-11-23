"""Session mounts, network isolation arguments and GitHub token discovery for role containers."""

import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping

from sandbox_executor import local_llm
from sandbox_executor.launcher.antigravity_mounts import antigravity_session_mounts
from sandbox_executor.launcher.pi_mounts import get_pi_mounts

logger = logging.getLogger(__name__)

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


#: Runs ``gh auth token``-style argv and returns the completed process.
CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]
_GH_TIMEOUT = 10.0


def _run_gh(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=_GH_TIMEOUT, check=False)


def resolve_github_token(
    environ: Mapping[str, str] | None = None, run: CommandRunner | None = None
) -> tuple[str | None, str]:
    """The token and its source: ``GITHUB_TOKEN``, then ``GH_TOKEN``, then ``gh auth token``; ``(None, "none")``.

    The one resolver for the token handed to the sandbox, so the token scope pre-flight and ``holon doctor`` check
    exactly that token.
    """
    env = os.environ if environ is None else environ
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        if env.get(name):
            return env[name], name
    if run is None:
        if not shutil.which("gh"):
            return None, "none"
        run = _run_gh
    try:
        res = run(["gh", "auth", "token"])
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("Failed to retrieve token via gh CLI: %s", e)
        return None, "none"
    token = (res.stdout or "").strip() if res.returncode == 0 and isinstance(res.stdout, str) else ""
    return (token, "gh") if token else (None, "none")


def find_github_token() -> str | None:
    """Auto-detect GitHub token from environment variables or gh CLI."""
    return resolve_github_token()[0]


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

        # Prefer ~/.holon/sessions/antigravity, then fall back to host ~/.gemini/antigravity-cli
        if os.path.exists(dedicated_session):
            return antigravity_session_mounts(dedicated_session)

        # On macOS, ~/.holon/sessions/antigravity is required if not using HOLON_AGENT_KEY
        if sys.platform == "darwin" and not has_key:
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

        host_agy_cli = os.path.join(home, ".gemini", "antigravity-cli")
        if os.path.exists(host_agy_cli):
            return antigravity_session_mounts(host_agy_cli)

        return mounts

    session_mapping = {
        "claude": [
            (os.path.join(home, ".config/claude"), "/home/holon/.config/claude"),
        ],
        "codex": [
            (os.path.join(home, ".codex"), "/home/holon/.codex"),
        ],
    }

    norm_agent_id = local_llm.normalize_agent_id(agent_id)
    dirs = session_mapping.get(norm_agent_id, [])
    # In host-local model mode the container receives a generated agent directory whose base URLs are reachable from it,
    # so the host's own provider config is deliberately not mounted.
    if norm_agent_id == "pi":
        return mounts if local_llm.local_llm_requested() else get_pi_mounts(home)
    for host_path, container_path in dirs:
        if os.path.exists(host_path):
            mounts.extend(["-v", f"{host_path}:{container_path}:ro"])
            if norm_agent_id == "codex" and container_path == "/home/holon/.codex":
                mounts.extend(["--tmpfs", "/home/holon/.codex/log:uid=1000,gid=1000"])
                mounts.extend(["--tmpfs", "/home/holon/.codex/cache:uid=1000,gid=1000"])

    return mounts
