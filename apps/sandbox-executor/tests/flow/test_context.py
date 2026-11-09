"""Tests for the flow data model: stages, statuses, stage results, the context and its serialisation."""

import json
import os

import pytest
from sandbox_executor.flow import (
    FlowCheckpoint,
    FlowContext,
    FlowStage,
    PipelineEngine,
    StageResult,
    StageStatus,
    load_checkpoint,
    migrate_checkpoint_data,
    save_checkpoint,
)

from tests.leak_assertions import make_gh_probe, make_probe


class TestFlowDataModels:
    """Test data structures, enums, serialisation, and atomic checkpoint persistence."""

    def test_flow_stage_enum(self):
        assert FlowStage.INTENT.value == "intent"
        assert FlowStage.PLAN.value == "plan"
        assert FlowStage.EXECUTE.value == "execute"
        assert FlowStage.REVIEW.value == "review"
        assert FlowStage.CALIBRATE.value == "calibrate"
        assert FlowStage.COMPLETED.value == "completed"

        assert FlowStage.from_str("intent") == FlowStage.INTENT
        assert FlowStage.from_str("PLAN") == FlowStage.PLAN
        assert FlowStage.from_str(FlowStage.EXECUTE) == FlowStage.EXECUTE

        with pytest.raises(ValueError, match="Unknown FlowStage"):
            FlowStage.from_str("invalid_stage")

    def test_stage_status_enum(self):
        assert StageStatus.PENDING.value == "pending"
        assert StageStatus.RUNNING.value == "running"
        assert StageStatus.SUCCESS.value == "success"
        assert StageStatus.FAILED.value == "failed"
        assert StageStatus.HALTED_FOR_HUMAN.value == "halted_for_human"
        assert StageStatus.SKIPPED.value == "skipped"

        assert StageStatus.from_str("pending") == StageStatus.PENDING
        assert StageStatus.from_str("HALTED_FOR_HUMAN") == StageStatus.HALTED_FOR_HUMAN
        with pytest.raises(ValueError, match="Unknown StageStatus"):
            StageStatus.from_str("unknown_status")

    def test_stage_result_serialization(self):
        res = StageResult(
            stage=FlowStage.INTENT,
            status=StageStatus.SUCCESS,
            start_time="2026-09-26T00:00:00Z",
            end_time="2026-09-26T00:00:05Z",
            payload={"branch": "I-123-test/_"},
            error=None,
        )
        d = res.to_dict()
        assert d["stage"] == "intent"
        assert d["status"] == "success"
        assert d["payload"]["branch"] == "I-123-test/_"

        reconstructed = StageResult.from_dict(d)
        assert reconstructed.stage == FlowStage.INTENT
        assert reconstructed.status == StageStatus.SUCCESS
        assert reconstructed.payload == {"branch": "I-123-test/_"}

    def test_flow_context_serialization_and_logging(self):
        ctx = FlowContext(
            repo_dir="/tmp/repo",
            intent_data={"slug": "test-slug", "goal": "Write tests"},
            agent="antigravity-agent",
            model="gemini-3.8-flash-medium",
            dry_run=True,
        )
        ctx.log("Sensitive message token=secret1234567890")
        assert len(ctx.logs) == 1
        assert "secret1234567890" not in ctx.logs[0]
        assert "*******" in ctx.logs[0]

        ctx.stage_results["intent"] = StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS)
        d = ctx.to_dict()
        assert d["agent"] == "antigravity-agent"
        assert d["stage_results"]["intent"]["status"] == "success"

        reconstructed = FlowContext.from_dict(d)
        assert reconstructed.agent == "antigravity-agent"
        assert reconstructed.dry_run is True
        assert reconstructed.stage_results["intent"].status == StageStatus.SUCCESS
        assert len(reconstructed.logs) == 1

    def test_flow_context_log_and_stage_failure_mask_provider_tokens_and_env_literals(self, monkeypatch):
        gho_probe = make_gh_probe("gh" + "o_")
        ghp_probe = make_gh_probe()
        env_probe = make_probe("ghtoken")
        monkeypatch.setenv("GITHUB_TOKEN", env_probe)
        ctx = FlowContext(repo_dir="/tmp/repo", dry_run=True, token_reduce=False)

        ctx.log(f"push failed for {gho_probe} and {ghp_probe} using {env_probe}")
        res = PipelineEngine(ctx).handle_stage_failure(
            FlowStage.EXECUTE, RuntimeError(f"auth {gho_probe} {ghp_probe} {env_probe} rejected")
        )

        for text in (*ctx.logs, res.error):
            for sample in (gho_probe, ghp_probe, env_probe):
                assert sample not in text
        assert "push failed" in ctx.logs[0]
        assert "rejected" in res.error

    def test_flow_context_new_flags_serialization(self):
        ctx = FlowContext(
            skip_review=True,
            skip_calibrate=True,
            max_review_iterations=5,
            mitm_web=True,
            token_reduce=True,
        )
        d = ctx.to_dict()
        assert d["skip_review"] is True
        assert d["skip_calibrate"] is True
        assert d["max_review_iterations"] == 5
        assert d["mitm_web"] is True
        assert d["token_reduce"] is True

        reconstructed = FlowContext.from_dict(d)
        assert reconstructed.skip_review is True
        assert reconstructed.skip_calibrate is True
        assert reconstructed.max_review_iterations == 5
        assert reconstructed.mitm_web is True
        assert reconstructed.token_reduce is True

    def test_legacy_checkpoint_loading_defaults(self):
        legacy_data = {
            "repo_dir": ".",
            "agent": "antigravity-agent",
            "model": "gemini-3.8-flash-medium",
        }
        ctx = FlowContext.from_dict(legacy_data)
        assert ctx.skip_review is False
        assert ctx.skip_calibrate is False
        assert ctx.max_review_iterations == 25
        assert ctx.mitm_web is False
        assert ctx.token_reduce is False

    def test_atomic_checkpoint_save_and_load(self, tmp_path):
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={"slug": "checkpoint-test", "goal": "Verify atomic saving"},
            agent="antigravity-agent",
        )
        ctx.stage_results["intent"] = StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS)

        checkpoint_file = str(tmp_path / ".holon" / "flow" / "checkpoint-checkpoint-test.json")
        saved_path = save_checkpoint(ctx, checkpoint_file)
        assert os.path.exists(saved_path)

        loaded = load_checkpoint(saved_path)
        assert loaded.intent_slug == "checkpoint-test"
        assert loaded.context.agent == "antigravity-agent"
        assert "intent" in loaded.completed_stages

    def test_default_checkpoint_of_a_sub_intent_is_keyed_on_its_branch(self, tmp_path):
        root = FlowContext(
            repo_dir=str(tmp_path), intent_data={"slug": "add-the-parser", "branch": "I-1-add-the-parser"}
        )
        child = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={
                "slug": "add-the-parser",
                "branch": "I-1-add-the-parser--1-add-the-parser",
                "parent_intent_id": "I-1-add-the-parser/_",
            },
        )
        grandchild = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={
                "slug": "add-the-parser",
                "branch": "I-1-add-the-parser--1-add-the-parser--1-add-the-parser",
                "parent_intent_id": "I-1-add-the-parser--1-add-the-parser/_",
            },
        )

        paths = [save_checkpoint(ctx) for ctx in (root, child, grandchild)]

        flow_dir = tmp_path / ".holon" / "flow"
        assert paths == [
            str(flow_dir / "checkpoint-add-the-parser.json"),
            str(flow_dir / "checkpoint-I-1-add-the-parser--1-add-the-parser.json"),
            str(flow_dir / "checkpoint-I-1-add-the-parser--1-add-the-parser--1-add-the-parser.json"),
        ]
        assert [load_checkpoint(p).context.intent_data["branch"] for p in paths] == [
            "I-1-add-the-parser",
            "I-1-add-the-parser--1-add-the-parser",
            "I-1-add-the-parser--1-add-the-parser--1-add-the-parser",
        ]

        # Test non-existent checkpoint
        with pytest.raises(FileNotFoundError):
            load_checkpoint(str(tmp_path / "does_not_exist.json"))

    def test_checkpoint_schema_version_serialized(self, tmp_path):
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={"slug": "version-test", "goal": "Check version"},
            agent="antigravity-agent",
        )
        checkpoint_file = str(tmp_path / "checkpoint-version.json")
        saved_path = save_checkpoint(ctx, checkpoint_file)
        with open(saved_path, encoding="utf-8") as f:
            data = json.load(f)
        assert data.get("schema_version") == 1
        assert data.get("context", {}).get("schema_version") == 1

    def test_load_legacy_v0_checkpoint_auto_migration(self, tmp_path):
        v0_data = {
            "intent_slug": "legacy-intent",
            "current_stage": "intent",
            "completed_stages": [],
            "context": {
                "repo_dir": str(tmp_path),
                "agent": "antigravity-agent",
                "model": "gemini-3.8-flash-medium",
            },
            "timestamp": "2026-01-01T00:00:00Z",
        }
        legacy_file = tmp_path / "checkpoint-legacy.json"
        legacy_file.write_text(json.dumps(v0_data), encoding="utf-8")

        loaded = load_checkpoint(str(legacy_file))
        assert loaded.schema_version == 1
        assert loaded.context.schema_version == 1
        assert loaded.context.max_review_iterations == 25
        assert loaded.context.token_reduce is False
        assert loaded.context.mitm_web is False

    def test_migration_dispatch_table_execution(self):
        v0_raw = {
            "intent_slug": "test-dispatch",
            "current_stage": "plan",
            "completed_stages": ["intent"],
            "context": {
                "agent": "codex",
            },
        }
        migrated = migrate_checkpoint_data(v0_raw, target_version=1)
        assert migrated["schema_version"] == 1
        assert migrated["context"]["schema_version"] == 1
        assert migrated["context"]["agent"] == "codex"
        assert migrated["context"]["max_review_iterations"] == 25
        assert migrated["context"]["token_reduce"] is False

    def test_future_schema_version_rejection(self, tmp_path):
        future_data = {
            "schema_version": 999,
            "intent_slug": "future-intent",
            "current_stage": "intent",
            "completed_stages": [],
            "context": {},
            "timestamp": "2028-01-01T00:00:00Z",
        }
        future_file = tmp_path / "checkpoint-future.json"
        future_file.write_text(json.dumps(future_data), encoding="utf-8")

        with pytest.raises(ValueError, match="Unsupported checkpoint schema_version 999"):
            load_checkpoint(str(future_file))

    def test_none_schema_version_handling(self):
        v0_null = {
            "schema_version": None,
            "intent_slug": "legacy-slug",
            "current_stage": "intent",
            "context": {"schema_version": None},
        }
        cp = FlowCheckpoint.from_dict(v0_null)
        assert cp.schema_version == 1
        assert cp.context.schema_version == 1

        ctx = FlowContext.from_dict({"schema_version": None, "agent": "claude"})
        assert ctx.schema_version == 1
        assert ctx.agent == "claude"

    def test_future_context_schema_version_rejection(self):
        with pytest.raises(ValueError, match="Unsupported context schema_version 999"):
            FlowContext.from_dict({"schema_version": 999})
