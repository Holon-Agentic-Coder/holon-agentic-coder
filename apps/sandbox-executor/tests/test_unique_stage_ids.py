"""Tests for stage ID generation, same-second collision handling, and pair-keyed ledger validation."""

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox_executor.entrypoint.planner import PLAN_FILE_PATTERN
from sandbox_executor.flow.review.ids import build_review_id
from sandbox_executor.ledger import (
    _describe_id_owner,
    _extract_parent_ref,
    build_execution_id,
    build_plan_id,
    validate_ledger_file_unique_ids,
    validate_ledger_unique_ids,
    validate_stage_ledger,
)
from sandbox_executor.plan_paths import DuplicateStageNameError, plan_file_rel


class TestSameSecondIds(unittest.TestCase):
    """Same-second names under one parent never overwrite; same names under different parents are legal."""

    def test_two_intents_planned_in_the_same_second_produce_distinct_plan_files(self):
        plan_id = build_plan_id(1794446836, "I-1-alpha/_", "claude-agent", "claude-sonnet-5-5")
        same_id = build_plan_id(1794446836, "I-2-beta/_", "claude-agent", "claude-sonnet-5-5")
        self.assertEqual(plan_id, same_id)  # the id repeats; the intent-keyed paths differ
        first = plan_file_rel(f"I-1-alpha/{plan_id}")
        second = plan_file_rel(f"I-2-beta/{same_id}")
        self.assertNotEqual(first, second)
        self.assertRegex(first, PLAN_FILE_PATTERN)
        self.assertRegex(second, PLAN_FILE_PATTERN)

    def test_same_second_same_parent_bumps_the_epoch_instead_of_overwriting(self):
        taken = {"P-1794446836-CLA-CS5.5"}
        second = build_plan_id(1794446836, "I-1-alpha/_", "claude-agent", "claude-sonnet-5-5", exists=taken.__contains__)
        self.assertEqual(second, "P-1794446837-CLA-CS5.5")

    def test_exhausted_epoch_bumps_fail_clearly_and_never_overwrite(self):
        with self.assertRaises(DuplicateStageNameError) as ctx:
            build_execution_id(1794446836, "I-1-a/P-1-x/_", "claude-agent", "claude-sonnet-5-5", exists=lambda _: True)
        self.assertIn("E-1794446836-CLA-CS5.5", str(ctx.exception))
        self.assertIn("refusing to overwrite", str(ctx.exception))

    def test_old_generation_file_names_still_match_the_plan_pattern(self):
        self.assertRegex("plans/P-1794446836-claude-agent-sonnet.md", PLAN_FILE_PATTERN)
        self.assertRegex("plans/P-1794446836-abcdef12-claude-agent-sonnet.md", PLAN_FILE_PATTERN)
        self.assertRegex("plans/I-1-alpha/P-1794446836-CLA-CS5.5.md", PLAN_FILE_PATTERN)

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

    def test_old_and_new_review_ids_validate_together(self):
        records = [
            {"execution_id": "REV-1794446836-claude-agent", "plan_branch": "I-1/P-1/_"},
            {"execution_id": build_review_id("claude-agent"), "plan_branch": "I-2/P-2/_"},
        ]
        validate_ledger_unique_ids(records, id_key="execution_id", parent_key="plan_branch")

    def test_duplicate_pair_error_names_both_owners(self):
        records = [
            {"plan_id": "P-x", "intent_branch": "I-100/_", "agent": "alpha", "created_at": "t1"},
            {"plan_id": "P-x", "intent_branch": "I-100/_", "agent": "beta", "created_at": "t2"},
        ]
        with self.assertRaises(ValueError) as ctx:
            validate_ledger_unique_ids(records)
        message = str(ctx.exception)
        for expected in ("I-100", "alpha", "t1", "beta", "t2"):
            self.assertIn(expected, message)


class TestGenerationThreeIds(unittest.TestCase):
    def test_stage_id_builders_structure(self):
        """build_plan_id and build_execution_id produce P-/E-{epoch}-{ticker}-{model code} with no hex."""
        plan_id = build_plan_id(1794182557, "I-1794182541-shortref/_", "claude-agent", "claude-sonnet-5-5")
        self.assertEqual(plan_id, "P-1794182557-CLA-CS5.5")

        exec_id = build_execution_id(
            1794182857,
            f"I-1794182541-shortref/{plan_id}/_",
            "antigravity-agent",
            "gemini-3.8-flash-medium",
        )
        self.assertEqual(exec_id, "E-1794182857-ANA-G3.8FM")
        self.assertIsNone(re.search(r"-[0-9a-f]{8}-", plan_id))

    def test_flow_branch_discovery_suffix_matching(self):
        """Branch discovery relies on the branch ending with the ticker-and-model-code suffix."""
        plan_id = build_plan_id(1234567890, "I-123/_", "pi-agent", "claude-opus-5-5")
        branch = f"I-123/{plan_id}/_"
        self.assertTrue(branch.removesuffix("/_").endswith("-PIA-CO5.5"))

        other_branch = "I-123/P-1234567890-abcdef12-other-agent-other-model/_"
        self.assertFalse(other_branch.removesuffix("/_").endswith("-PIA-CO5.5"))

    def test_partial_branch_regex_matching(self):
        """_resumable_partial_branch's regex extracts the epoch from both old and new generation branches."""
        for branch in (
            "I-123/P-456-dis1-agent-model/E-1794182857-12345678-antigravity-agent-gemini-3.8-flash-medium/_",
            "I-123/P-456-CLA-CF5/E-1794182857-CLA-CF5/_",
        ):
            match = re.search(r"/E-(\d+)-[^/]+/_$", branch)
            self.assertIsNotNone(match)
            self.assertEqual(match.group(1), "1794182857")


class TestPairKeyedLedgerValidation(unittest.TestCase):
    def test_unique_pairs_pass_validation(self):
        records = [
            {"plan_id": "P-1", "intent_branch": "I-100/_"},
            {"plan_id": "P-2", "intent_branch": "I-200/_"},
            {"plan_id": "P-3", "intent_branch": "I-100/_"},
        ]
        validate_ledger_unique_ids(records, id_key="plan_id", parent_key="intent_branch")

    def test_same_id_under_different_intents_is_legal(self):
        """Two intents planned in the same second by the same runner and model legally share a plan id."""
        records = [
            {"plan_id": "P-1794446836-CLA-CS5.5", "intent_branch": "I-100-alpha/_"},
            {"plan_id": "P-1794446836-CLA-CS5.5", "intent_branch": "I-200-beta/_"},
        ]
        validate_ledger_unique_ids(records, id_key="plan_id", parent_key="intent_branch")

    def test_repeated_intent_and_plan_pair_is_rejected(self):
        records = [
            {"plan_id": "P-duplicate", "intent_branch": "I-100/_"},
            {"plan_id": "P-duplicate", "intent_branch": "I-100/_"},
        ]
        with self.assertRaises(ValueError) as ctx:
            validate_ledger_unique_ids(records, id_key="plan_id", parent_key="intent_branch")
        self.assertIn("Duplicate plan_id 'P-duplicate' repeated under the same parent", str(ctx.exception))

    def test_superseding_revision_of_the_same_pair_is_legal(self):
        """The flow's back-filled decision row repeats the pair with a bumped ledger_revision."""
        records = [
            {"plan_id": "P-1", "intent_branch": "I-100/_", "status": "proposed"},
            {"plan_id": "P-1", "intent_branch": "I-100/_", "status": "proposed", "ledger_revision": 2},
        ]
        validate_ledger_unique_ids(records, id_key="plan_id", parent_key="intent_branch")

    def test_extract_parent_ref_keeps_the_full_parent_branch(self):
        """Plans nested under plans key on their full parent path, not just one segment."""
        rec1 = {"execution_id": "E-colliding", "plan_branch": "I-100/P-plan-1/_"}
        rec2 = {"execution_id": "E-colliding", "plan_branch": "I-100/P-plan-2/_"}
        self.assertEqual(_extract_parent_ref(rec1, "plan_branch"), "I-100/P-plan-1")
        self.assertEqual(_extract_parent_ref(rec2, "plan_branch"), "I-100/P-plan-2")
        # Different parents: the repeated execution id is legal under the pair keying.
        validate_ledger_unique_ids([rec1, rec2], id_key="execution_id", parent_key="plan_branch")

    def test_extract_parent_ref_fallbacks(self):
        rec_plan = {"plan_id": "P-12345"}
        self.assertEqual(_extract_parent_ref(rec_plan, "plan_branch"), "P-12345")

        rec_intent = {"intent_id": "I-67890"}
        self.assertEqual(_extract_parent_ref(rec_intent, "intent_branch"), "I-67890")

    def test_validate_ledger_file_unique_ids(self):
        """File validation rejects a repeated pair and ignores non-existent files."""
        validate_ledger_file_unique_ids("/non/existent/path/ledger.jsonl")

        with tempfile.TemporaryDirectory() as tmp_dir:
            ledger_file = Path(tmp_dir) / "test_ledger.jsonl"
            ledger_file.write_text(
                json.dumps({"plan_id": "P-1", "intent_branch": "I-100/_"})
                + "\n"
                + json.dumps({"plan_id": "P-1", "intent_branch": "I-100/_"})
                + "\n"
            )
            with self.assertRaises(ValueError):
                validate_ledger_file_unique_ids(str(ledger_file), id_key="plan_id", parent_key="intent_branch")

    def test_validate_stage_ledger_pair_semantics(self):
        """The plan stage rejects a repeated pair; the execute stage is guarded at creation time instead."""
        validate_stage_ledger("/non/existent/path", "plan")
        validate_stage_ledger("/non/existent/path", "execute")

        with tempfile.TemporaryDirectory() as tmp_dir:
            ledger_dir = Path(tmp_dir) / "holon-knowledge" / "ledger"
            ledger_dir.mkdir(parents=True, exist_ok=True)

            plans_file = ledger_dir / "plans.jsonl"
            plans_file.write_text(
                json.dumps({"plan_id": "P-dup", "intent_branch": "I-100/_"})
                + "\n"
                + json.dumps({"plan_id": "P-dup", "intent_branch": "I-200/_"})
                + "\n"
            )
            validate_stage_ledger(tmp_dir, "plan")  # same id under different intents is legal

            plans_file.write_text(
                json.dumps({"plan_id": "P-dup", "intent_branch": "I-100/_"})
                + "\n"
                + json.dumps({"plan_id": "P-dup", "intent_branch": "I-100/_"})
                + "\n"
            )
            with self.assertRaises(ValueError):
                validate_stage_ledger(tmp_dir, "plan")

            # The historical executions ledger carries repeated pairs without revision bumps, so the execute
            # stage runs no whole-file pair check; creation-time 'exists' checks guard new names instead.
            execs_file = ledger_dir / "executions.jsonl"
            execs_file.write_text(
                json.dumps({"execution_id": "E-dup", "plan_branch": "I-100/P-1/_"})
                + "\n"
                + json.dumps({"execution_id": "E-dup", "plan_branch": "I-100/P-1/_"})
                + "\n"
            )
            validate_stage_ledger(tmp_dir, "execute")


if __name__ == "__main__":
    unittest.main()
