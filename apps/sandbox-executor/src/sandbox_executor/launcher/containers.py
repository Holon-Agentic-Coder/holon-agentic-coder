import contextlib
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from typing import TypedDict, Unpack

from sandbox_executor import agent_runner, git_flow, local_llm
from sandbox_executor.agent_runner import HOST_WORKTREE_ROLES, SANDBOX_WORKSPACE_MOUNT
from sandbox_executor.budgets import ENV_REPLAN_FEEDBACK, ENV_REPLAN_FEEDBACK_FILE
from sandbox_executor.launcher import container_env, env_files, messages, proxy_paths, run_tracking, sidecar
from sandbox_executor.launcher import mounts as mount_args
from sandbox_executor.redaction import redact_command

_ENV_FILE_FLAG_INSERT_INDEX = 2
_INTENT_CONTAINER_PATH = "/tmp/intent.json"
_REPLAN_FEEDBACK_CONTAINER_PATH = "/tmp/replan_feedback.txt"
_RESULT_CONTAINER_PATH = "/tmp/review_result.json"
_LOCAL_LLM_WARNING_ROLES = ("planner", "executor")


class ContainerLaunchOptions(TypedDict, total=False):
    intent_file: str | None
    token_reduce: bool
    mitm_web: bool
    proxy_attach: bool
    target_worktree: str | None
    result_file: str | None


class _LaunchAborted(Exception):
    def __init__(self, exit_code: int) -> None:
        super().__init__(exit_code)
        self.exit_code = exit_code


@dataclass
class ContainerLaunch:
    role: str
    image_name: str
    container_args: list[str]
    agent_id: str
    intent_file: str | None = None
    token_reduce: bool = False
    mitm_web: bool = False
    proxy_attach: bool = False
    target_worktree: str | None = None
    result_file: str | None = None
    docker_cmd: list[str] = field(default_factory=list, init=False)
    secret_env: dict[str, str] = field(default_factory=dict, init=False)
    passthrough_env: dict[str, str] = field(default_factory=dict, init=False)
    forwarded_env: dict[str, str] = field(default_factory=dict, init=False)
    workspace_mounts: list[str] = field(default_factory=list, init=False)
    env_file_path: str | None = field(default=None, init=False)
    local_agent_dir: str | None = field(default=None, init=False)
    replan_feedback_host_path: str | None = field(default=None, init=False)
    _owned_replan_feedback_file: str | None = field(default=None, init=False)

    def run(self) -> int:
        if not shutil.which("docker"):
            print("Error: 'docker' CLI command not found. Please install Docker.", file=sys.stderr)
            return 1
        try:
            self.workspace()
        except _LaunchAborted as aborted:
            return aborted.exit_code
        try:
            self.credentials()
            self.docker_cmd.extend(proxy_paths._gateway_host_args())

            tr_mounts, tr_envs = sidecar.get_token_reduction_mounts_and_envs(
                token_reduce=self.token_reduce, mitm_web=self.mitm_web, proxy_attach=self.proxy_attach
            )
            self.mounts(tr_mounts, tr_envs)
            self.argv()
            self.env_file()
            messages.print_encodable(f"Executing: {redact_command(self.docker_cmd)}")
            return run_tracking.run_tracked(self.docker_cmd, env=self._child_env())
        except _LaunchAborted as aborted:
            return aborted.exit_code
        finally:
            self._release()

    def workspace(self) -> None:
        if self.role in HOST_WORKTREE_ROLES:
            try:
                self.workspace_mounts = git_flow.sandbox_worktree_mounts(
                    self.target_worktree, SANDBOX_WORKSPACE_MOUNT, self.role
                )
            except RuntimeError as exc:
                print(f"Error: {messages.redact_cli_error(exc)}", file=sys.stderr)
                raise _LaunchAborted(1) from exc

    def credentials(self) -> None:
        tty_flag = ["-it"] if sys.stdin.isatty() else ["-i"]
        self.docker_cmd = ["docker", "run", "--rm", *tty_flag, *run_tracking.container_identity_args(self.role)]
        self.docker_cmd.extend(["-e", "DOCKER_CLI_HINTS=false"])
        self.forwarded_env = container_env.forwarded_env(
            self.role, self.agent_id, pin_workspace=bool(self.workspace_mounts)
        )
        self._stage_replan_feedback()
        for key, value in sorted(self.forwarded_env.items()):
            self._add_env(key, value)

    def _stage_replan_feedback(self) -> None:
        feedback_file = self.forwarded_env.pop(ENV_REPLAN_FEEDBACK_FILE, "").strip()
        inline_feedback = self.forwarded_env.pop(ENV_REPLAN_FEEDBACK, "")
        if feedback_file:
            abs_path = os.path.abspath(feedback_file)
            if not os.path.isfile(abs_path):
                print(f"Error: {ENV_REPLAN_FEEDBACK_FILE} '{feedback_file}' does not exist.", file=sys.stderr)
                raise _LaunchAborted(1)
            self.replan_feedback_host_path = abs_path
            self.forwarded_env[ENV_REPLAN_FEEDBACK_FILE] = _REPLAN_FEEDBACK_CONTAINER_PATH
        elif inline_feedback.strip():
            try:
                env_files.sweep_stale_env_files(env_files.env_file_dir())
                staged_path = env_files.write_replan_feedback_file(inline_feedback)
            except OSError as exc:
                print(f"Error: could not stage replan feedback file: {messages.redact_cli_error(exc)}.", file=sys.stderr)
                raise _LaunchAborted(1) from exc
            self.replan_feedback_host_path = staged_path
            self._owned_replan_feedback_file = staged_path
            self.forwarded_env[ENV_REPLAN_FEEDBACK_FILE] = _REPLAN_FEEDBACK_CONTAINER_PATH

    def _child_env(self) -> dict[str, str]:
        child_env = dict(os.environ)
        if self.replan_feedback_host_path is not None:
            child_env.pop(ENV_REPLAN_FEEDBACK, None)
        child_env.update(self.passthrough_env)
        child_env["DOCKER_CLI_HINTS"] = "false"
        return child_env

    def mounts(self, tr_mounts: list[str], tr_envs: dict[str, str]) -> None:
        self.docker_cmd.extend(tr_mounts)
        self._local_model(tr_envs)

        for k, v in tr_envs.items():
            self._add_env(k, v)

        self._payload_mounts()
        self.docker_cmd.extend(self.workspace_mounts)

        session_mounts = mount_args.get_agent_session_mounts(self.agent_id)
        self.docker_cmd.extend(session_mounts)

        network_isolation_args = mount_args.build_network_isolation_args(token_reduce=self.token_reduce)
        self.docker_cmd.extend(network_isolation_args)

    def argv(self) -> None:
        self.docker_cmd.append(self.image_name)
        self.docker_cmd.extend(self.container_args)

    def env_file(self) -> None:
        if not self.secret_env:
            return
        try:
            env_files.sweep_stale_env_files(env_files.env_file_dir())
            self.env_file_path = env_files.write_env_file(self.secret_env)
        except ValueError as exc:
            print(f"Error: {messages.redact_cli_error(exc)}.", file=sys.stderr)
            raise _LaunchAborted(1) from exc
        except OSError as exc:
            print(f"Error: could not write the credential env-file: {messages.redact_cli_error(exc)}.", file=sys.stderr)
            raise _LaunchAborted(1) from exc
        self.docker_cmd[_ENV_FILE_FLAG_INSERT_INDEX:_ENV_FILE_FLAG_INSERT_INDEX] = [
            "--env-file",
            self.env_file_path,
        ]

    def _add_env(self, key: str, value: str) -> None:
        try:
            env_files.validate_env_var_size(key, value)
        except ValueError as exc:
            print(f"Error: {messages.redact_cli_error(exc)}.", file=sys.stderr)
            raise _LaunchAborted(1) from exc
        if env_files.is_passthrough_env_item(key, value):
            self.passthrough_env[key] = value
            self.docker_cmd.extend(["-e", key])
        elif env_files.is_env_file_item(key, value):
            self.secret_env[key] = value
        else:
            self.docker_cmd.extend(["-e", f"{key}={value}"])

    def _is_proxy_owned(self) -> bool:
        return self.token_reduce or self.mitm_web or bool(sidecar._sidecar_state.container_name)

    def _local_model(self, tr_envs: dict[str, str]) -> None:
        norm_agent_id = local_llm.normalize_agent_id(self.agent_id)
        if local_llm.local_llm_requested() and norm_agent_id != "pi" and self.role in _LOCAL_LLM_WARNING_ROLES:
            print(
                f"Warning: host-local model mode applies to the pi runner only; ignoring it for '{self.agent_id}'.",
                file=sys.stderr,
            )
        if local_llm.local_llm_requested() and norm_agent_id == "pi":
            local_config = self._prepare_local_agent_dir()
            self.docker_cmd.extend(local_llm.container_mount_args(self.local_agent_dir))
            for key, value in local_llm.container_env(self.local_agent_dir).items():
                self._add_env(key, value)
            container_env.exempt_local_hosts(local_config, tr_envs, self._is_proxy_owned())

    def _prepare_local_agent_dir(self) -> dict:
        try:
            base_tmp = os.path.expanduser("~/.holon/tmp")
            os.makedirs(base_tmp, mode=0o755, exist_ok=True)
            self.local_agent_dir = tempfile.mkdtemp(prefix="holon-pi-agent-", dir=base_tmp)
            local_config = local_llm.prepare_agent_dir(
                self.local_agent_dir,
                host_config=local_llm.host_models_json(),
                allow_list=local_llm.host_local_allow_list(),
            )
            if "HOLON_AGENT_PROVIDER" not in self.forwarded_env:
                self._add_env("HOLON_AGENT_PROVIDER", container_env.local_provider(local_config))
        except local_llm.LocalLLMConfigError as exc:
            messages.print_encodable(f"Error: {messages.redact_cli_error(exc)}", file=sys.stderr)
            raise _LaunchAborted(1) from exc
        return local_config

    def _payload_mounts(self) -> None:
        if self.role == "intent-creator":
            if not self.intent_file:
                print("Error: Intent file is mandatory for intent creation.", file=sys.stderr)
                raise _LaunchAborted(1)
            abs_intent = os.path.abspath(self.intent_file)
            if not os.path.exists(abs_intent):
                print(f"Error: Intent file '{self.intent_file}' does not exist.", file=sys.stderr)
                raise _LaunchAborted(1)
            self.docker_cmd.extend(["-v", f"{abs_intent}:{_INTENT_CONTAINER_PATH}:ro"])

        if self.replan_feedback_host_path:
            self.docker_cmd.extend(["-v", f"{self.replan_feedback_host_path}:{_REPLAN_FEEDBACK_CONTAINER_PATH}:ro"])

        if self.result_file:
            abs_result = os.path.abspath(self.result_file)
            self.docker_cmd.extend(["-v", f"{abs_result}:{_RESULT_CONTAINER_PATH}"])

    def _release(self) -> None:
        if self.env_file_path:
            with contextlib.suppress(FileNotFoundError):
                os.remove(self.env_file_path)
        if self._owned_replan_feedback_file:
            with contextlib.suppress(FileNotFoundError):
                os.remove(self._owned_replan_feedback_file)
        sidecar.teardown_token_reduction_proxy()
        if self.local_agent_dir:
            shutil.rmtree(self.local_agent_dir, ignore_errors=True)
        try:
            runner = agent_runner.get_runner(self.agent_id)
            if hasattr(runner, "cleanup_ephemeral_credentials"):
                runner.cleanup_ephemeral_credentials()
        except Exception:
            pass


def run_docker_container(
    role: str,
    image_name: str,
    container_args: list[str],
    agent_id: str,
    **options: Unpack[ContainerLaunchOptions],
) -> int:
    return ContainerLaunch(role, image_name, container_args, agent_id, **options).run()
