import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
from sandbox_executor.plan_paths import PLAN_FILE_PATTERN, plan_file_rel


class TestSameSecondIds(unittest.TestCase):
    def test_plan_ids_and_file_names_differ_within_one_second(self):
        first = build_plan_id(1794446836, "I-1/ids", "claude-agent", "claude-sonnet-5-5")
        second = build_plan_id(1794446836, "I-1/ids", "claude-agent", "claude-sonnet-5-5", taken={first})
        self.assertNotEqual(first, second)
        self.assertEqual(second, f"{first}-2")
        self.assertNotEqual(plan_file_rel("I-1/ids", first), plan_file_rel("I-1/ids", second))
        self.assertEqual(plan_file_rel("I-1/ids", first), f"plans/I-1/ids/{first}.md")
        self.assertRegex(plan_file_rel("I-1-ids", first), PLAN_FILE_PATTERN)
        self.assertRegex(plan_file_rel("I-1-ids", second), PLAN_FILE_PATTERN)

    def test_same_second_twin_intents_get_distinct_plan_files(self):
        first = build_plan_id(1794446836, "I-1-alpha", "claude-agent", "claude-sonnet-5-5")
        twin = build_plan_id(1794446836, "I-1-beta", "claude-agent", "claude-sonnet-5-5")
        self.assertEqual(first, twin)
        self.assertNotEqual(plan_file_rel("I-1-alpha", first), plan_file_rel("I-1-beta", twin))

    def test_execution_ids_differ_within_one_second(self):
        first = build_execution_id(1794446836, "I-1/ids/P-1-CLA-CS5.5", "claude-agent", "claude-sonnet-5-5")
        second = build_execution_id(
            1794446836, "I-1/ids/P-1-CLA-CS5.5", "claude-agent", "claude-sonnet-5-5", taken={first}
        )
        self.assertEqual(first, "E-1794446836-CLA-CS5.5")
        self.assertEqual(second, f"{first}-2")

    def test_hexless_plan_file_name_still_matches_plan_pattern(self):
        self.assertRegex("plans/P-1794446836-claude-agent-sonnet.md", PLAN_FILE_PATTERN)

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

    def test_duplicate_error_names_both_owners(self):
        records = [
            {"plan_id": "P-x", "intent_branch": "I-100/_", "agent": "alpha", "created_at": "t1"},
            {"plan_id": "P-x", "intent_branch": "I-100/_", "agent": "beta", "created_at": "t2"},
        ]
        with self.assertRaises(ValueError) as ctx:
            validate_ledger_unique_ids(records)
        message = str(ctx.exception)
        for expected in ("I-100", "alpha", "t1", "beta", "t2"):
            self.assertIn(expected, message)


class TestUniqueStageIDs(unittest.TestCase):
    def test_disambiguator_uniqueness_across_rapid_invocations(self):
        parent_branch = "I-1794182541-unique-plan-and-execution-ids/_"
        count = 100
        disambiguators = [build_stage_disambiguator(parent_branch) for _ in range(count)]

        self.assertEqual(len(set(disambiguators)), count)
        for dis in disambiguators:
            self.assertTrue(re.fullmatch(r"^[0-9a-f]{8}$", dis), f"Invalid disambiguator format: {dis}")

    def test_disambiguator_uniqueness_across_different_branches(self):
        dis1 = build_stage_disambiguator("I-100-alpha/_")
        dis2 = build_stage_disambiguator("I-200-beta/_")
        self.assertNotEqual(dis1, dis2)

    def test_stage_id_builders_structure(self):
        plan_id = build_plan_id(
            1794182557,
            "I-1794182541-unique-plan-and-execution-ids/_",
            "antigravity-agent",
            "gemini-3.8-flash-medium",
        )
        self.assertEqual(plan_id, "P-1794182557-ANA-G3.8FM")

        exec_id = build_execution_id(
            1794182857,
            "I-1794182541-unique-plan-and-execution-ids/P-1794182557-ANA-G3.8FM/_",
            "antigravity-agent",
            "gemini-3.8-flash-medium",
        )
        self.assertEqual(exec_id, "E-1794182857-ANA-G3.8FM")

    def test_flow_branch_discovery_suffix_matching(self):
        agent = "antigravity-agent"
        model = "gemini-3.8-flash-medium"
        preferred_suffix = "-ANA-G3.8FM"

        plan_id = build_plan_id(1234567890, "I-123/_", agent, model)
        branch = f"I-123/{plan_id}/_"
        self.assertTrue(branch.removesuffix("/_").endswith(preferred_suffix))

        other_branch = "I-123/P-1234567890-CLA-CO5.5/_"
        self.assertFalse(other_branch.removesuffix("/_").endswith(preferred_suffix))

    def test_partial_branch_regex_matching(self):
        branch = "I-123/P-456-dis1-agent-model/E-1794182857-12345678-antigravity-agent-gemini-3.8-flash-medium/_"
        match = re.search(r"/E-(\d+)-[^/]+/_$", branch)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "1794182857")

    def test_ledger_unique_ids_validation_success(self):
        records = [
            {"plan_id": "P-1", "intent_branch": "I-100/_"},
            {"plan_id": "P-2", "intent_branch": "I-200/_"},
            {"plan_id": "P-3", "intent_branch": "I-100/_"},
        ]
        validate_ledger_unique_ids(records, id_key="plan_id", parent_key="intent_branch")

    def test_ledger_unique_ids_validation_repeated_intent_and_plan_pair_rejected(self):
        records = [
            {"plan_id": "P-duplicate", "intent_branch": "I-100/_", "status": "proposed"},
            {"plan_id": "P-duplicate", "intent_branch": "I-100/_", "status": "approved"},
        ]
        with self.assertRaises(ValueError) as ctx:
            validate_ledger_unique_ids(records, id_key="plan_id", parent_key="intent_branch")
        self.assertIn("Duplicate plan_id 'P-duplicate' recorded twice for the same intent", str(ctx.exception))

    def test_ledger_unique_ids_same_plan_id_under_two_intents_permitted(self):
        records = [
            {"plan_id": "P-1794567531-CLA-CS5.5", "intent_branch": "I-1794567515-alpha/_"},
            {"plan_id": "P-1794567531-CLA-CS5.5", "intent_branch": "I-1794567515-beta/_"},
            {"plan_id": "P-1794567531-CLA-CS5.5", "intent_branch": "I-1794567515-alpha--1-child/_"},
        ]
        validate_ledger_unique_ids(records, id_key="plan_id", parent_key="intent_branch")

    def test_ledger_unique_ids_validation_superseding_row_permitted(self):
        records = [
            {"plan_id": "P-1", "intent_branch": "I-100/_", "status": "proposed"},
            {"plan_id": "P-1", "intent_branch": "I-100/_", "status": "approved", "ledger_revision": 2},
        ]
        validate_ledger_unique_ids(records, id_key="plan_id", parent_key="intent_branch")

    def test_extract_parent_ref_plan_branch_collision_detection(self):
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
        self.assertEqual(ref1, "I-100/P-plan-1")
        self.assertEqual(ref2, "I-100/P-plan-2")
        self.assertEqual(_extract_parent_ref({"plan_branch": "I-100/P-plan-1/E-colliding/_"}, "plan_branch"), ref1)
        self.assertEqual(
            _extract_parent_ref({"intent_branch": "I-1-a/P-2-b--1-child/_"}, "intent_branch"), "I-1-a/P-2-b--1-child"
        )

        validate_ledger_unique_ids([rec1, rec2], id_key="execution_id", parent_key="plan_branch")

    def test_extract_parent_ref_falls_back_to_plan_id_and_intent_id(self):
        rec_plan = {"plan_id": "P-12345"}
        self.assertEqual(_extract_parent_ref(rec_plan, "plan_branch"), "P-12345")

        rec_intent = {"intent_id": "I-67890"}
        self.assertEqual(_extract_parent_ref(rec_intent, "intent_branch"), "I-67890")

    def test_validate_ledger_file_unique_ids_checks_contents_and_ignores_missing_files(self):
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

    def test_validate_stage_ledger_success_and_collision(self):
        validate_stage_ledger("/non/existent/path", "plan")
        validate_stage_ledger("/non/existent/path", "execute")

        with tempfile.TemporaryDirectory() as tmp_dir:
            ledger_dir = Path(tmp_dir) / "holon-knowledge" / "ledger"
            ledger_dir.mkdir(parents=True, exist_ok=True)

            plans_file = ledger_dir / "plans.jsonl"
            plans_file.write_text(
                json.dumps({"plan_id": "P-1", "intent_branch": "I-100/_"})
                + "\n"
                + json.dumps({"plan_id": "P-2", "intent_branch": "I-200/_"})
                + "\n"
            )
            validate_stage_ledger(tmp_dir, "plan")

            plans_file.write_text(
                json.dumps({"plan_id": "P-dup", "intent_branch": "I-100/_"})
                + "\n"
                + json.dumps({"plan_id": "P-dup", "intent_branch": "I-100/_"})
                + "\n"
            )
            with self.assertRaises(ValueError):
                validate_stage_ledger(tmp_dir, "plan")

            execs_file = ledger_dir / "executions.jsonl"
            execs_file.write_text(
                json.dumps({"execution_id": "E-1", "plan_branch": "I-100/P-1/_"})
                + "\n"
                + json.dumps({"execution_id": "E-2", "plan_branch": "I-100/P-2/_"})
                + "\n"
            )
            validate_stage_ledger(tmp_dir, "execute")

            execs_file.write_text(
                json.dumps({"execution_id": "E-dup", "plan_branch": "I-100/P-1/_"})
                + "\n"
                + json.dumps({"execution_id": "E-dup", "plan_branch": "I-100/P-2/_"})
                + "\n"
            )
            validate_stage_ledger(tmp_dir, "execute")


if __name__ == "__main__":
    unittest.main()
