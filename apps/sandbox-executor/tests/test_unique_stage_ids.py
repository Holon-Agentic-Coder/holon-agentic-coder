"""Tests for stage ID generation, disambiguation, and ledger uniqueness validation."""

import json
import re
import tempfile
import unittest
from pathlib import Path

from sandbox_executor.ledger import (
    _extract_parent_ref,
    build_execution_id,
    build_plan_id,
    build_stage_disambiguator,
    validate_ledger_file_unique_ids,
    validate_ledger_unique_ids,
)


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


if __name__ == "__main__":
    unittest.main()
