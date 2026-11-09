"""Target worktree resolution for ``holon flow``: provisioning, the default-branch redirect and checkpoint refusal.

The run's target worktree is resolved before preflight, before the flow context is built and before any stage runs:

- A working tree with the intent's base branch checked out (normally ``main``) selects the repository and the base
  branch only; the run happens in its ``flow-<slug>`` sibling, branched off ``origin/<base>`` (reused when present).
- A checkpoint whose recorded target is such a worktree is refused, naming the ``flow-<slug>`` worktree to use.
- A working tree on any other branch is used as it is; bare and absent targets are auto-provisioned.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from typing import Any

from sandbox_executor import cli
from sandbox_executor.git_flow import (
    default_branch_worktree_problem,
    derive_worktree_layout,
    ensure_flow_worktree_off_default,
    ensure_worktree,
    flow_worktree_layout,
    is_bare_repository,
    is_default_branch_worktree,
    is_working_tree,
    normalise_base_branch,
    target_worktree_problem,
)

logger = logging.getLogger(__name__)

_CHECKPOINT_TARGET_HINT = ", or pass --target-worktree to resume against a different attached worktree"

TARGET_WORKTREE_HELP: str = (
    "Repository and base branch to run against (default: current directory). If it has the intent's target branch "
    "checked out (normally main), the run happens in a dedicated flow-<slug> sibling worktree branched off "
    "origin/<target_branch>; the given worktree is never modified."
)


def _extract_flow_slug(
    args: argparse.Namespace,
    context: Any | None,
    intent_data: dict[str, Any],
) -> str:
    """Extract or derive a sanitised slug identifier for flow worktree naming."""
    slug: str | None = None
    if intent_data and isinstance(intent_data, dict):
        slug = intent_data.get("slug")
    if not slug and context is not None:
        slug = getattr(context, "slug", None)
        if not slug and hasattr(context, "intent_data") and isinstance(context.intent_data, dict):
            slug = context.intent_data.get("slug")
    if not slug and getattr(args, "intent_file", None):
        stem = os.path.splitext(os.path.basename(args.intent_file))[0]
        slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", stem).strip("-")
    if not slug:
        repo_base = os.path.basename(os.path.abspath(getattr(args, "target_worktree", ".") or "."))
        slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", repo_base).strip("-") or "task"
    return slug


def _flow_target_branch(intent_data: dict[str, Any], context: Any | None) -> str:
    """Return the base branch a newly provisioned flow worktree branches from (default: 'origin/main')."""
    if intent_data and isinstance(intent_data, dict) and intent_data.get("target_branch"):
        return str(intent_data["target_branch"])
    if (
        context is not None
        and getattr(context, "intent_data", None)
        and isinstance(context.intent_data, dict)
        and context.intent_data.get("target_branch")
    ):
        return str(context.intent_data["target_branch"])
    return "origin/main"


def _effective_flow_target(args: argparse.Namespace, context: Any | None) -> tuple[str, bool]:
    """Return the target worktree the run will use and whether it was restored from a checkpoint.

    Mirrors ``_update_flow_context``: an explicit --target-worktree other than '.' wins, otherwise a checkpoint
    resume keeps the checkpoint's recorded target_worktree.
    """
    explicit = getattr(args, "target_worktree", None)
    if explicit and explicit != ".":
        return explicit, False
    if context is not None:
        return getattr(context, "target_worktree", None) or ".", True
    return explicit or ".", False


def _nearest_existing_parent(path: str) -> str:
    """Return the closest existing ancestor directory of path."""
    current = os.path.dirname(os.path.abspath(path))
    while not os.path.exists(current) and os.path.dirname(current) != current:
        current = os.path.dirname(current)
    return current


def _should_provision(target_worktree: str, from_checkpoint: bool, slug: str) -> bool:
    """Return True for the auto-provisioned cases: a bare clone, or an absent CLI target that is safe to provision.

    A plain non-git directory is never provisioned, because the base repository lookup would otherwise fall back to
    the current working directory's repository. For the same reason an absent CLI target is provisioned only when it
    is the derived ``flow-<slug>`` path or its nearest existing parent lies inside a git repository, so a deleted or
    mistyped path fails fast instead. An absent checkpoint target is not re-provisioned either, so that a removed run
    worktree is reported rather than silently replaced.
    """
    if is_bare_repository(target_worktree):
        return True
    if from_checkpoint or os.path.exists(target_worktree):
        return False
    derived_path, _ = derive_worktree_layout(target_worktree, slug)
    if derived_path == os.path.normpath(os.path.realpath(target_worktree)):
        return True
    parent = _nearest_existing_parent(target_worktree)
    return is_working_tree(parent) or is_bare_repository(parent)


def _provision_flow_worktree(
    args: argparse.Namespace,
    context: Any | None,
    intent_data: dict[str, Any],
    target_worktree: str,
    from_checkpoint: bool,
) -> tuple[str, str]:
    """Auto-provision or attach a flow worktree.

    Returns:
        tuple[str, str]: The worktree path and an empty hint, or target_worktree and a hint naming the redacted
            provisioning error when provisioning fails.
    """
    slug = _extract_flow_slug(args, context, intent_data)
    try:
        worktree_path = ensure_worktree(
            target_worktree, slug=slug, target_branch=_flow_target_branch(intent_data, context)
        )
    except (RuntimeError, ValueError) as e:
        error = cli.redact_cli_error(e)
        logger.warning("Could not auto-provision a git worktree from '%s': %s", target_worktree, error)
        return target_worktree, f" (auto-provisioning from this target failed: {error})"
    logger.info("Provisioned or attached git worktree for flow '%s' at: %s", slug, worktree_path)
    if not from_checkpoint:
        args.target_worktree = worktree_path
    if context is not None:
        context.target_worktree = worktree_path
    return worktree_path, ""


def _ensure_attached_worktree(
    args: argparse.Namespace,
    context: Any | None,
    intent_data: dict[str, Any],
) -> bool:
    """Ensure the run's target worktree is an attached, non-bare git working tree before any stage runs.

    A bare or absent target is auto-provisioned as before (a checkpoint-restored target only when bare). Any other
    target that is not a working tree, or one provisioning could not fix, prints an actionable error and exits 1.

    Returns:
        bool: True when the target is usable, False after the failure exit (for a patched ``sys.exit``).
    """
    target_worktree, from_checkpoint = _effective_flow_target(args, context)
    if is_working_tree(target_worktree):
        return True

    resolved, provision_hint = target_worktree, ""
    if _should_provision(target_worktree, from_checkpoint, _extract_flow_slug(args, context, intent_data)):
        resolved, provision_hint = _provision_flow_worktree(
            args, context, intent_data, target_worktree, from_checkpoint
        )

    if from_checkpoint:
        problem = target_worktree_problem(
            resolved,
            subject=f"Checkpoint '{args.checkpoint}' target_worktree",
            hint=provision_hint + _CHECKPOINT_TARGET_HINT,
        )
    else:
        problem = target_worktree_problem(resolved, hint=provision_hint)
    if problem is not None:
        print(f"Error: {problem}", file=sys.stderr)
        sys.exit(1)
        return False
    return True


def _refuse_default_branch_checkpoint(
    args: argparse.Namespace,
    context: Any | None,
    intent_data: dict[str, Any],
    target_worktree: str,
) -> bool:
    """Refuse a checkpoint resume whose recorded target worktree has the base branch checked out.

    The flow-<slug> path is derived read-only; nothing is created. Always exits 1.

    Returns:
        bool: False after the failure exit (for a patched ``sys.exit``).
    """
    base = normalise_base_branch(_flow_target_branch(intent_data, context))
    flow_worktree, _ = flow_worktree_layout(target_worktree, _extract_flow_slug(args, context, intent_data))
    problem = default_branch_worktree_problem(
        target_worktree,
        base,
        flow_worktree=flow_worktree,
        subject=f"Checkpoint '{args.checkpoint}' target_worktree",
    )
    if not is_working_tree(flow_worktree):
        problem = (
            f"{problem} It does not exist yet: pass --target-worktree {os.path.abspath(target_worktree)} to "
            f"provision it from origin/{base} and resume there."
        )
    print(f"Error: {problem}", file=sys.stderr)
    sys.exit(1)
    return False


def ensure_flow_worktree(
    args: argparse.Namespace,
    context: Any | None,
    intent_data: dict[str, Any],
) -> bool:
    """Resolve the run's target worktree before preflight, context construction and any stage.

    A checkpoint target with the intent's base branch checked out is refused. A CLI target with it checked out is
    accepted here and redirected to its flow-<slug> sibling by :func:`redirect_default_branch_target` after the
    pre-flight probe. Any other target goes through the attached-worktree check and the existing auto-provisioning
    of bare and absent targets.

    Returns:
        bool: True when the target is usable, False after a failure exit (for a patched ``sys.exit``).
    """
    target_worktree, from_checkpoint = _effective_flow_target(args, context)
    if is_default_branch_worktree(target_worktree, _flow_target_branch(intent_data, context)):
        if from_checkpoint:
            return _refuse_default_branch_checkpoint(args, context, intent_data, target_worktree)
        return True
    return _ensure_attached_worktree(args, context, intent_data)


def redirect_default_branch_target(
    args: argparse.Namespace,
    context: Any | None,
    intent_data: dict[str, Any],
) -> bool:
    """Run the flow in the flow-<slug> sibling of a default-branch CLI target worktree, never in the target itself.

    Called after the pre-flight probe and before the flow context is built, so a refused probe creates no branch and
    every stage, the checkpoint and the run summary see the flow worktree. Any other target is left as it is.

    Returns:
        bool: True when the target needs no redirect or once ``args.target_worktree`` (and a resumed context's
            ``target_worktree``) name the flow worktree, False after the failure exit (for a patched ``sys.exit``).
    """
    target_worktree, from_checkpoint = _effective_flow_target(args, context)
    base = normalise_base_branch(_flow_target_branch(intent_data, context))
    if from_checkpoint or not is_default_branch_worktree(target_worktree, base):
        return True
    slug = _extract_flow_slug(args, context, intent_data)
    try:
        flow_worktree = ensure_flow_worktree_off_default(target_worktree, slug, base)
    except (RuntimeError, ValueError) as e:
        print(
            f"Error: Target worktree '{os.path.abspath(target_worktree)}' has the base branch '{base}' checked out, "
            f"so the flow must run in a dedicated flow worktree, which could not be provisioned: "
            f"{cli.redact_cli_error(e)}. The flow never runs in the default-branch worktree; fix the flow worktree "
            f"or pass --target-worktree <worktree on another branch>.",
            file=sys.stderr,
        )
        sys.exit(1)
        return False
    logger.info(
        "Target worktree '%s' has the base branch '%s' checked out; it selects the repository and base branch only. "
        "Running the flow in '%s' (branch %s; a new flow branch starts from origin/%s).",
        os.path.abspath(target_worktree),
        base,
        flow_worktree,
        os.path.basename(flow_worktree),
        base,
    )
    args.target_worktree = flow_worktree
    if context is not None:
        context.target_worktree = flow_worktree
    return True
