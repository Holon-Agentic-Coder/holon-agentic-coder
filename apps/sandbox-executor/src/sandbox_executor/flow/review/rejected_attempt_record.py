"""Ledger row for a resolver change rejected because its pre-push tests failed."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.review.records import _bounded_error_context, record_review_action
from sandbox_executor.flow.review.rejected_patch_host import _persist_rejected_patch
from sandbox_executor.flow.review.resolver import ResolverOutcome
from sandbox_executor.ledger import generate_action_id


def _record_rejected_attempt(
    context: FlowContext, exec_file: str, review_target: str, iteration: int, outcome: ResolverOutcome
) -> None:
    """Append one failed action row pointing at the rejected patch and test summary of a failed pre-push run.

    Nothing is written when the pass kept no rejected attempt. The ledger writer redacts every string again.
    """
    if not (outcome.rejected_patch or outcome.rejected_patch_text or outcome.failed_tests or outcome.test_summary):
        return
    patch_path = outcome.rejected_patch
    if outcome.rejected_patch_text:
        patch_path = _persist_rejected_patch(context.repo_dir, iteration, patch_path, outcome.rejected_patch_text)
    row: dict[str, Any] = {
        "action_id": generate_action_id("ACT-RES"),
        "execution_id": f"REV-RES-{iteration}-{int(time.time())}",
        "action": f"Review Loop iteration {iteration} resolver change rejected: pre-push tests failed",
        "category": "code",
        "target": review_target,
        "outcome": "failure",
        "entry_type": "action",
        "action_origin": "pr_review",
        "review_iteration": iteration,
        "labels": ["review", "resolver", f"iteration-{iteration}", "rejected-attempt"],
        "error_context": _bounded_error_context(outcome.test_summary) if outcome.test_summary else None,
        "rejected_patch": patch_path,
        "failed_tests": list(outcome.failed_tests),
        "created_at": datetime.now(UTC).isoformat(),
    }
    record_review_action(context, exec_file, row)
