"""Tests for stage ID generation, disambiguation, and ledger uniqueness validation."""

import inspect
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox_executor.entrypoint import planner
from sandbox_executor.entrypoint.planner import PLAN_FILE_PATTERN, plan_file_rel
from sandbox_executor.flow.review import loop as review_loop
from sandbox_executor.flow.review.ids import build_review_id
from sandbox_executor.ledger import (
    _describe_id_owner,
    _extract_parent_ref,
    build_execution_id,
    build_plan_id,
    build_stage_disambiguator,
    validate_ledger_file_unique_ids,
    validate_ledger_unique_ids,
    validate_stage_ledger,
)


class TestSameSecondIds(unittest.TestCase):
    """Ids and file names created in one second stay distinct; old formats stay valid."""

    def test_plan_ids_and_file_names_differ_within_one_second(self):
        first = build_plan_id(1794446836, "I-1/ids", "claude-agent", "sonnet", disambiguator="aaaaaaaa")
        second = build_plan_id(1794446836, "I-1/ids", "claude-agent", "sonnet", disambiguator="bbbbbbbb")
        self.assertNotEqual(plan_file_rel(first), plan_file_rel(second))
        self.assertEqual(plan_file_rel(first), f"plans/{first}.md")
        self.assertIn("aaaaaaaa", plan_file_rel(first))
        self.assertRegex(plan_file_rel(first), PLAN_FILE_PATTERN)

    def test_hexless_plan_file_name_still_matches_plan_pattern(self):
        self.assertRegex("plans/P-1794446836-claude-agent-sonnet.md", PLAN_FILE_PATTERN)

    def test_call_sites_use_the_hex_qualified_id_helpers(self):
        self.assertIn("build_review_id(context.agent)", inspect.getsource(review_loop.run_review_loop))
        self.assertIn("plan_file_rel(plan_id)", inspect.getsource(planner))

    def test_describe_id_owner_fallbacks(self):
        self.assertEqual(
            _describe_id_owner("I-1", {"agent_name": "gamma", "timestamp": "t9"}),
            "'I-1' (agent gamma, created_at t9)",
        )
        self.assertEqual(
            _describe_id_owner("I-2", {}),
            "'I-2' (agent unknown-agent, created_at unknown-time)",
        )

    def test_review_ids_differ_within_one_second(self):
        with patch("sandbox_executor.flow.review.ids.time.time", return_value=1794446836.0):
            first, second = build_review_id("claude-agent"), build_review_id("claude-agent")
        self.assertNotEqual(first, second)
        self.assertRegex(first, r"REV-1794446836-[0-9a-f]{8}-claude-agent")

    def test_old_and_new_review_ids_validate_together(self):
        records = [
            {"execution_id": "REV-1794446836-claude-agent", "plan_branch": "I-1/P-1/_"},
            {"execution_id": build_review_id("claude-agent"), "plan_branch": "I-2/P-2/_"},
        ]
        validate_ledger_unique_ids(records, id_key="execution_id", parent_key="plan_branch")

    def test_duplicate_error_names_both_owners(self):
        records = [
            {"plan_id": "P-x", "intent_branch": "I-100/_", "agent": "alpha", "created_at": "t1"},
            {"plan_id": "P-x", "intent_branch": "I-200/_", "agent": "beta", "created_at": "t2"},
        ]
        with self.assertRaises(ValueError) as ctx:
            validate_ledger_unique_ids(records)
        message = str(ctx.exception)
        for expected in ("I-100", "alpha", "t1", "I-200", "beta", "t2"):
            self.assertIn(expected, message)


class TestUniqueStageIDs(unittest.TestCase):
    def test_disambiguator_uniqueness_across_rapid_invocations(self):
        """Disambiguator values generated in a tight loop for the same branch must be unique and 8-hex."""
        parent_branch = "I-1794182541-unique-plan-and-execution-ids/_"
        count = 100
        disambiguators = [build_stage_disambiguator(parent_branch) for _ in range(count)]

        self.assertEqual(len(set(disambiguators)), count)
        for dis in disambiguators:
            self.assertTrue(re.fullmatch(r"^[0-9a-f]{8}$", dis), f"Invalid disambiguator format: {dis}")

    def test_disambiguator_uniqueness_across_different_branches(self):
        """Disambiguators generated for differing parent branches must differ."""
        dis1 = build_stage_disambiguator("I-100-alpha/_")
        dis2 = build_stage_disambiguator("I-200-beta/_")
        self.assertNotEqual(dis1, dis2)

    def test_stage_id_builders_structure(self):
        """build_plan_id and build_execution_id must conform to stage ID schema and preserve suffix."""
        plan_id = build_plan_id(
            1794182557,
            "I-1794182541-unique-plan-and-execution-ids/_",
            "antigravity-agent",
            "gemini-3.8-flash-medium",
            disambiguator="abcdef12",
        )
        self.assertEqual(plan_id, "P-1794182557-abcdef12-antigravity-agent-gemini-3.8-flash-medium")

        exec_id = build_execution_id(
            1794182857,
            "I-1794182541-unique-plan-and-execution-ids/P-1794182557-abcdef12-antigravity-agent-gemini-3.8-flash-medium/_",
            "antigravity-agent",
            "gemini-3.8-flash-medium",
            disambiguator="12345678",
        )
        self.assertEqual(exec_id, "E-1794182857-12345678-antigravity-agent-gemini-3.8-flash-medium")

        # Dynamic disambiguator generation
        auto_plan_id = build_plan_id(
            1794182557,
            "I-1794182541-unique-plan-and-execution-ids/_",
            "agent/runner",
            "model/v1.0",
        )
        pattern = r"^P-1794182557-[0-9a-f]{8}-agent_runner-model_v1\.0$"
        self.assertRegex(auto_plan_id, pattern)

    def test_flow_branch_discovery_suffix_matching(self):
        """flow.py branch discovery relies on branch ending with preferred suffix."""
        agent = "antigravity-agent"
        model = "gemini-3.8-flash-medium"
        preferred_suffix = f"-{agent}-{model}"

        plan_id = build_plan_id(1234567890, "I-123/_", agent, model)
        branch = f"I-123/{plan_id}/_"
        self.assertTrue(branch.removesuffix("/_").endswith(preferred_suffix))

        other_branch = "I-123/P-1234567890-abcdef12-other-agent-other-model/_"
        self.assertFalse(other_branch.removesuffix("/_").endswith(preferred_suffix))

    def test_partial_branch_regex_matching(self):
        """flow.py _resumable_partial_branch regex extracts timestamp sequence from 5-segment branch."""
        branch = "I-123/P-456-dis1-agent-model/E-1794182857-12345678-antigravity-agent-gemini-3.8-flash-medium/_"
        match = re.search(r"/E-(\d+)-[^/]+/_$", branch)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "1794182857")

    def test_ledger_unique_ids_validation_success(self):
        """Unique plan and execution IDs pass validation cleanly."""
        records = [
            {"plan_id": "P-1", "intent_branch": "I-100/_"},
            {"plan_id": "P-2", "intent_branch": "I-200/_"},
            {"plan_id": "P-3", "intent_branch": "I-100/_"},
        ]
        validate_ledger_unique_ids(records, id_key="plan_id", parent_key="intent_branch")

    def test_ledger_unique_ids_validation_duplicate_rejected(self):
        """validate_ledger_unique_ids raises ValueError on duplicate ID across distinct parents."""
        records = [
            {"plan_id": "P-duplicate", "intent_branch": "I-100/_"},
            {"plan_id": "P-duplicate", "intent_branch": "I-200/_"},
        ]
        with self.assertRaises(ValueError) as ctx:
            validate_ledger_unique_ids(records, id_key="plan_id", parent_key="intent_branch")
        self.assertIn("Duplicate plan_id 'P-duplicate' collision detected", str(ctx.exception))

    def test_ledger_unique_ids_validation_idempotent_duplicate_permitted(self):
        """Identical plan_id under the same intent branch is accepted as idempotent update."""
        records = [
            {"plan_id": "P-1", "intent_branch": "I-100/_", "status": "proposed"},
            {"plan_id": "P-1", "intent_branch": "I-100/_", "status": "approved"},
        ]
        validate_ledger_unique_ids(records, id_key="plan_id", parent_key="intent_branch")

    def test_extract_parent_ref_plan_branch_collision_detection(self):
        """_extract_parent_ref distinguishes plans under the same intent for plan_branch parent."""
        rec1 = {
            "execution_id": "E-colliding",
            "plan_branch": "I-100/P-plan-1/_",
        }
        rec2 = {
            "execution_id": "E-colliding",
            "plan_branch": "I-100/P-plan-2/_",
        }

        ref1 = _extract_parent_ref(rec1, "plan_branch")
        ref2 = _extract_parent_ref(rec2, "plan_branch")
        self.assertNotEqual(ref1, ref2)
        self.assertEqual(ref1, "P-plan-1")
        self.assertEqual(ref2, "P-plan-2")

        with self.assertRaises(ValueError) as ctx:
            validate_ledger_unique_ids([rec1, rec2], id_key="execution_id", parent_key="plan_branch")
        self.assertIn("Duplicate execution_id 'E-colliding' collision detected", str(ctx.exception))

    def test_extract_parent_ref_fallbacks(self):
        """_extract_parent_ref supports fallback keys plan_id and intent_id."""
        rec_plan = {"plan_id": "P-12345"}
        self.assertEqual(_extract_parent_ref(rec_plan, "plan_branch"), "P-12345")

        rec_intent = {"intent_id": "I-67890"}
        self.assertEqual(_extract_parent_ref(rec_intent, "intent_branch"), "I-67890")

    def test_validate_ledger_file_unique_ids(self):
        """validate_ledger_file_unique_ids validates file contents and ignores non-existent files."""
        # Non-existent file does not raise
        validate_ledger_file_unique_ids("/non/existent/path/ledger.jsonl")

        with tempfile.TemporaryDirectory() as tmp_dir:
            ledger_file = Path(tmp_dir) / "test_ledger.jsonl"
            ledger_file.write_text(
                json.dumps({"plan_id": "P-1", "intent_branch": "I-100/_"})
                + "\n"
                + json.dumps({"plan_id": "P-1", "intent_branch": "I-200/_"})
                + "\n"
            )
            with self.assertRaises(ValueError):
                validate_ledger_file_unique_ids(str(ledger_file), id_key="plan_id", parent_key="intent_branch")

    def test_validate_stage_ledger_success_and_collision(self):
        """validate_stage_ledger validates plan and execution stage ledgers correctly."""
        # Non-existent files/directories do not raise
        validate_stage_ledger("/non/existent/path", "plan")
        validate_stage_ledger("/non/existent/path", "execute")

        with tempfile.TemporaryDirectory() as tmp_dir:
            ledger_dir = Path(tmp_dir) / "holon-knowledge" / "ledger"
            ledger_dir.mkdir(parents=True, exist_ok=True)

            # Valid plans ledger
            plans_file = ledger_dir / "plans.jsonl"
            plans_file.write_text(
                json.dumps({"plan_id": "P-1", "intent_branch": "I-100/_"})
                + "\n"
                + json.dumps({"plan_id": "P-2", "intent_branch": "I-200/_"})
                + "\n"
            )
            validate_stage_ledger(tmp_dir, "plan")

            # Duplicate plan_id across distinct intents raises
            plans_file.write_text(
                json.dumps({"plan_id": "P-dup", "intent_branch": "I-100/_"})
                + "\n"
                + json.dumps({"plan_id": "P-dup", "intent_branch": "I-200/_"})
                + "\n"
            )
            with self.assertRaises(ValueError):
                validate_stage_ledger(tmp_dir, "plan")

            # Valid executions ledger
            execs_file = ledger_dir / "executions.jsonl"
            execs_file.write_text(
                json.dumps({"execution_id": "E-1", "plan_branch": "I-100/P-1/_"})
                + "\n"
                + json.dumps({"execution_id": "E-2", "plan_branch": "I-100/P-2/_"})
                + "\n"
            )
            validate_stage_ledger(tmp_dir, "execute")

            # Duplicate execution_id across distinct plans raises
            execs_file.write_text(
                json.dumps({"execution_id": "E-dup", "plan_branch": "I-100/P-1/_"})
                + "\n"
                + json.dumps({"execution_id": "E-dup", "plan_branch": "I-100/P-2/_"})
                + "\n"
            )
            with self.assertRaises(ValueError):
                validate_stage_ledger(tmp_dir, "execute")


if __name__ == "__main__":
    unittest.main()
