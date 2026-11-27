from __future__ import annotations

import contextlib
import json
import os
import tempfile

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.review.delta_review import (
    current_decision,
    delta_state_of,
    record_addressed_findings,
    set_current,
    set_forwarded,
)
from sandbox_executor.flow.review.delta_review_mode import FULL, ReviewMode, decision_from_dict

FORWARDED_REL = os.path.join(".holon", "review", "delta_request.json")


def forward_scoped_decision(context: FlowContext) -> str | None:
    current = delta_state_of(context).get("current")
    if not current_decision(context).scoped:
        return None
    path = os.path.join(context.target_worktree, FORWARDED_REL)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # why: a fresh temp file renamed over the target never follows a link planted there in the shared worktree.
        fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".delta_request.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(current, handle)
            os.replace(tmp_path, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
            raise
    except OSError as exc:
        set_current(context, ReviewMode(FULL, "forwarding failed", current_decision(context).head))
        context.log(f"[Review Loop] Could not forward the scoped decision; reviewing in full: {exc}")
        return None
    return FORWARDED_REL


def load_forwarded_decision(context: FlowContext, repo_dir: str, rel_path: str) -> bool:
    parts = rel_path.replace("\\", "/").split("/")
    if os.path.isabs(rel_path) or ".." in parts:
        return False
    try:
        with open(os.path.join(repo_dir, rel_path), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return False
    decision = decision_from_dict(data)
    if not decision.scoped:
        return False
    addressed = data.get("addressed")
    # why: decide_review_mode honours the forwarded decision only for the head the container resolves itself.
    set_forwarded(context, decision)
    record_addressed_findings(context, addressed if isinstance(addressed, list) else [])
    return True


def discard_forwarded_decision(context: FlowContext) -> None:
    with contextlib.suppress(OSError):
        os.remove(os.path.join(context.target_worktree, FORWARDED_REL))
