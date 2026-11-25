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
_ANTIGRAVITY_AGENT_IDS: tuple[str, ...] = ("antigravity", "antigravity-agent")
_CODEX_CONTAINER_DIR = "/home/holon/.codex"

CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]
_GH_TIMEOUT = 10.0


def build_network_isolation_args(
    token_reduce: bool = False,
    network_mode: str | None = None,
) -> list[str]:
    args = [
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
    ]

    for host in BLOCKED_METADATA_HOSTS:
        args.extend(["--add-host", f"{host}:{SINKHOLE_IP}"])

    if network_mode and not token_reduce:
        args.extend(["--network", network_mode])

    return args


def _run_gh(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=_GH_TIMEOUT, check=False)


def resolve_github_token(
    environ: Mapping[str, str] | None = None, run: CommandRunner | None = None
) -> tuple[str | None, str]:
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
    return resolve_github_token()[0]


def _abort_missing_macos_antigravity_session(dedicated_session: str) -> None:
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


def _antigravity_mounts(home: str) -> list[str]:
    dedicated_session = os.getenv(
        "HOLON_ANTIGRAVITY_SESSION_DIR",
        os.path.join(home, ".holon", "sessions", "antigravity"),
    )
    has_key = bool(os.getenv("HOLON_AGENT_KEY"))

    if os.path.exists(dedicated_session):
        return antigravity_session_mounts(dedicated_session)

    if sys.platform == "darwin" and not has_key:
        _abort_missing_macos_antigravity_session(dedicated_session)

    host_agy_cli = os.path.join(home, ".gemini", "antigravity-cli")
    if os.path.exists(host_agy_cli):
        return antigravity_session_mounts(host_agy_cli)

    return []


def _standard_session_mounts(home: str, norm_agent_id: str) -> list[str]:
    session_mapping = {
        "claude": [
            (os.path.join(home, ".config/claude"), "/home/holon/.config/claude"),
        ],
        "codex": [
            (os.path.join(home, ".codex"), _CODEX_CONTAINER_DIR),
        ],
    }

    mounts: list[str] = []
    for host_path, container_path in session_mapping.get(norm_agent_id, []):
        if os.path.exists(host_path):
            mounts.extend(["-v", f"{host_path}:{container_path}:ro"])
            if norm_agent_id == "codex" and container_path == _CODEX_CONTAINER_DIR:
                mounts.extend(["--tmpfs", f"{_CODEX_CONTAINER_DIR}/log:uid=1000,gid=1000"])
                mounts.extend(["--tmpfs", f"{_CODEX_CONTAINER_DIR}/cache:uid=1000,gid=1000"])
    return mounts


def get_agent_session_mounts(agent_id: str) -> list[str]:
    home = os.path.expanduser("~")
    if agent_id.lower() in _ANTIGRAVITY_AGENT_IDS:
        return _antigravity_mounts(home)

    norm_agent_id = local_llm.normalize_agent_id(agent_id)
    if norm_agent_id == "pi":
        return [] if local_llm.local_llm_requested() else get_pi_mounts(home)

    return _standard_session_mounts(home, norm_agent_id)
