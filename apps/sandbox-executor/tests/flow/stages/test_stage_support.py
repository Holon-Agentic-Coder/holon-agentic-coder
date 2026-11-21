"""The steps Stage 1 and Stage 2 share: knowledge consult, success result and the intent payload file."""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.flow.context import FlowContext, FlowStage, StageStatus
from sandbox_executor.flow.stages.stage_support import consult_knowledge, intent_payload_file, stage_success

ENGINE = "sandbox_executor.flow.stages.stage_support.ConsultEngine"
STAGE_LOG = logging.getLogger("sandbox_executor.flow.stages.intent")


@pytest.mark.parametrize(
    ("intent", "match", "expected"),
    [
        ({"labels": "db, backend,, "}, "all", {"all_labels": ["db", "backend"]}),
        ({"labels": ["db", "ui"]}, "any", {"any_labels": ["db", "ui"]}),
        ({}, "all", {"all_labels": []}),
    ],
)
def test_consult_matches_the_intent_labels_by_the_requested_keyword(tmp_path: Path, intent, match, expected):
    context = FlowContext(target_worktree=str(tmp_path))
    with patch(ENGINE) as engine:
        engine.return_value.consult_records.return_value = MagicMock(consulted_ids=["K-1"])
        assert consult_knowledge(context, intent, match=match, purpose="plan", log=STAGE_LOG) == ["K-1"]
    engine.assert_called_once_with(
        project_db_path=os.path.join(str(tmp_path), "holon-knowledge/.index/action_index.db")
    )
    engine.return_value.consult_records.assert_called_once_with(**expected, top_k=6, max_tokens=1200)
    assert context.logs[-1].endswith("Consulted 1 knowledge records for plan: ['K-1']")


def test_a_consult_failure_is_swallowed_and_logged_on_the_stage_logger(tmp_path: Path, caplog):
    context = FlowContext(target_worktree=str(tmp_path))
    with patch(ENGINE, side_effect=RuntimeError("no index")), caplog.at_level(logging.DEBUG, logger=STAGE_LOG.name):
        assert consult_knowledge(context, {"labels": "db"}, match="all", purpose="intent", log=STAGE_LOG) == []
    assert [(r.name, r.getMessage()) for r in caplog.records] == [
        (STAGE_LOG.name, "Non-fatal consult failure during intent stage: no index")
    ]
    assert context.logs == []


def test_stage_success_records_the_result_under_its_key():
    context = FlowContext()
    result = stage_success(context, FlowStage.PLAN, "plan", "2026-11-21T00:00:00Z", {"plan_id": "P-1"})
    assert result.status == StageStatus.SUCCESS
    assert result.stage == FlowStage.PLAN
    assert result.start_time == "2026-11-21T00:00:00Z"
    assert result.end_time.endswith("Z")
    assert result.payload == {"plan_id": "P-1"}
    assert context.stage_results["plan"] is result


def test_the_intent_payload_file_is_world_readable_json_and_removed_afterwards(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with intent_payload_file({"goal": "g"}) as path:
        assert Path(path).parent == tmp_path / ".holon" / "tmp"
        assert Path(path).name.startswith("holon-flow-intent-") and path.endswith(".json")
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o644
        assert json.loads(Path(path).read_text(encoding="utf-8")) == {"goal": "g"}
    assert not Path(path).exists()


def test_the_intent_payload_file_is_removed_when_the_dispatch_raises(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(RuntimeError), intent_payload_file({"goal": "g"}) as path:
        raise RuntimeError("dispatch failed")
    assert not Path(path).exists()
