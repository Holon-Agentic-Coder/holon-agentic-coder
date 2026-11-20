"""Checkpoint discovery for ``holon flow <intent> --from-stage <stage>`` without ``--checkpoint``.

Such a resume loads the intent's default checkpoint (``.holon/flow/checkpoint-<slug>.json``) when one exists, so the
resumed stage gets the recorded branches and earlier stage results instead of an empty flow context. The target
worktree is probed first, then the dedicated ``flow-<slug>`` worktree a default-branch target is redirected to.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from sandbox_executor.cli.commands._flow_worktree import _extract_flow_slug
from sandbox_executor.flow.checkpoint_discovery import _find_intent_checkpoint
from sandbox_executor.git_flow import flow_worktree_layout


def _candidate_worktrees(args: argparse.Namespace, intent_data: dict[str, Any]) -> list[str]:
    """The target worktree, then its ``flow-<slug>`` sibling; only existing directories."""
    target = getattr(args, "target_worktree", None) or "."
    if not os.path.isdir(target):
        return []
    dedicated, _ = flow_worktree_layout(target, _extract_flow_slug(args, None, intent_data))
    return [path for path in (target, dedicated) if os.path.isdir(path)]


def discover_resume_checkpoint(args: argparse.Namespace) -> str | None:
    """The intent's existing checkpoint for a ``--from-stage`` run without ``--checkpoint``, or ``None``."""
    if not args.from_stage or args.checkpoint or not args.intent_file or not os.path.isfile(args.intent_file):
        return None
    with open(args.intent_file, encoding="utf-8") as f:
        intent_data = json.load(f)
    if not isinstance(intent_data, dict):
        return None
    return _find_intent_checkpoint(intent_data, _candidate_worktrees(args, intent_data))
