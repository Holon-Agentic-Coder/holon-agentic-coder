import os
import re
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import sandbox_executor.entrypoint.executor as executor_facade
from sandbox_executor.git import GitRepository
from sandbox_executor.redaction import redact_agent_secrets, redact_command_args

_MAX_PRINT_LEN: int = 5000
_MAX_PRINT_ARG_LEN: int = 250
_TRUTHY_FLAG_VALUES: tuple[str, ...] = ("1", "true", "yes", "on")
_ACTION_PROVENANCE_KEYS: frozenset[str] = frozenset(
    {"action_origin", "review_severity", "review_iteration", "review_finding_ids"}
)
_CLEAN_GIT_ENV_VARS: tuple[str, ...] = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


def _get_clean_git_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    return {k: v for k, v in (os.environ if base_env is None else base_env).items() if k not in _CLEAN_GIT_ENV_VARS}


def is_action_logging_enabled(intent_data: dict[str, Any] | None = None) -> bool:
    flag = os.getenv("HOLON_ACTION_LOGGING") or os.getenv("HOLON_RECORD_ACTIONS")
    if flag is not None:
        return flag.lower() in _TRUTHY_FLAG_VALUES
    if os.getenv("HOLON_ACTION_ORIGIN") or os.getenv("HOLON_REVIEW_SEVERITY"):
        return True
    if intent_data and (intent_data.keys() & _ACTION_PROVENANCE_KEYS):
        return True
    return "PYTEST_CURRENT_TEST" not in os.environ


_FALLBACK_SAFE_DIRECTORIES: set[str] = set()


def _get_local_safe_directories(repo_dir: str) -> list[str]:
    cmd = ["git", "-c", f"safe.directory={repo_dir}", "config", "--local", "--get-all", "safe.directory"]
    res = subprocess.run(cmd, cwd=repo_dir, capture_output=True, text=True)
    if res.returncode == 0:
        return [line.strip() for line in res.stdout.splitlines() if line.strip()]
    return []


def _inject_safe_directory_if_needed(cmd_args: list[str], cwd: str | None) -> list[str]:
    has_safe_dir = any(arg == "safe.directory" or arg.startswith("safe.directory=") for arg in cmd_args)
    if cwd and _is_safe_directory(cwd) and not has_safe_dir and len(cmd_args) > 1:
        return [cmd_args[0], "-c", f"safe.directory={cwd}", *cmd_args[1:]]
    return cmd_args


def run_cmd(
    args: list[str],
    cwd: str | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    redacted_args = redact_command_args(args)
    print_args = [arg[:_MAX_PRINT_ARG_LEN] + "..." if len(arg) > _MAX_PRINT_ARG_LEN else arg for arg in redacted_args]
    cmd_str = " ".join(print_args)
    if len(cmd_str) > _MAX_PRINT_LEN:
        cmd_str = cmd_str[: _MAX_PRINT_LEN - 3] + "..."
    print(f"Running: {cmd_str}")

    cmd_env = env
    cmd_args = list(args)
    if cmd_args and cmd_args[0] == "git":
        cmd_env = _get_clean_git_env(env)
        cmd_args = _inject_safe_directory_if_needed(cmd_args, cwd)

    result = subprocess.run(cmd_args, cwd=cwd, capture_output=True, text=True, env=cmd_env)
    if result.returncode != 0 and check:
        print(f"Command failed with code {result.returncode}", file=sys.stderr)
        print(f"Command args: {cmd_str}", file=sys.stderr)
        full_out = redact_agent_secrets(result.stdout)
        full_err = redact_agent_secrets(result.stderr)
        print(f"Stdout:\n{_clip_for_print(full_out)}", file=sys.stderr)
        print(f"Stderr:\n{_clip_for_print(full_err)}", file=sys.stderr)
        raise subprocess.CalledProcessError(result.returncode, redacted_args, output=full_out, stderr=full_err)
    return result


def _is_safe_directory(cwd: str) -> bool:
    norm_cwd = os.path.realpath(cwd)
    if norm_cwd in _FALLBACK_SAFE_DIRECTORIES:
        return True
    return any(sd == "*" or os.path.realpath(sd) == norm_cwd for sd in executor_facade._get_local_safe_directories(cwd))


def _clip_for_print(text: str) -> str:
    if len(text) <= _MAX_PRINT_LEN:
        return text
    half_print = _MAX_PRINT_LEN // 2
    return text[:half_print] + "\n... (truncated) ...\n" + text[-half_print:]


def _get_repo(repo_dir: str) -> GitRepository:
    return GitRepository(repo_dir, runner=executor_facade.run_cmd)


def _entrypoint_git_runner(repo_dir: str) -> Callable[[list[str]], str]:
    repo = _get_repo(repo_dir)

    def _run(args: list[str]) -> str:
        res = repo.run(args, check=False)
        if res.returncode != 0:
            raise RuntimeError(f"git {' '.join(args[:2])} failed: {redact_agent_secrets((res.stderr or '').strip())}")
        return res.stdout if isinstance(res.stdout, str) else ""

    return _run


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return default if val is None else float(val)
    except (ValueError, TypeError):
        return default


def _sanitize_string(name: str, fallback: str = "unknown") -> str:
    s = re.sub(r"\s+", "-", name.replace("/", "_"))
    s = re.sub(r"[^a-zA-Z0-9_.-]", "", s)
    s = re.sub(r"-+", "-", s)
    s = re.sub(r"\.+", ".", s)
    s = s[:64].strip(".-")
    return s or fallback
