from typing import Any, TypedDict, Unpack

from sandbox_executor.agent_defaults import FALLBACK_AGENT, image_for_agent, normalise_agent_id
from sandbox_executor.agent_runner import INTENT_CREATOR_ROLE
from sandbox_executor.launcher import containers

INTENT_CREATOR_IMAGE: str = "holon/orchestrator"
# why: intent-creator never invokes a runner, so it keeps the antigravity mount profile regardless of caller runner.
INTENT_CREATOR_AGENT_ID: str = "antigravity"


class RoleLaunchOptions(TypedDict, total=False):
    target_worktree: str | None
    intent_file: str | None
    result_file: str | None
    token_reduce: bool
    mitm_web: bool
    proxy_attach: bool


def resolve_role_image(role: str, agent_id: str) -> str:
    if role == INTENT_CREATOR_ROLE:
        return INTENT_CREATOR_IMAGE
    return image_for_agent(agent_id)


def _validate_launch_options(options: RoleLaunchOptions) -> None:
    unexpected = sorted(set(options) - set(RoleLaunchOptions.__annotations__))
    if unexpected:
        raise TypeError(f"dispatch_role_agent() got an unexpected keyword argument '{unexpected[0]}'")


def _build_container_kwargs(role: str, runner: str | None, options: RoleLaunchOptions) -> dict[str, Any]:
    token_reduce = options.get("token_reduce", False)
    if role == INTENT_CREATOR_ROLE:
        kwargs: dict[str, Any] = {"agent_id": INTENT_CREATOR_AGENT_ID, "intent_file": options.get("intent_file")}
        if token_reduce:
            kwargs["token_reduce"] = True
    else:
        kwargs = {"agent_id": normalise_agent_id(runner or FALLBACK_AGENT), "token_reduce": token_reduce}
    if options.get("mitm_web", False):
        kwargs["mitm_web"] = True
    if options.get("proxy_attach", False):
        kwargs["proxy_attach"] = True
    if target_worktree := options.get("target_worktree"):
        kwargs["target_worktree"] = target_worktree
    if result_file := options.get("result_file"):
        kwargs["result_file"] = result_file
    return kwargs


def dispatch_role_agent(
    role: str,
    runner: str | None,
    model: str | None,
    positional_args: list[str],
    **options: Unpack[RoleLaunchOptions],
) -> int:
    del model
    _validate_launch_options(options)
    kwargs = _build_container_kwargs(role, runner, options)
    image_name = resolve_role_image(role, kwargs["agent_id"])
    return containers.run_docker_container(role, image_name, list(positional_args), **kwargs)
