import atexit
import json
import logging
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from typing import Any

from sandbox_executor import local_llm

logger = logging.getLogger(__name__)

# Directory cleanup and root protection helpers
ALLOWED_PARENTS = {
    "/home",
    "/Users",
    "/tmp",
    "/var/tmp",
    "/private/var/folders",
    "/var/folders",
    os.path.expanduser("~/.holon-sandbox"),
    os.path.expanduser("~/.holon"),
}

_temp = tempfile.gettempdir()
if _temp != "/":
    ALLOWED_PARENTS.add(_temp)

ALLOWED_EXACT = {
    "/workspace",
    "/repo",
}

ALLOWED_PARENT_RESOLVED = {os.path.abspath(p) for p in ALLOWED_PARENTS} | {os.path.realpath(p) for p in ALLOWED_PARENTS}
ALLOWED_EXACT_RESOLVED = {os.path.abspath(e) for e in ALLOWED_EXACT} | {os.path.realpath(e) for e in ALLOWED_EXACT}


def _check_forbidden_root(path: str) -> None:
    abs_path = os.path.abspath(path)
    real_path = os.path.realpath(path)

    for p in (abs_path, real_path):
        if p == "/":
            raise RuntimeError(f"Refusing to perform operation on system root-level directory: {path}")

        p_allowed = False
        if p in ALLOWED_EXACT_RESOLVED:
            p_allowed = True
        else:
            for parent_p in ALLOWED_PARENT_RESOLVED:
                if p.startswith(parent_p.rstrip("/") + "/"):
                    p_allowed = True
                    break
        if not p_allowed:
            msg = f"Refusing to perform operation on system root-level directory: {path}"
            raise RuntimeError(msg)


def _handle_remove_readonly(func: Callable, path: str, *_args: Any) -> None:
    """Error handler for shutil.rmtree to handle read-only files/directories (e.g. git pack files)."""
    try:
        if os.path.isdir(path):
            os.chmod(path, stat.S_IWUSR | stat.S_IRUSR | stat.S_IXUSR, follow_symlinks=False)
        else:
            os.chmod(path, stat.S_IWUSR | stat.S_IRUSR, follow_symlinks=False)
    except (OSError, NotImplementedError) as err:
        logger.debug("Failed to set write permissions on %s: %s", path, err)
    func(path)


def _rmtree(path: str) -> None:
    """Helper to call shutil.rmtree with the onexc / onerror error handler."""
    import shutil

    if sys.version_info >= (3, 12):  # noqa: UP036
        shutil.rmtree(path, onexc=_handle_remove_readonly)
    else:
        shutil.rmtree(path, onerror=_handle_remove_readonly)


def _clear_dir_contents(path: str, raise_on_error: bool = False) -> None:
    """Clear all contents of a directory without removing the directory itself."""
    if not os.path.isdir(path):
        return

    try:
        _check_forbidden_root(path)
    except RuntimeError as e:
        if raise_on_error:
            raise
        print(f"Warning: {e}", file=sys.stderr)
        return

    try:
        items = os.listdir(path)
    except PermissionError as e:
        if raise_on_error:
            raise
        print(f"Warning: Failed to list directory {path}: {e}", file=sys.stderr)
        return

    for item in items:
        item_path = os.path.join(path, item)
        try:
            if os.path.islink(item_path) or not os.path.isdir(item_path):
                try:
                    os.unlink(item_path)
                except PermissionError:
                    if not os.path.islink(item_path):
                        os.chmod(item_path, stat.S_IWUSR | stat.S_IRUSR)
                    os.unlink(item_path)
            else:
                _rmtree(item_path)
        except Exception as e:
            if raise_on_error:
                raise
            print(f"Warning: Failed to remove {item_path}: {e}", file=sys.stderr)


def cleanup_repo_dir(repo_dir: str, raise_on_error: bool = False) -> None:
    """Clean up existing repo directory.

    Clears contents if mount, unlinks if symlink, otherwise removes the tree with read-only chmod handling.

    Args:
        repo_dir: Path to the repository directory.
        raise_on_error: If True, propagates exceptions. If False, prints a warning and continues.
    """
    if not os.path.lexists(repo_dir):
        return
    try:
        _check_forbidden_root(repo_dir)
        if os.path.ismount(repo_dir):
            _clear_dir_contents(repo_dir, raise_on_error=raise_on_error)
        elif os.path.islink(repo_dir):
            os.unlink(repo_dir)
        elif os.path.isdir(repo_dir):
            _rmtree(repo_dir)
        else:
            os.remove(repo_dir)
    except Exception as e:
        if raise_on_error:
            raise RuntimeError(f"Failed to clean up existing repo dir {repo_dir}: {e}") from e
        print(f"Warning: Failed to clean up repo dir {repo_dir}: {e}", file=sys.stderr)


def get_workspace_dir() -> str:
    """Returns the workspace directory path based on environment or sandbox status.

    Checks ``HOLON_REPO_DIR`` first, then determines if running in a sandbox environment
    (via ``HOLON_IN_SANDBOX``, ``HOLON_ROLE``, ``/.dockerenv``, or user heuristic) and returns
    ``~/.holon-sandbox/workspace`` if in a sandbox or ``~/.holon/repo`` otherwise.
    """
    repo_dir = os.getenv("HOLON_REPO_DIR")
    if repo_dir:
        return repo_dir

    in_sandbox_explicit = (
        str(os.getenv("HOLON_IN_SANDBOX", "")).lower() in ("1", "true", "yes")
        or bool(os.getenv("HOLON_ROLE"))
        or os.path.exists("/.dockerenv")
    )
    in_sandbox_heuristic = os.getenv("USER") == "holon" or os.getenv("USERNAME") == "holon"
    if in_sandbox_heuristic and not in_sandbox_explicit:
        logger.warning("using heuristic sandbox detection; set HOLON_IN_SANDBOX=1 to suppress this.")
    in_sandbox = in_sandbox_explicit or in_sandbox_heuristic

    return os.path.expanduser("~/.holon-sandbox/workspace") if in_sandbox else os.path.expanduser("~/.holon/repo")


_TRUTHY = ("1", "true", "yes", "on")


def skip_permissions_enabled() -> bool:
    """Return whether ``HOLON_AGENT_SKIP_PERMISSIONS`` (default true) asks runners to bypass permission prompts.

    Read on every call, so a runner honours the value current when its command is built rather than at import time.
    """
    return os.getenv("HOLON_AGENT_SKIP_PERMISSIONS", "true").lower() in _TRUTHY


class EnvMapping:
    """Defines a mapping from an environment variable to a command-line flag."""

    def __init__(self, env_var: str, flag: str, is_boolean: bool = False):
        self.env_var = env_var
        self.flag = flag
        self.is_boolean = is_boolean


class AgentRunner:
    """Base class for constructing and running agent CLI commands."""

    def __init__(self, agent_id: str, binary_name: str, model_flag: str):
        self.agent_id = agent_id
        self.binary_name = binary_name
        self.model_flag = model_flag
        self._resolved_version = None

    def build_cmd(self, model_name: str, prompt_file: str, intent_file: str, full_prompt: str) -> list[str]:
        raise NotImplementedError

    def compose_cmd(self, model_name: str, full_prompt: str) -> list[str]:
        """Assemble the agent argv without validating credentials (see :class:`StandardAgentRunner`)."""
        raise NotImplementedError

    def get_version(self) -> str:
        """Return agent version string, returning 'unknown' fallback if resolution fails."""
        raise NotImplementedError


class StandardAgentRunner(AgentRunner):
    """Runner for agents that accept prompt strings directly via arguments."""

    def __init__(
        self,
        agent_id: str,
        binary_name: str,
        model_flag: str,
        prefix: list[str] | None = None,
        suffix: list[str] | None = None,
        env_mappings: list[EnvMapping] | None = None,
        required_keys: list[str] | None = None,
        custom_validator: str | None = None,
        bypass_flags: list[str] | None = None,
    ):
        super().__init__(agent_id, binary_name, model_flag)
        self.prefix = prefix or []
        # Bypass argv, emitted only while skip_permissions_enabled(); never routed through any settings value.
        self.bypass_flags = bypass_flags or []
        self.suffix = suffix or []
        self.env_mappings = env_mappings or []
        self.required_keys = required_keys or []
        self.custom_validator = custom_validator
        self._ephemeral_files: list[str] = []

    def resolve_credentials(self) -> None:
        """Processes Tier 1 secret bundles to unpack configuration files."""
        # Ephemeral Secret Bundle Injection (HOLON_SECRET_BUNDLE_PATH or default /run/secrets/holon_auth.json)
        secret_bundle_path = os.getenv("HOLON_SECRET_BUNDLE_PATH", "/run/secrets/holon_auth.json")
        if os.path.exists(secret_bundle_path):
            try:
                import json

                with open(secret_bundle_path) as f:
                    bundle = json.load(f)
                target_agent = bundle.get("agent_id", "")
                if not target_agent or target_agent.lower() == self.agent_id:
                    api_key = bundle.get("api_key") or bundle.get("token")
                    if api_key:
                        os.environ["HOLON_AGENT_KEY"] = api_key

                    # Unpack session files into /home/holon/ if specified
                    config_files = bundle.get("config_files", {})
                    base_home = os.path.realpath(os.path.expanduser("~"))
                    if not base_home.endswith(os.sep):
                        base_home += os.sep
                    for rel_path, content in config_files.items():
                        full_dest = os.path.realpath(os.path.expanduser(rel_path))
                        if not full_dest.startswith(base_home):
                            raise ValueError(f"Path traversal detected in config_files path: {rel_path}")
                        os.makedirs(os.path.dirname(full_dest), exist_ok=True)
                        with open(full_dest, "w") as sf:
                            sf.write(content)
                        self._ephemeral_files.append(full_dest)
                    if self._ephemeral_files:
                        atexit.register(self.cleanup_ephemeral_credentials)
            except Exception as e:
                logger.warning(f"Failed to process secret bundle {secret_bundle_path}: {e}")

    def cleanup_ephemeral_credentials(self) -> None:
        """Securely wipe temporary secret bundles or session artifacts generated during the run."""
        for path in self._ephemeral_files:
            try:
                if os.path.exists(path):
                    if os.path.isdir(path):
                        _rmtree(path)
                    else:
                        os.remove(path)
            except Exception as e:
                logger.warning(f"Failed to clean up ephemeral file {path}: {e}")
        self._ephemeral_files.clear()

    def validate(self) -> None:
        """Validates that required environment variables or credentials exist across the 3-Tier Fallback Contract."""
        self.resolve_credentials()

        # Tier 2 & 3: Agent-specific environment variables and session directory fallbacks
        if self.custom_validator == "codex":
            if os.getenv("HOLON_AGENT_OSS_MODE") in ("true", "1"):
                return
            has_key = os.getenv("HOLON_AGENT_KEY")
            has_session = os.path.exists("/home/holon/.codex") or os.path.exists(os.path.expanduser("~/.codex"))
            if not (has_key or has_session):
                print(
                    "Error: Missing required credentials for agent 'codex'.\n"
                    "Please set 'HOLON_AGENT_KEY', set 'HOLON_AGENT_OSS_MODE=true', "
                    "or mount active credentials to '/home/holon/.codex'.",
                    file=sys.stderr,
                )
                sys.exit(1)
            return

        if self.custom_validator == "gemini":
            has_key = os.getenv("HOLON_AGENT_KEY")
            has_gcloud = os.path.exists("/home/holon/.config/gcloud") or os.path.exists(
                os.path.expanduser("~/.config/gcloud")
            )
            if not (has_key or has_gcloud):
                print(
                    "Error: Missing required API credentials for agent 'gemini'.\n"
                    "Please set 'HOLON_AGENT_KEY' or mount active gcloud credentials to '/home/holon/.config/gcloud'.",
                    file=sys.stderr,
                )
                sys.exit(1)
            return

        if self.custom_validator == "antigravity":
            has_key = os.getenv("HOLON_AGENT_KEY")
            has_session = (
                os.path.exists("/home/holon/.gemini/antigravity-cli")
                or os.path.exists(os.path.expanduser("~/.gemini/antigravity-cli"))
                or os.path.exists("/home/holon/.gemini")
                or os.path.exists(os.path.expanduser("~/.gemini"))
                or os.path.exists("/home/holon/.config/gcloud")
                or os.path.exists(os.path.expanduser("~/.config/gcloud"))
                or os.path.exists(os.path.expanduser("~/.holon/sessions/antigravity"))
            )
            if not (has_key or has_session):
                print(
                    "Error: Missing required API credentials for agent 'antigravity'.\n"
                    "Please set 'HOLON_AGENT_KEY' or mount active session to '/home/holon/.gemini'.",
                    file=sys.stderr,
                )
                sys.exit(1)
            return

        if self.required_keys:
            # A host-local inference server addressed through the Docker gateway needs no provider credential (Bean
            # 0049). Only accepted for pi -- the generated artifact is a pi agent directory, so a pi-only gate keeps a
            # stray opt-in from masking a missing cloud key for another runner -- and only when the local endpoint is
            # actually configured.
            if (
                self.agent_id in ("pi", "pi-agent")
                and local_llm.local_llm_requested()
                and (os.getenv(local_llm.ENV_PI_AGENT_DIR) or os.getenv(local_llm.ENV_LOCAL_BASE_URL))
            ):
                return

            pi_dirs = [
                "/home/holon/.pi/agent/models.json",
                "~/.pi/agent/models.json",
                "/home/holon/.config/pi/models.json",
                "~/.config/pi/models.json",
            ]
            env_pi_dir = os.getenv(local_llm.ENV_PI_AGENT_DIR, "").strip()
            if env_pi_dir:
                pi_dirs.insert(0, os.path.join(env_pi_dir, "models.json"))

            session_dirs = {
                "claude": ["/home/holon/.config/claude", "~/.config/claude"],
                # pi resolves providers from models.json; that single file is what the launcher mounts, so that is what
                # counts as an available session here -- not the surrounding directory, which holds auth.json and
                # transcripts.
                "pi": pi_dirs,
            }
            has_session_dir = False
            norm_id = local_llm.normalize_agent_id(self.agent_id)
            if norm_id in session_dirs:
                has_session_dir = any(
                    os.path.exists(p) or os.path.exists(os.path.expanduser(p)) for p in session_dirs[norm_id]
                )

            has_key = any(os.getenv(k) for k in self.required_keys)

            if not (has_key or has_session_dir):
                print(
                    f"Error: Missing required API credentials for agent '{self.agent_id}'.\n"
                    "Please set 'HOLON_AGENT_KEY', mount session credentials, or select a host-local model with "
                    f"'{local_llm.ENV_LOCAL_MODE}=1' plus a reachable endpoint.",
                    file=sys.stderr,
                )
                sys.exit(1)

    def compose_cmd(self, model_name: str, full_prompt: str) -> list[str]:
        """Assemble the agent argv without validating credentials or changing the environment.

        Used by :meth:`build_cmd` and by the host-side pre-flight probe, which runs the command inside the runner's
        container where the binary and credentials actually live.
        """
        prefix = [*self.prefix, *self.bypass_flags] if self.bypass_flags and skip_permissions_enabled() else self.prefix
        cmd = [self.binary_name, *prefix, self.model_flag, model_name, *self.suffix]
        self._append_env_mappings(cmd)
        cmd.append(full_prompt)
        return cmd

    def _append_env_mappings(self, cmd: list[str]) -> None:
        for mapping in self.env_mappings:
            val = os.getenv(mapping.env_var)
            if not val:
                continue
            if mapping.is_boolean:
                if val.lower() in ("true", "1", "yes", "on"):
                    cmd.append(mapping.flag)
            else:
                cmd.extend([mapping.flag, val])

    def build_cmd(self, model_name: str, prompt_file: str, intent_file: str, full_prompt: str) -> list[str]:
        self.validate()

        # Coherence with host-local model provider: default HOLON_AGENT_PROVIDER from HOLON_LOCAL_PROVIDER or "local"
        if (
            local_llm.normalize_agent_id(self.agent_id) == "pi"
            and local_llm.local_llm_requested()
            and not os.getenv("HOLON_AGENT_PROVIDER")
        ):
            local_prov = os.getenv(local_llm.ENV_LOCAL_PROVIDER, "").strip()
            if not local_prov:
                agent_dir = os.getenv(local_llm.ENV_PI_AGENT_DIR, "")
                models_path = os.path.join(agent_dir, "models.json") if agent_dir else ""
                if models_path and os.path.isfile(models_path):
                    try:
                        with open(models_path, encoding="utf-8") as f:
                            m_data = json.load(f)
                        provs = m_data.get("providers", {})
                        if len(provs) == 1:
                            local_prov = next(iter(provs.keys()))
                        elif "local" in provs:
                            local_prov = "local"
                    except Exception:
                        pass
            if local_prov:
                os.environ["HOLON_AGENT_PROVIDER"] = local_prov

        return self.compose_cmd(model_name, full_prompt)

    def get_version(self) -> str:
        """Return agent version string, returning 'unknown' fallback if resolution fails."""
        if self._resolved_version is not None:
            return self._resolved_version

        for arg in ["--version", "-v", "version"]:
            try:
                result = subprocess.run(
                    [self.binary_name, arg],
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
                if result.returncode != 0:
                    continue
                output = (result.stdout or "") + (result.stderr or "")
                match = re.search(r"(\d+\.\d+\.\d+)", output)
                if match:
                    self._resolved_version = match.group(1)
                    return self._resolved_version
            except subprocess.TimeoutExpired:
                logger.debug("Version check timed out for agent %s", self.agent_id)
                continue
            except Exception as exc:
                logger.debug("Failed to query version for agent %s: %s", self.agent_id, exc)
                continue

        self._resolved_version = "unknown"
        return self._resolved_version


class AntigravityAgentRunner(StandardAgentRunner):
    """Runner for the Antigravity agent.

    Defers evaluation of ``HOLON_AGENT_EFFORT`` and ``HOLON_AGENT_SKIP_PERMISSIONS``
    environment variables to :meth:`build_cmd` so that runtime changes to the variables
    are always respected instead of being frozen at module import time.
    """

    def compose_cmd(self, model_name: str, full_prompt: str) -> list[str]:
        # Resolve HOLON_AGENT_EFFORT and optional permissions bypass at call time
        prefix = list(self.prefix)
        suffix = list(self.suffix)
        if skip_permissions_enabled():
            prefix.append("--dangerously-skip-permissions")
        suffix.extend(["--effort", os.getenv("HOLON_AGENT_EFFORT", "medium"), "-p"])

        cmd = [self.binary_name, *prefix, self.model_flag, model_name, *suffix]
        self._append_env_mappings(cmd)
        cmd.append(full_prompt)
        return cmd

    def build_cmd(self, model_name: str, prompt_file: str, intent_file: str, full_prompt: str) -> list[str]:
        self.validate()
        return self.compose_cmd(model_name, full_prompt)


# Runner registry: maps agent_id -> StandardAgentRunner instance.
#
# Architectural assumption: one agent per sandbox container execution.
# HOLON_AGENT_PROVIDER is shared across runners that use it (e.g. pi-agent),
# but since only a single agent is active per container, a single HOLON_AGENT_PROVIDER
# value is always unambiguous at runtime. If multi-agent orchestration is ever needed,
# per-agent provider overrides would need to be introduced.
runners = {
    "pi": StandardAgentRunner(
        "pi",
        "pi",
        "--model",
        prefix=["-p"],
        env_mappings=[
            # HOLON_AGENT_PROVIDER selects the backend provider (e.g. anthropic, openai).
            # Auth is handled by HOLON_AGENT_KEY, which _apply_generic_token maps to PI_API_KEY
            # internally so the pi CLI can authenticate via its native env var.
            EnvMapping("HOLON_AGENT_PROVIDER", "--provider"),
        ],
        required_keys=["HOLON_AGENT_KEY"],
    ),
    "claude": StandardAgentRunner(
        "claude",
        "claude",
        "--model",
        suffix=["-p"],
        # Print mode refuses every write unless bypass is both allowed and selected; both are plain CLI parameters.
        bypass_flags=["--allow-dangerously-skip-permissions", "--permission-mode", "bypassPermissions"],
        env_mappings=[
            EnvMapping("HOLON_AGENT_SETTINGS", "--settings"),
        ],
        required_keys=["HOLON_AGENT_KEY"],
    ),
    "gemini": StandardAgentRunner(
        "gemini",
        "gemini",
        "--model",
        suffix=["-p"],
        custom_validator="gemini",
    ),
    "opencode": StandardAgentRunner(
        "opencode",
        "opencode",
        "--model",
        prefix=["run"],
        env_mappings=[
            # HOLON_AGENT_MODE selects the opencode sub-agent (e.g. code, architect).
            EnvMapping("HOLON_AGENT_MODE", "--agent"),
        ],
        required_keys=["HOLON_AGENT_KEY"],
    ),
    "codex": StandardAgentRunner(
        "codex",
        "codex",
        "-m",
        prefix=["exec"],
        env_mappings=[
            # HOLON_AGENT_OSS_MODE=true enables offline/open-source mode (no API key required).
            EnvMapping("HOLON_AGENT_OSS_MODE", "--oss", is_boolean=True),
            EnvMapping("HOLON_AGENT_LOCAL_PROVIDER", "--local-provider"),
            EnvMapping("HOLON_AGENT_CONFIG", "-c"),
        ],
        custom_validator="codex",
    ),
    "antigravity": AntigravityAgentRunner(
        "antigravity",
        "agy",
        "--model",
        custom_validator="antigravity",
    ),
}


def get_runner(agent_name: str) -> AgentRunner:
    """Helper function to parse the agent name and return the appropriate AgentRunner instance."""
    agent_id = agent_name.lower().replace("-agent", "").replace("agent-", "")
    if agent_id not in runners:
        supported = ", ".join(sorted(runners.keys()))
        print(f"Error: Unsupported agent '{agent_name}'. Supported agents are: {supported}")
        sys.exit(1)
    return runners[agent_id]


def get_repo_url() -> str:
    """Get the target repository URL for git operations from HOLON_REPO_URL.

    Returns:
        The repository URL string from the HOLON_REPO_URL environment variable.

    Raises:
        RuntimeError: If HOLON_REPO_URL is unset or empty.
    """
    repo_url = os.getenv("HOLON_REPO_URL")
    if repo_url and repo_url.strip():
        return repo_url.strip()

    raise RuntimeError(
        "HOLON_REPO_URL environment variable is not set or empty. "
        "In containerized execution, HOLON_REPO_URL is normally populated dynamically by the host CLI wrapper (holon). "
        "Please run via the holon CLI wrapper or export HOLON_REPO_URL explicitly."
    )
