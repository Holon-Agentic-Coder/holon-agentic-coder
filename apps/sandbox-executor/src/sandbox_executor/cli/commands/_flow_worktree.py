from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from typing import Any

from sandbox_executor import cli
from sandbox_executor.flow.run_lock import hold_flow_run_lock
from sandbox_executor.git_flow import (
    checked_out_branch,
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

_DEFAULT_BASE_BRANCH = "origin/main"
_FALLBACK_SLUG = "task"
_SLUG_SANITISE_PATTERN = re.compile(r"[^a-zA-Z0-9_\-]+")
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
    slug: str | None = None
    if intent_data and isinstance(intent_data, dict):
        slug = intent_data.get("slug")
    if not slug and context is not None:
        slug = getattr(context, "slug", None)
        if not slug and hasattr(context, "intent_data") and isinstance(context.intent_data, dict):
            slug = context.intent_data.get("slug")
    if not slug and getattr(args, "intent_file", None):
        stem = os.path.splitext(os.path.basename(args.intent_file))[0]
        slug = _SLUG_SANITISE_PATTERN.sub("-", stem).strip("-")
    if not slug:
        repo_base = os.path.basename(os.path.abspath(getattr(args, "target_worktree", ".") or "."))
        slug = _SLUG_SANITISE_PATTERN.sub("-", repo_base).strip("-") or _FALLBACK_SLUG
    return slug


def _flow_target_branch(intent_data: dict[str, Any], context: Any | None) -> str:
    if intent_data and isinstance(intent_data, dict) and intent_data.get("target_branch"):
        return str(intent_data["target_branch"])
    context_data = getattr(context, "intent_data", None)
    if isinstance(context_data, dict) and context_data.get("target_branch"):
        return str(context_data["target_branch"])
    return _DEFAULT_BASE_BRANCH


def _effective_flow_target(args: argparse.Namespace, context: Any | None) -> tuple[str, bool]:
    explicit = getattr(args, "target_worktree", None)
    if explicit and explicit != ".":
        return explicit, False
    if context is not None:
        return getattr(context, "target_worktree", None) or ".", True
    return explicit or ".", False


def _nearest_existing_parent(path: str) -> str:
    current = os.path.dirname(os.path.abspath(path))
    while not os.path.exists(current) and os.path.dirname(current) != current:
        current = os.path.dirname(current)
    return current


def _should_provision(target_worktree: str, from_checkpoint: bool, slug: str) -> bool:
    if is_bare_repository(target_worktree):
        return True
    if from_checkpoint or os.path.exists(target_worktree):
        return False
    derived_path, _ = derive_worktree_layout(target_worktree, slug)
    is_derived_flow_worktree = derived_path == os.path.normpath(os.path.realpath(target_worktree))
    if is_derived_flow_worktree:
        return True
    parent = _nearest_existing_parent(target_worktree)
    parent_is_git_repo = is_working_tree(parent) or is_bare_repository(parent)
    return parent_is_git_repo


def _provision_flow_worktree(
    args: argparse.Namespace,
    context: Any | None,
    intent_data: dict[str, Any],
    target_worktree: str,
    from_checkpoint: bool,
) -> tuple[str, str]:
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
    target_worktree, from_checkpoint = _effective_flow_target(args, context)
    hold_flow_run_lock(target_worktree, _extract_flow_slug(args, context, intent_data))
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
        checked_out_branch(flow_worktree) or "detached HEAD",
        base,
    )
    args.target_worktree = flow_worktree
    if context is not None:
        context.target_worktree = flow_worktree
    return True
