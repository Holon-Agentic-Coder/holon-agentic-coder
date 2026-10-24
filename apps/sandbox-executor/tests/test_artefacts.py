import glob
import os
import pathlib
import tempfile
import unittest

from sandbox_executor.artefacts import (
    EXECUTION_SPEC,
    PLAN_SPEC,
    build_reprompt,
    execution_write_protocol,
    parse_first_metrics_table,
    plan_write_protocol,
    validate_artefact,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

PLAN = """# Plan for I-1

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.8   |
| entropy_pred        | 2.5   |
| impact_pred         | 10.0  |
| cost_pred           | 0.5   |
| learning_value_pred | 0.5   |
| ev_pred             | 6.0   |

### Strategy Rationale

{filler}

## Step 1: Do the thing

| metric         | value |
| -------------- | ----- |
| p_success_pred | 0.1   |
"""

NOTES = """# Execution Record: E-1

## Summary

{filler}

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 1     |
| entropy_actual        | 1.2   |
| impact_actual         | 8.0   |
| cost_actual           | 2.0   |
| learning_value_actual | 3.0   |

## Steps Executed

- Step 1 done.

## Tests Run

- `uv run pytest` passed.
"""


class TestArtefacts(unittest.TestCase):
    def write(self, content):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "artefact.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_complete_plan_is_usable_and_complete(self):
        report = validate_artefact(self.write(PLAN.format(filler="Reasoning. " * 80)), PLAN_SPEC)
        self.assertTrue(report.usable)
        self.assertTrue(report.complete)
        self.assertEqual(report.metrics["p_success"], 0.8)

    def test_only_the_first_metrics_table_counts(self):
        # A step table must never stand in for the overall table.
        content = PLAN.format(filler="x").replace("| p_success_pred      | 0.8   |\n", "")
        self.assertNotIn("p_success", parse_first_metrics_table(content))
        report = validate_artefact(self.write(content + "y" * 900), PLAN_SPEC)
        self.assertEqual(report.missing_metrics, ["p_success"])
        self.assertFalse(report.usable)

    def test_non_numeric_metric_is_missing(self):
        content = PLAN.format(filler="z" * 900).replace("| cost_pred           | 0.5   |", "| cost_pred | TBD |")
        report = validate_artefact(self.write(content), PLAN_SPEC)
        self.assertEqual(report.missing_metrics, ["cost"])

    def test_ev_row_is_optional(self):
        # The planner recomputes EV from the five inputs, so a non-numeric or absent ev_pred row is not missing.
        content = PLAN.format(filler="z" * 900).replace("| ev_pred             | 6.0   |", "| ev_pred | TBD |")
        report = validate_artefact(self.write(content), PLAN_SPEC)
        self.assertEqual(report.missing_metrics, [])
        self.assertTrue(report.metrics_ok)

    def test_size_floor(self):
        report = validate_artefact(self.write(PLAN.format(filler="short")), PLAN_SPEC)
        self.assertTrue(report.metrics_ok)
        self.assertFalse(report.size_ok)
        self.assertFalse(report.usable)

    def test_missing_sections_are_listed_by_label(self):
        content = PLAN.format(filler="w" * 900).replace("### Strategy Rationale", "### Something else")
        report = validate_artefact(self.write(content), PLAN_SPEC)
        self.assertTrue(report.usable)
        self.assertFalse(report.complete)
        self.assertEqual(len(report.missing_sections), 1)
        self.assertIn("Strategy Rationale", report.missing_sections[0])

    def test_headings_match_case_insensitively(self):
        content = PLAN.format(filler="v" * 900).replace("## Overall Plan Metrics", "## overall plan metrics")
        self.assertTrue(validate_artefact(self.write(content), PLAN_SPEC).complete)

    def test_missing_file(self):
        report = validate_artefact("/nonexistent/plan.md", PLAN_SPEC)
        self.assertFalse(report.exists)
        self.assertFalse(report.usable)
        self.assertEqual(len(report.missing_sections), len(PLAN_SPEC.sections))

    def test_execution_notes_accept_actual_suffix(self):
        report = validate_artefact(self.write(NOTES.format(filler="Did the work. " * 20)), EXECUTION_SPEC)
        self.assertTrue(report.complete, report.describe())
        self.assertEqual(report.metrics["success"], 1.0)

    def test_reprompt_lists_only_missing_sections(self):
        text = build_reprompt(["`### Strategy Rationale`"], "plans/P-1.md")
        self.assertIn("plans/P-1.md", text)
        self.assertIn("- `### Strategy Rationale`", text)
        self.assertNotIn("Overall Plan Metrics", text)
        self.assertEqual(text.count("\n- "), 1)

    def test_reprompt_for_partial_executor_notes_forbids_code_changes(self):
        text = build_reprompt(["`## Tests Run`"], "executions/E-1.md", partial=True, modify_code=False)
        self.assertIn("partial", text)
        self.assertIn("Do not modify any code", text)

    def test_write_protocols_name_the_target_file(self):
        self.assertIn("plans/P-1.md", plan_write_protocol("plans/P-1.md"))
        self.assertIn("executions/E-1.md", execution_write_protocol("executions/E-1.md"))

    def test_existing_repository_plans_pass_validation(self):
        """False-negative guard: the validator must accept at least 90 per cent of the repository's real plans.

        Calibration reports and the short synthetic stubs written by the dry flow stage are not authored plans.
        """
        paths = [
            p
            for p in glob.glob(str(REPO_ROOT / "plans" / "P-*.md"))
            if not p.endswith("_calibration.md") and os.path.getsize(p) >= PLAN_SPEC.size_floor
        ]
        if not paths:
            self.skipTest("no repository plans available")
        accepted = [p for p in paths if validate_artefact(p, PLAN_SPEC).complete]
        self.assertGreaterEqual(len(accepted) / len(paths), 0.9)
