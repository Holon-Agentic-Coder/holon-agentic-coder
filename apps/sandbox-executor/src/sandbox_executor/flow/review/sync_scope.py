"""The re-review scope a target merge leaves behind, and the rule the Stage 4 sync records it with.

An empty ``context.review_scope_paths`` means "review everything", so a resolved code conflict only narrows the next
pass when an approval stands; otherwise the pending full review already covers the resolved paths.

Two post-merge writers set the scope themselves: ``end_of_flow_sync`` unions it without an approval check, and
``approval_carry_over.record_stage5_carry_over`` replaces it with the Stage 5 conflict gate's scope.
"""

from __future__ import annotations

from sandbox_executor.calibration import is_metadata_path
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.ci_reverify import approved_head_of
from sandbox_executor.flow.review.approval_coverage import approved_fingerprint_of
from sandbox_executor.flow.target_sync_result import SyncResult

SCOPE_UNCHANGED = "unchanged"
SCOPE_UNIONED = "unioned"
SCOPE_RECORDED = "recorded"
SCOPE_FULL_REVIEW = "full_review"


def is_mechanical_only(result: SyncResult) -> bool:
    """True only when ``result`` merged cleanly or mechanically without invoking a code resolver."""
    return result.status == "merged" and not result.code_paths and not result.resolver_tier


def compute_rereview_scope(result: SyncResult) -> tuple[str, ...]:
    """Return sorted deduplicated code and test paths touched by an agent-resolved merge commit."""
    if result.status != "merged" or is_mechanical_only(result):
        return ()
    scoped = {
        p
        for p in (*result.code_paths, *result.resolver_test_edits)
        if p and not is_metadata_path(p) and not p.startswith("holon-knowledge/ledger/")
    }
    return tuple(sorted(scoped))


def format_conflict_block_reason(result: SyncResult, target_branch: str) -> str:
    """Format a class-accurate block reason; ``code conflicts`` appears only for unresolved ``CODE`` paths."""
    resolved = {path for path, _, _ in result.resolutions}
    unresolved_code = [path for path in result.code_paths if path not in resolved]
    if unresolved_code:
        suffix = f" ({result.test_result})" if result.test_result else ""
        return f"code conflicts with {target_branch} in: {', '.join(unresolved_code)}{suffix}"
    unresolved_other = [path for path in result.conflicted_paths if path not in resolved]
    if unresolved_other:
        detail = f": {result.error}" if result.error else ""
        return f"merge conflicts with {target_branch} in: {', '.join(unresolved_other)}{detail}"
    if result.resolutions and result.error:
        classes = ", ".join(sorted({cls for _, cls, _ in result.resolutions}))
        return f"merging {target_branch} ({classes}) failed: {result.error}"
    return f"merging {target_branch} failed: {result.error or 'unknown error'}"


def _approval_recorded(context: FlowContext) -> bool:
    """True when the recorded Stage 4 consensus is approved (a withdrawal clears ``approved``)."""
    return bool(approved_fingerprint_of(context) or approved_head_of(context))


def record_rereview_scope(context: FlowContext, scope: tuple[str, ...], *, approved: bool | None = None) -> str:
    """Record ``scope`` for the next pass and return what was done (``SCOPE_*``).

    An existing scope is unioned; an empty one (a full review) is set only when an approval stands (``approved``, or
    read from the context when None). Otherwise it stays empty, so the full review also covers the resolved paths.
    """
    if not scope:
        return SCOPE_UNCHANGED
    current = list(getattr(context, "review_scope_paths", None) or [])
    if current:
        context.review_scope_paths = sorted(set(current) | set(scope))
        return SCOPE_UNIONED
    if _approval_recorded(context) if approved is None else approved:
        context.review_scope_paths = sorted(set(scope))
        return SCOPE_RECORDED
    context.log(f"[Review Loop] Resolved paths {', '.join(scope)} are covered by the pending full review.")
    return SCOPE_FULL_REVIEW
