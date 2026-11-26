"""Tests of Stage 3 integration: the plan branch is merged into the parent execution branch for the combined PR.

The end-to-end integration runs share their pipeline scenario with other tests, which carry the integration assertions:
the default decomposed run is test_children.py's ``test_children_nest_under_the_plan_branch_...``, and the APPROVED
run is test_decomposition_approved.py's only test.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from sandbox_executor.flow import FlowContext, StageStatus
from sandbox_executor.flow.stages.integration import integrate_children


def test_a_failed_integration_merge_fails_stage_3_with_the_reason(tmp_path):
    ctx = FlowContext(repo_dir=str(tmp_path), token_reduce=False, intent_data={"slug": "parent"})
    payload = {
        "decomposed": True,
        "plan_branch": "I-1-p/P-2-a-m/_",
        "execution_branch": "I-1-p/P-2-a-m/E-3-a-m/_",
        "children_progress": [{"slug": "a"}],
    }
    with patch(
        "sandbox_executor.flow.stages.integration.merge_into_branch",
        return_value=(None, "merging conflicts outside holon-knowledge/ledger/*.jsonl"),
    ):
        res = integrate_children(ctx, "2026-11-09T00:00:00Z", payload)

    assert res.status == StageStatus.FAILED
    assert res.error == (
        "Execute stage failed: integrating 'I-1-p/P-2-a-m/_' into 'I-1-p/P-2-a-m/E-3-a-m/_' failed: merging conflicts "
        "outside holon-knowledge/ledger/*.jsonl"
    )
    assert json.loads(json.dumps(res.payload))["plan_branch"] == "I-1-p/P-2-a-m/_"


def test_integration_without_a_parent_execution_branch_fails(tmp_path):
    ctx = FlowContext(repo_dir=str(tmp_path), token_reduce=False, intent_data={"slug": "parent"})
    res = integrate_children(ctx, "2026-11-09T00:00:00Z", {"plan_branch": "P/_", "children_progress": []})
    assert res.status == StageStatus.FAILED
    assert res.error == "Execute stage failed: no parent execution branch to integrate."
    # The recorded decomposition survives, so a resume continues the children instead of re-dispatching the executor.
    assert res.payload == {"plan_branch": "P/_", "children_progress": []}
