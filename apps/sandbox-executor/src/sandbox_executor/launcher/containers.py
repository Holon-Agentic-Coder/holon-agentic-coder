"""The role-container launch: ``ContainerLaunch`` assembles one ``docker run`` in named steps and runs it."""

import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import TypedDict, Unpack

from sandbox_executor import agent_runner, git_flow, local_llm
from sandbox_executor.agent_runner import HOST_WORKTREE_ROLES, SANDBOX_WORKSPACE_MOUNT
from sandbox_executor.launcher import container_env, env_files, messages, proxy_paths, sidecar
from sandbox_executor.launcher import mounts as mount_args
from sandbox_executor.redaction import redact_command


class ContainerLaunchOptions(TypedDict, total=False):
    """The optional ``run_docker_container`` keywords; an omitted key keeps the ``ContainerLaunch`` default."""

    intent_file: str | None
    token_reduce: bool
    mitm_web: bool
    proxy_attach: bool
    target_worktree: str | None
    result_file: str | None


class _LaunchAborted(Exception):
    """A launch step printed its error and stopped the launch; ``run`` returns the carried exit code."""

    def __init__(self, exit_code: int) -> None:
        super().__init__(exit_code)
        self.exit_code = exit_code


@dataclass
class ContainerLaunch:
    """One role-container ``docker run``, assembled by the named steps ``credentials``, ``mounts``, ``argv`` and
    ``env_file`` and executed by ``run``.

    Credentials never go on the docker command line, where ``ps`` and /proc/<pid>/cmdline can read them: they are
    collected in ``secret_env`` and handed over in a 0600 --env-file just before the run. That includes any value with
    a URL password (a proxy URL in any scheme, or none), whatever its name, and every forwarded HOLON_* value,
    whatever its name or shape (see `env_files.is_env_file_item`). A HOLON_* value no env-file line can carry goes as a
    bare `-e NAME`, its value in the docker client env (`passthrough_env`).
    """

    role: str
    image_name: str
    container_args: list[str]
    agent_id: str
    intent_file: str | None = None
    token_reduce: bool = False
    mitm_web: bool = False
    # Attach to a proxy the user already runs (the flow's port-probe hit); never spawns a sidecar.
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

    def run(self) -> int:
        """Validate the worktree, assemble the command and run the container.

        Returns:
            The container's exit code, or 1 when docker is missing or a step fails before the run.
        """
        if not shutil.which("docker"):
            print("Error: 'docker' CLI command not found. Please install Docker.", file=sys.stderr)
            return 1
        try:
            self.workspace()
        except _LaunchAborted as aborted:
            return aborted.exit_code
        self.credentials()

        # Containers reach the host only through the gateway name; make it resolvable on Linux as it already is on
        # Docker Desktop, so agent base URLs rewritten to host.docker.internal work on every platform.
        self.docker_cmd.extend(proxy_paths._gateway_host_args())

        # Token Reduction Proxy & CA Mounts. From this point on the sidecar (and its network) may exist,
        # so every remaining exit path — early returns included — must run teardown, not just the final
        # subprocess.run.
        tr_mounts, tr_envs = sidecar.get_token_reduction_mounts_and_envs(
            token_reduce=self.token_reduce, mitm_web=self.mitm_web, proxy_attach=self.proxy_attach
        )
        try:
            self.mounts(tr_mounts, tr_envs)
            self.argv()
            self.env_file()
            # A forwarded HOLON_* value may hold a non-UTF-8 byte (surrogateescape); it must not crash the run here.
            messages.print_encodable(f"Executing: {redact_command(self.docker_cmd)}")
            result = subprocess.run(
                self.docker_cmd, env={**os.environ, **self.passthrough_env, "DOCKER_CLI_HINTS": "false"}
            )
            return result.returncode
        except _LaunchAborted as aborted:
            return aborted.exit_code
        finally:
            self._release()

    def workspace(self) -> None:
        """Validate a host-worktree role's worktree and keep its bind mounts for the ``mounts`` step."""
        if self.role in HOST_WORKTREE_ROLES:
            try:
                self.workspace_mounts = git_flow.sandbox_worktree_mounts(
                    self.target_worktree, SANDBOX_WORKSPACE_MOUNT, self.role
                )
            except RuntimeError as exc:
                print(f"Error: {messages.redact_cli_error(exc)}", file=sys.stderr)
                raise _LaunchAborted(1) from exc

    def credentials(self) -> None:
        """Start the ``docker run`` command and route each forwarded variable to ``-e`` or to the secret env-file."""
        tty_flag = ["-it"] if sys.stdin.isatty() else ["-i"]
        self.docker_cmd = ["docker", "run", "--rm", *tty_flag]
        self.docker_cmd.extend(["-e", "DOCKER_CLI_HINTS=false"])
        self.forwarded_env = container_env.forwarded_env(
            self.role, self.agent_id, pin_workspace=bool(self.workspace_mounts)
        )
        for key, value in sorted(self.forwarded_env.items()):
            self._add_env(key, value)

    def mounts(self, tr_mounts: list[str], tr_envs: dict[str, str]) -> None:
        """Add the proxy, host-local model, payload, worktree and session mounts, then the isolation arguments."""
        self.docker_cmd.extend(tr_mounts)
        self._local_model(tr_envs)

        for k, v in tr_envs.items():
            self._add_env(k, v)

        self._payload_mounts()

        # The validated host worktree, its git directories and the read-only git-config overlays.
        self.docker_cmd.extend(self.workspace_mounts)

        # Auto-detect Session Mounts
        session_mounts = mount_args.get_agent_session_mounts(self.agent_id)
        self.docker_cmd.extend(session_mounts)

        # Network isolation and egress controls
        network_isolation_args = mount_args.build_network_isolation_args(token_reduce=self.token_reduce)
        self.docker_cmd.extend(network_isolation_args)

    def argv(self) -> None:
        """Append the image and its arguments, completing the command line."""
        self.docker_cmd.append(self.image_name)
        self.docker_cmd.extend(self.container_args)

    def env_file(self) -> None:
        """Write the secret variables to a 0600 env-file and pass it right after ``docker run``; fail closed."""
        if not self.secret_env:
            return
        try:
            env_files.sweep_stale_env_files(env_files.env_file_dir())
            self.env_file_path = env_files.write_env_file(self.secret_env)
        except ValueError as exc:
            print(f"Error: {messages.redact_cli_error(exc)}.", file=sys.stderr)
            raise _LaunchAborted(1) from exc
        except OSError as exc:
            # An unwritable or unreadable ~/.holon/tmp/env-files: fail closed with a clear message
            # rather than a traceback, and never fall back to passing the credentials on the argv.
            print(f"Error: could not write the credential env-file: {messages.redact_cli_error(exc)}.", file=sys.stderr)
            raise _LaunchAborted(1) from exc
        # Right after `docker run`. Docker reads --env-file on the client side, so the host path
        # needs no bind mount.
        self.docker_cmd[2:2] = ["--env-file", self.env_file_path]

    def _add_env(self, key: str, value: str) -> None:
        """Route a variable: every ``HOLON_*`` item to the env-file, or a bare ``-e NAME`` when no env-file line
        can carry it; any other item to the env-file if secret, else ``-e KEY=VALUE``."""
        if env_files.is_passthrough_env_item(key, value):
            self.passthrough_env[key] = value
            self.docker_cmd.extend(["-e", key])
        elif env_files.is_env_file_item(key, value):
            self.secret_env[key] = value
        else:
            self.docker_cmd.extend(["-e", f"{key}={value}"])

    def _local_model(self, tr_envs: dict[str, str]) -> None:
        """Hand a pi agent a generated host-local model config reachable from the container."""
        # Host-local inference servers: loopback and the host's own LAN address are unreachable from the
        # container namespace, so hand the agent a generated config whose authorities resolve via the gateway. The
        # artifact is a pi agent directory, so it is produced for pi only; other runners still need real credentials.
        norm_agent_id = local_llm.normalize_agent_id(self.agent_id)
        if local_llm.local_llm_requested() and norm_agent_id != "pi" and self.role in ("planner", "executor"):
            print(
                f"Warning: host-local model mode applies to the pi runner only; ignoring it for '{self.agent_id}'.",
                file=sys.stderr,
            )
        if local_llm.local_llm_requested() and norm_agent_id == "pi":
            local_config = self._prepare_local_agent_dir()
            self.docker_cmd.extend(local_llm.container_mount_args(self.local_agent_dir))
            for key, value in local_llm.container_env(self.local_agent_dir).items():
                self._add_env(key, value)
            proxy_owned = self.token_reduce or self.mitm_web or bool(sidecar._sidecar_state.container_name)
            container_env.exempt_local_hosts(local_config, tr_envs, proxy_owned)

    def _prepare_local_agent_dir(self) -> dict:
        """Generate the pi agent directory under ``~/.holon/tmp`` and return its configuration."""
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
        """Mount the intent file (mandatory for the intent creator) and the result file, when given."""
        # Intent file mount for intent-creator role
        if self.role == "intent-creator":
            if not self.intent_file:
                print("Error: Intent file is mandatory for intent creation.", file=sys.stderr)
                raise _LaunchAborted(1)
            abs_intent = os.path.abspath(self.intent_file)
            if not os.path.exists(abs_intent):
                print(f"Error: Intent file '{self.intent_file}' does not exist.", file=sys.stderr)
                raise _LaunchAborted(1)
            self.docker_cmd.extend(["-v", f"{abs_intent}:/tmp/intent.json"])

        # Result file mount for role containers (e.g. review-loop result transport)
        if self.result_file:
            abs_result = os.path.abspath(self.result_file)
            self.docker_cmd.extend(["-v", f"{abs_result}:/tmp/review_result.json"])

    def _release(self) -> None:
        """Remove the env-file, the sidecar, the generated agent directory and the runner's ephemeral credentials."""
        if self.env_file_path:
            with contextlib.suppress(FileNotFoundError):
                os.remove(self.env_file_path)
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
    """Constructs docker run command with auto-discovered credentials and executes it.

    Every role (intent creator, planner, executor and review loop) works in place on the host worktree, so its
    ``target_worktree`` is required, validated and bind-mounted at the sandbox workspace path (see
    ``git_flow.sandbox_worktree_mounts``) with ``HOLON_TARGET_WORKTREE`` pinned to the mountpoint. No container
    reaches the remote: the host fetches the base before the container starts and pushes after it returns.
    """
    return ContainerLaunch(role, image_name, container_args, agent_id, **options).run()
