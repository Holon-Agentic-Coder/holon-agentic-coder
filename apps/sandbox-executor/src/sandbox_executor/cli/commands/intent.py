from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

from sandbox_executor import cli
from sandbox_executor.cli.commands._host_publish import prepare_worktree, push_checked_out_branch
from sandbox_executor.cli.options import (
    add_target_worktree_option,
    add_token_reduction_options,
)

ORDER = 10
_DEFAULT_TARGET_BRANCH = "main"
_FALLBACK_BRANCH_NAME = "intent"
_SLUG_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")
_LEADING_INTENT_PREFIX_RE = re.compile(r"^I-\d+-")


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("intent", help="Run Intent Creator to initialise a new intent branch.")
    parser.add_argument("intent_file", help="Path to local intent JSON/YAML file (e.g. intents/my-task.json)")
    add_target_worktree_option(parser, default=None)
    add_token_reduction_options(parser)
    parser.set_defaults(_run=run)


def _payload(intent_file: str) -> dict[str, Any]:
    try:
        with open(intent_file, encoding="utf-8") as f:
            loaded = json.load(f)
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _flow_slug(payload: dict[str, Any]) -> str:
    slug = str(payload.get("slug") or "")
    sanitised_slug = _SLUG_NON_ALNUM_RE.sub("-", slug).strip("-").lower()
    if sanitised_slug:
        return sanitised_slug
    branch = str(payload.get("branch") or _FALLBACK_BRANCH_NAME).removesuffix("/_")
    return _LEADING_INTENT_PREFIX_RE.sub("", branch)


def run(args: argparse.Namespace) -> int | None:
    payload = _payload(args.intent_file)
    try:
        worktree = prepare_worktree(
            str(payload.get("target_branch") or _DEFAULT_TARGET_BRANCH), args.target_worktree, _flow_slug(payload)
        )
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {cli.redact_cli_error(exc)}", file=sys.stderr)
        sys.exit(1)
    exit_code = cli.dispatch_role_agent(
        cli.INTENT_CREATOR_ROLE,
        getattr(args, "agent", None),
        getattr(args, "model", None),
        [],
        intent_file=args.intent_file,
        target_worktree=worktree,
        token_reduce=bool(getattr(args, "token_reduce", False)),
        mitm_web=bool(getattr(args, "mitm_web", False)),
    )
    if exit_code == 0:
        try:
            push_checked_out_branch(worktree)
        except RuntimeError as exc:
            print(f"Error: {cli.redact_cli_error(exc)}", file=sys.stderr)
            exit_code = 1
    sys.exit(exit_code)
    return None
