import logging
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping

from sandbox_executor.runners import runner_spec_for
from sandbox_executor.runners.antigravity import antigravity_session_mounts as antigravity_session_mounts
from sandbox_executor.runners.pi import get_pi_mounts as get_pi_mounts

logger = logging.getLogger(__name__)

BLOCKED_METADATA_HOSTS: list[str] = [
    "169.254.169.254",
    "metadata.google.internal",
    "instance-data",
]
SINKHOLE_IP: str = "127.0.0.1"

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


def get_agent_session_mounts(agent_id: str) -> list[str]:
    spec = runner_spec_for(agent_id)
    if spec is None:
        return []
    return spec.build_session_mounts(os.path.expanduser("~"))
