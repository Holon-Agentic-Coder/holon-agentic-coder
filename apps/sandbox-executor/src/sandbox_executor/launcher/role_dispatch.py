"""The single dispatch path for the authoring role containers ('holon intent|plan|execute' and 'holon flow')."""

from typing import Any, TypedDict, Unpack

from sandbox_executor.agent_defaults import FALLBACK_AGENT, image_for_agent, normalise_agent_id
from sandbox_executor.agent_runner import INTENT_CREATOR_ROLE
from sandbox_executor.launcher import containers

INTENT_CREATOR_IMAGE: str = "holon/orchestrator"
# The intent-creator entrypoint never invokes a runner, so its container keeps the historical antigravity
# mount profile whatever runner the caller resolved.
INTENT_CREATOR_AGENT_ID: str = "antigravity"


class RoleLaunchOptions(TypedDict, total=False):
    """The optional ``dispatch_role_agent`` keywords; an omitted key takes the default named in its docstring."""

    target_worktree: str | None
    intent_file: str | None
    result_file: str | None
    token_reduce: bool
    mitm_web: bool


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


def dispatch_role_agent(
    role: str,
    runner: str | None,
    model: str | None,
    positional_args: list[str],
    **options: Unpack[RoleLaunchOptions],
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
        **options: Keyword-only launch options (`RoleLaunchOptions`):
            target_worktree: Host git worktree the role container works in (required; default None).
            intent_file: Intent payload path, mandatory for the intent creator (default None).
            result_file: Structured outcome payload path for container-to-host result transport (default None).
            token_reduce: Whether to route container traffic through the token reduction proxy (default False).
            mitm_web: Whether to start the proxy with the mitmweb interface (default False).

    Returns:
        int: Container exit code (non-zero on failure).
    """
    del model  # Carried in positional_args for the roles that run a model.
    # `**options` accepts any keyword at runtime; keep the TypeError the named keyword-only parameters raised.
    unexpected = sorted(set(options) - set(RoleLaunchOptions.__annotations__))
    if unexpected:
        raise TypeError(f"dispatch_role_agent() got an unexpected keyword argument '{unexpected[0]}'")
    target_worktree = options.get("target_worktree")
    intent_file = options.get("intent_file")
    result_file = options.get("result_file")
    token_reduce = options.get("token_reduce", False)
    mitm_web = options.get("mitm_web", False)
    if role == INTENT_CREATOR_ROLE:
        kwargs: dict[str, Any] = {"agent_id": INTENT_CREATOR_AGENT_ID, "intent_file": intent_file}
        if token_reduce:
            kwargs["token_reduce"] = True
    else:
        kwargs = {"agent_id": normalise_agent_id(runner or FALLBACK_AGENT), "token_reduce": token_reduce}
    if mitm_web:
        kwargs["mitm_web"] = True
    if target_worktree:
        kwargs["target_worktree"] = target_worktree
    if result_file:
        kwargs["result_file"] = result_file
    image_name = resolve_role_image(role, kwargs["agent_id"])
    return containers.run_docker_container(role, image_name, list(positional_args), **kwargs)
