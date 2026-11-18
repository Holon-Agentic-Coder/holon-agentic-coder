"""Tests for stage ID generation, same-second collision handling, and ledger pair-uniqueness validation."""

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox_executor.entrypoint.planner import PLAN_FILE_PATTERN, plan_file_rel
from sandbox_executor.flow.review.ids import build_review_id
from sandbox_executor.ledger import (
    MAX_SAME_SECOND_BUMPS,
    DuplicateFlowIdError,
    _describe_id_owner,
    _extract_parent_ref,
    build_execution_id,
    build_plan_id,
    validate_ledger_file_unique_ids,
    validate_ledger_unique_ids,
    validate_stage_ledger,
)


class TestSameSecondIds(unittest.TestCase):
    """Ids and file names created in one second stay distinct; old formats stay valid."""

    def test_same_second_plans_under_one_parent_get_distinct_names(self):
        taken: set[str] = set()
        first = build_plan_id(1794446836, "I-1-ids/_", "claude-agent", "claude-sonnet-5-5", exists=taken.__contains__)
        taken.add(first)
        second = build_plan_id(1794446836, "I-1-ids/_", "claude-agent", "claude-sonnet-5-5", exists=taken.__contains__)
        self.assertEqual(first, "P-1794446836-CLA-CS5.5")
        self.assertEqual(second, "P-1794446837-CLA-CS5.5")
        self.assertNotEqual(plan_file_rel(f"I-1-ids/{first}"), plan_file_rel(f"I-1-ids/{second}"))
        self.assertRegex(plan_file_rel(f"I-1-ids/{first}"), PLAN_FILE_PATTERN)

    def test_exhausted_epoch_bumps_fail_clearly_instead_of_overwriting(self):
        with self.assertRaises(DuplicateFlowIdError) as ctx:
            build_plan_id(1794446836, "I-1-ids/_", "claude-agent", "claude-sonnet-5-5", exists=lambda _c: True)
        message = str(ctx.exception)
        self.assertIn("I-1-ids", message)
        self.assertIn(str(MAX_SAME_SECOND_BUMPS), message)

    def test_old_generation_file_names_still_match_plan_pattern(self):
        self.assertRegex("plans/P-1794446836-claude-agent-sonnet.md", PLAN_FILE_PATTERN)
        self.assertRegex("plans/P-1794446836-abcdef12-claude-agent-sonnet.md", PLAN_FILE_PATTERN)
        self.assertRegex("plans/I-1-ids/P-1794446836-CLA-CS5.5.md", PLAN_FILE_PATTERN)
        self.assertRegex("plans/I-1-root/I-1-root--1-kid/P-2-CLA-CS5.5.md", PLAN_FILE_PATTERN)

    def test_review_loop_records_its_summary_under_the_hex_qualified_review_id(self):
        from tests.test_review_loop import _ledger, _loop

        with tempfile.TemporaryDirectory() as tmp_dir:
            from tests.review_fakes import FakeEnsemble

            with patch(
                "sandbox_executor.flow.review.loop.build_review_id", return_value="REV-1-deadbeef-fake-agent"
            ) as build:
                _loop(Path(tmp_dir), FakeEnsemble())
            build.assert_called_once_with("fake-agent")
            self.assertIn("REV-1-deadbeef-fake-agent", [r.get("execution_id") for r in _ledger(Path(tmp_dir))])

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

    def test_duplicate_pair_error_names_both_owners(self):
        records = [
            {"plan_id": "P-x", "intent_branch": "I-100/_", "plan_file": "plans/I-100/P-x.md", "created_at": "t1"},
            {"plan_id": "P-x", "intent_branch": "I-100/_", "plan_file": "plans/I-100/other.md", "created_at": "t2"},
        ]
        with self.assertRaises(ValueError) as ctx:
            validate_ledger_unique_ids(records)
        message = str(ctx.exception)
        for expected in ("I-100", "t1", "t2", "plans/I-100/P-x.md", "plans/I-100/other.md"):
            self.assertIn(expected, message)


class TestUniqueStageIDs(unittest.TestCase):
    def test_stage_id_builders_structure(self):
        """build_plan_id and build_execution_id produce hex-free gen-3 names from ticker and model code."""
        plan_id = build_plan_id(
            1794182557,
            "I-1794182541-unique-plan-and-execution-ids/_",
            "antigravity-agent",
            "gemini-3.8-flash-medium",
        )
        self.assertEqual(plan_id, "P-1794182557-ANA-G3.8FM")
        self.assertNotRegex(plan_id, r"-[0-9a-f]{8}-")

        exec_id = build_execution_id(
            1794182857,
            f"I-1794182541-unique-plan-and-execution-ids/{plan_id}/_",
            "antigravity-agent",
            "gemini-3.8-flash-medium",
        )
        self.assertEqual(exec_id, "E-1794182857-ANA-G3.8FM")

    def test_flow_branch_discovery_suffix_matching(self):
        """flow.py branch discovery relies on branch ending with the ticker-and-model-code suffix."""
        preferred_suffix = "-ANA-G3.8FM"

        plan_id = build_plan_id(1234567890, "I-123/_", "antigravity-agent", "gemini-3.8-flash-medium")
        branch = f"I-123/{plan_id}/_"
        self.assertTrue(branch.removesuffix("/_").endswith(preferred_suffix))

        other_branch = "I-123/P-1234567890-CLA-CS5.5/_"
        self.assertFalse(other_branch.removesuffix("/_").endswith(preferred_suffix))

    def test_partial_branch_regex_matching(self):
        """flow.py _resumable_partial_branch regex extracts timestamp sequence from 5-segment branch."""
        branch = "I-123/P-456-dis1-agent-model/E-1794182857-12345678-antigravity-agent-gemini-3.8-flash-medium/_"
        match = re.search(r"/E-(\d+)-[^/]+/_$", branch)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "1794182857")

    def test_ledger_unique_ids_validation_success(self):
        """Distinct (intent, plan id) pairs pass validation cleanly."""
        records = [
            {"plan_id": "P-1", "intent_branch": "I-100/_", "plan_file": "plans/I-100/P-1.md"},
            {"plan_id": "P-2", "intent_branch": "I-200/_", "plan_file": "plans/I-200/P-2.md"},
            {"plan_id": "P-3", "intent_branch": "I-100/_", "plan_file": "plans/I-100/P-3.md"},
        ]
        validate_ledger_unique_ids(records, id_key="plan_id", parent_key="intent_branch")

    def test_same_plan_id_under_distinct_intents_is_legal(self):
        """The same gen-3 id under two intents (same second, same runner and model) is two separate plans."""
        records = [
            {"plan_id": "P-1-CLA-CS5.5", "intent_branch": "I-100/_", "plan_file": "plans/I-100/P-1-CLA-CS5.5.md"},
            {"plan_id": "P-1-CLA-CS5.5", "intent_branch": "I-200/_", "plan_file": "plans/I-200/P-1-CLA-CS5.5.md"},
        ]
        validate_ledger_unique_ids(records, id_key="plan_id", parent_key="intent_branch")

    def test_repeated_pair_with_two_plan_files_is_rejected(self):
        """A repeated (intent, plan id) pair naming two different plan files fails the duplicate check."""
        records = [
            {"plan_id": "P-dup", "intent_branch": "I-100/_", "plan_file": "plans/I-100/P-dup.md"},
            {"plan_id": "P-dup", "intent_branch": "I-100/_", "plan_file": "plans/I-100/P-dup-second.md"},
        ]
        with self.assertRaises(ValueError) as ctx:
            validate_ledger_unique_ids(records, id_key="plan_id", parent_key="intent_branch")
        self.assertIn("Duplicate plan_id 'P-dup' under the same parent", str(ctx.exception))

    def test_ledger_unique_ids_validation_superseding_rows_permitted(self):
        """Superseding rows repeating the pair for the same plan file are accepted."""
        records = [
            {"plan_id": "P-1", "intent_branch": "I-100/_", "plan_file": "plans/I-100/P-1.md", "status": "proposed"},
            {"plan_id": "P-1", "intent_branch": "I-100/_", "plan_file": "plans/I-100/P-1.md", "status": "approved"},
        ]
        validate_ledger_unique_ids(records, id_key="plan_id", parent_key="intent_branch")

    def test_extract_parent_ref_scopes_executions_to_their_plan(self):
        """_extract_parent_ref distinguishes plans under the same intent for plan_branch parent."""
        rec1 = {"execution_id": "E-colliding", "plan_branch": "I-100/P-plan-1/_", "execution_file": "executions/a.md"}
        rec2 = {"execution_id": "E-colliding", "plan_branch": "I-100/P-plan-2/_", "execution_file": "executions/b.md"}

        ref1 = _extract_parent_ref(rec1, "plan_branch")
        ref2 = _extract_parent_ref(rec2, "plan_branch")
        self.assertNotEqual(ref1, ref2)
        self.assertEqual(ref1, "P-plan-1")
        self.assertEqual(ref2, "P-plan-2")

        # Distinct parents mean distinct pairs, so the shared id is legal under the keyed scheme.
        validate_ledger_unique_ids([rec1, rec2], id_key="execution_id", parent_key="plan_branch")

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
                json.dumps({"plan_id": "P-1", "intent_branch": "I-100/_", "plan_file": "plans/I-100/P-1.md"})
                + "\n"
                + json.dumps({"plan_id": "P-1", "intent_branch": "I-100/_", "plan_file": "plans/I-100/P-1b.md"})
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
                json.dumps({"plan_id": "P-1", "intent_branch": "I-100/_", "plan_file": "plans/I-100/P-1.md"})
                + "\n"
                + json.dumps({"plan_id": "P-2", "intent_branch": "I-200/_", "plan_file": "plans/I-200/P-2.md"})
                + "\n"
            )
            validate_stage_ledger(tmp_dir, "plan")

            # A repeated (intent, plan id) pair naming two plan files raises
            plans_file.write_text(
                json.dumps({"plan_id": "P-dup", "intent_branch": "I-100/_", "plan_file": "plans/I-100/P-dup.md"})
                + "\n"
                + json.dumps({"plan_id": "P-dup", "intent_branch": "I-100/_", "plan_file": "plans/I-100/P-x.md"})
                + "\n"
            )
            with self.assertRaises(ValueError):
                validate_stage_ledger(tmp_dir, "plan")

            # Valid executions ledger
            execs_file = ledger_dir / "executions.jsonl"
            execs_file.write_text(
                json.dumps({"execution_id": "E-1", "plan_branch": "I-100/P-1/_", "execution_file": "executions/1.md"})
                + "\n"
                + json.dumps({"execution_id": "E-2", "plan_branch": "I-100/P-2/_", "execution_file": "executions/2.md"})
                + "\n"
            )
            validate_stage_ledger(tmp_dir, "execute")

            # A repeated (plan, execution id) pair naming two execution files raises
            execs_file.write_text(
                json.dumps(
                    {"execution_id": "E-dup", "plan_branch": "I-100/P-1/_", "execution_file": "executions/a.md"}
                )
                + "\n"
                + json.dumps(
                    {"execution_id": "E-dup", "plan_branch": "I-100/P-1/_", "execution_file": "executions/b.md"}
                )
                + "\n"
            )
            with self.assertRaises(ValueError):
                validate_stage_ledger(tmp_dir, "execute")


if __name__ == "__main__":
    unittest.main()
