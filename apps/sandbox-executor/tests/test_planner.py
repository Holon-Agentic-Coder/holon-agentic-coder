import io
import json
import os
import pathlib
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.budgets import DEFAULT_ENTROPY_BUDGET, BudgetBreach
from sandbox_executor.entrypoint import planner
from sandbox_executor.turns import TurnResult

from tests.leak_assertions import assert_no_fragment, make_gh_probe


@patch.dict("os.environ", {"HOLON_AGENT_KEY": "dummy", "HOLON_REPO_URL": "git@github.com:example/repo.git"})
class TestPlanner(unittest.TestCase):
    def test_get_file_structure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = pathlib.Path(tmp_dir)
            # Create some mock files and directories
            (tmp_path / "dir1").mkdir()
            (tmp_path / "dir1" / "file1.py").write_text("print('hello')")
            (tmp_path / "dir2").mkdir()
            (tmp_path / ".git").mkdir()  # should be ignored
            (tmp_path / "dir1" / ".hidden").write_text("hidden")  # should be ignored

            structure = planner.get_file_structure(str(tmp_path))
            self.assertIn("dir1/", structure)
            self.assertIn("file1.py", structure)
            self.assertNotIn(".git", structure)
            self.assertNotIn(".hidden", structure)

    def test_parse_metrics(self):
        content = """
# Plan

| metric              | value                 |
|---------------------|-----------------------|
| p_success_pred      | 0.85                  |
| entropy_pred        | 2.1                   |
| impact_pred         | 1.0                   |
| cost_pred           | 0.4                   |
| learning_value_pred | 3.5                   |
| ev_pred             | 4.2                   |
"""
        metrics = planner.parse_metrics(content)
        self.assertEqual(metrics.get("p_success"), 0.85)
        self.assertEqual(metrics.get("entropy"), 2.1)
        self.assertEqual(metrics.get("impact"), 1.0)
        self.assertEqual(metrics.get("cost"), 0.4)
        self.assertEqual(metrics.get("learning_value"), 3.5)

    def test_parse_metrics_prefers_overall_section_over_step_tables(self):
        content = """
# Plan

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 5.8   |
| impact_pred         | 85.0  |
| cost_pred           | 16.0  |
| learning_value_pred | 4.0   |
| ev_pred             | 52.26 |

### Strategy Rationale

Subsections of the overall section are still in scope.

## Proposed Steps

### Step 5

#### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.9   |
| entropy_pred        | 0.4   |
| impact_pred         | 10.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 7.38  |
"""
        metrics = planner.parse_metrics(content)
        self.assertEqual(metrics.get("p_success"), 0.80)
        self.assertEqual(metrics.get("entropy"), 5.8)
        self.assertEqual(metrics.get("impact"), 85.0)
        self.assertEqual(metrics.get("cost"), 16.0)
        self.assertEqual(metrics.get("learning_value"), 4.0)
        self.assertEqual(metrics.get("ev"), 52.26)

    def test_parse_metrics_overall_section_at_end_of_document(self):
        content = """
# Plan

### Step 1 Metrics

| metric         | value |
| -------------- | ----- |
| impact_pred    | 3.0   |

## Overall Plan Metrics

| metric         | value |
| -------------- | ----- |
| impact_pred    | 42.0  |
"""
        metrics = planner.parse_metrics(content)
        self.assertEqual(metrics, {"impact": 42.0})

    def test_load_metrics_config(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Case 1: missing config file -> default values (lambda=0.3, mu=0.5)
            config = planner.load_metrics_config(tmp_dir)
            self.assertEqual(config["lambda"], 0.3)
            self.assertEqual(config["mu"], 0.5)

            # Case 2: valid ev_config.json present
            metrics_dir = pathlib.Path(tmp_dir) / "holon-config" / "metrics"
            metrics_dir.mkdir(parents=True)
            ev_file = metrics_dir / "ev_config.json"
            ev_file.write_text(json.dumps({"lambda": 0.4, "mu": 0.6}))

            config2 = planner.load_metrics_config(tmp_dir)
            self.assertEqual(config2["lambda"], 0.4)
            self.assertEqual(config2["mu"], 0.6)

            # Case 3: malformed JSON -> defaults returned, no exception raised
            ev_file.write_text("not valid json")
            config3 = planner.load_metrics_config(tmp_dir)
            self.assertEqual(config3["lambda"], 0.3)
            self.assertEqual(config3["mu"], 0.5)

            # Case 4: partial keys -> present key used, missing key falls back to default
            ev_file.write_text(json.dumps({"lambda": 0.2}))
            config4 = planner.load_metrics_config(tmp_dir)
            self.assertEqual(config4["lambda"], 0.2)
            self.assertEqual(config4["mu"], 0.5)

            # Case 5: non-numeric value -> falls back to default for that key
            ev_file.write_text(json.dumps({"lambda": "abc", "mu": 0.5}))
            config5 = planner.load_metrics_config(tmp_dir)
            self.assertEqual(config5["lambda"], 0.3)
            self.assertEqual(config5["mu"], 0.5)

            # Case 6: out-of-range bounds -> both fall back to defaults
            ev_file.write_text(json.dumps({"lambda": -1.0, "mu": 2.0}))
            config6 = planner.load_metrics_config(tmp_dir)
            self.assertEqual(config6["lambda"], 0.3)
            self.assertEqual(config6["mu"], 0.5)


VALID_PLAN = """# Plan for I-12345

- **Plan ID:** P-test

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

The plan validates the artefact on disk before anything is committed, so a truncated final message cannot lose work.

## Step 1: Add the validator

- **Step Type:** IMPLEMENTATION
- **Implementation:** add a validator module, wire it into the planner entrypoint and cover it with tests that use a
  temporary workspace and a scripted fake agent rather than any real runner process.

| metric         | value |
| -------------- | ----- |
| p_success_pred | 0.9   |
| entropy_pred   | 1.0   |

## Step 2: Document the behaviour

- **Step Type:** DOCUMENTATION
- **Implementation:** describe the incremental write protocol and the stdout fallback warning in the sandbox docs.
"""

PARTIAL_PLAN = VALID_PLAN.replace(
    "### Strategy Rationale\n\nThe plan validates the artefact on disk before anything is committed, "
    "so a truncated final message cannot lose work.\n\n",
    "",
)

INTENT = {
    "branch": "I-12345",
    "intent_id": "I-12345",
    "description": "Test description",
    "goal": "Test goal",
    "entropy_budget": "15.0",
}


class _PlannerHarness:
    """Runs planner.main() against a real temporary workspace with git and the agent replaced by fakes.

    ``agent_turns`` is a list of callables ``(cmd, watch_path) -> TurnResult``; each supervised turn pops the next one.
    """

    def __init__(self, testcase, agent_turns, model="gemini", env=None, build_cmd=None, intent=None, template=None):
        self.testcase = testcase
        self.intent = INTENT if intent is None else intent
        self.template = template or "Template {intent_json} {plan_id} {safe_model}"
        self.build_cmd = build_cmd or (lambda model, pf, inf, prompt: ["pi", "-p", "--model", model, prompt])
        self.agent_turns = list(agent_turns)
        self.model = model
        self.env = env or {}
        self.git_cmds = []
        self.turn_prompts = []
        self.exit_code = None
        self.stdout = ""
        self.stderr = ""

    def _fake_run_cmd(self, args, cwd=None, env=None, check=True):
        self.git_cmds.append(list(args))
        if args[:2] == ["git", "clone"]:
            ledger = pathlib.Path(cwd) / "holon-knowledge" / "ledger"
            ledger.mkdir(parents=True, exist_ok=True)
            (ledger / "intents.jsonl").write_text(json.dumps(self.intent) + "\n")
            prompts = pathlib.Path(cwd) / "holon-config" / "prompts"
            prompts.mkdir(parents=True, exist_ok=True)
            (prompts / "planner.template.md").write_text(self.template)
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    def _fake_run_turn(self, cmd, **kwargs):
        self.turn_prompts.append(cmd[-1])
        self.testcase.assertTrue(self.agent_turns, "planner ran more agent turns than scripted")
        return self.agent_turns.pop(0)(cmd, kwargs["watch_path"])

    def run(self):
        runner = MagicMock()
        runner.get_version.return_value = "1.0.0"
        runner.build_cmd.side_effect = self.build_cmd
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_dir = self.tmp.name
        out, err = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, {"HOLON_REPO_DIR": self.repo_dir, **self.env}),
            patch("sys.argv", ["planner.py", "I-12345/_", "pi-agent", self.model]),
            patch.object(planner, "get_workspace_dir", return_value=self.repo_dir),
            patch.object(planner, "cleanup_repo_dir") as self.mock_cleanup,
            patch.object(planner, "get_runner", return_value=runner),
            patch.object(planner, "run_cmd", side_effect=self._fake_run_cmd),
            patch.object(planner, "run_turn", side_effect=self._fake_run_turn),
            patch.object(planner, "converge_prettier") as self.mock_converge,
            patch("sandbox_executor.flow.check_and_resolve_stage_conflicts"),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            try:
                planner.main()
                self.exit_code = 0
            except SystemExit as exc:
                self.exit_code = exc.code
        self.stdout, self.stderr = out.getvalue(), err.getvalue()
        return self

    def ledger_lines(self, name):
        path = pathlib.Path(self.repo_dir) / "holon-knowledge" / "ledger" / name
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def committed(self):
        return any(cmd[:2] == ["git", "commit"] for cmd in self.git_cmds)

    def cleanup(self):
        self.tmp.cleanup()


def write_file(content, returncode=0, stdout="", breach=None):
    def turn(cmd, watch_path):
        if content is not None:
            with open(watch_path, "w", encoding="utf-8") as f:
                f.write(content)
        return TurnResult(returncode=returncode, stdout=stdout, breach=breach, stop_reason=None)

    return turn


def append_file(content, returncode=0):
    def turn(cmd, watch_path):
        with open(watch_path, "a", encoding="utf-8") as f:
            f.write(content)
        return TurnResult(returncode=returncode)

    return turn


def stdout_only(stdout, returncode=0):
    return lambda cmd, watch_path: TurnResult(returncode=returncode, stdout=stdout)


@patch.dict("os.environ", {"HOLON_AGENT_KEY": "dummy", "HOLON_REPO_URL": "git@github.com:example/repo.git"})
class TestPlannerMain(unittest.TestCase):
    def test_partial_fixture_lacks_only_the_rationale(self):
        self.assertNotEqual(PARTIAL_PLAN, VALID_PLAN)
        self.assertIn("## Step 1", PARTIAL_PLAN)

    def harness(self, turns, **kwargs):
        h = _PlannerHarness(self, turns, **kwargs).run()
        self.addCleanup(h.cleanup)
        return h

    def test_valid_plan_file_is_committed_with_its_metrics(self):
        h = self.harness([write_file(VALID_PLAN)])
        self.assertEqual(h.exit_code, 0, h.stderr)
        h.mock_cleanup.assert_called_once_with(h.repo_dir, raise_on_error=True)
        entries = h.ledger_lines("plans.jsonl")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["p_success"], 0.8)
        self.assertEqual(entries[0]["entropy"], 2.5)
        self.assertTrue(h.committed())
        self.assertTrue(any(cmd[:2] == ["git", "push"] for cmd in h.git_cmds))
        self.assertRegex(h.stdout, r"plan: ok \(\d+\.\d min / 35 min\)")

    def test_plan_row_persists_the_budget_shown_to_the_agent(self):
        intent = {**INTENT, "entropy_budget": 16.0}
        h = self.harness([write_file(VALID_PLAN)], intent=intent, template="Budget is {budget_from_intent}.")
        self.assertEqual(h.exit_code, 0, h.stderr)
        self.assertIn("Budget is 16.0.", h.turn_prompts[0])
        self.assertEqual(h.ledger_lines("plans.jsonl")[0]["entropy_budget"], 16.0)

    def test_missing_intent_budget_uses_the_shared_default(self):
        intent = {k: v for k, v in INTENT.items() if k != "entropy_budget"}
        h = self.harness([write_file(VALID_PLAN)], intent=intent, template="Budget is {budget_from_intent}.")
        self.assertEqual(h.exit_code, 0, h.stderr)
        self.assertIn(f"Budget is {DEFAULT_ENTROPY_BUDGET}.", h.turn_prompts[0])
        self.assertEqual(h.ledger_lines("plans.jsonl")[0]["entropy_budget"], DEFAULT_ENTROPY_BUDGET)

    def test_invalid_intent_budget_shows_and_persists_the_shared_default(self):
        for budget in (None, -1, "lots"):
            with self.subTest(budget=budget):
                intent = {**INTENT, "entropy_budget": budget}
                h = self.harness([write_file(VALID_PLAN)], intent=intent, template="Budget is {budget_from_intent}.")
                self.assertEqual(h.exit_code, 0, h.stderr)
                self.assertIn(f"Budget is {DEFAULT_ENTROPY_BUDGET}.", h.turn_prompts[0])
                self.assertEqual(h.ledger_lines("plans.jsonl")[0]["entropy_budget"], DEFAULT_ENTROPY_BUDGET)

    def test_integer_intent_budget_is_shown_as_the_resolved_float(self):
        intent = {**INTENT, "entropy_budget": 7}
        h = self.harness([write_file(VALID_PLAN)], intent=intent, template="Budget is {budget_from_intent}.")
        self.assertEqual(h.exit_code, 0, h.stderr)
        self.assertIn("Budget is 7.0.", h.turn_prompts[0])
        self.assertEqual(h.ledger_lines("plans.jsonl")[0]["entropy_budget"], 7.0)

    def test_replan_feedback_reaches_the_prompt_but_never_the_row(self):
        feedback = "The previous plan has entropy 12.6, above the entropy budget 5."
        h = self.harness(
            [write_file(VALID_PLAN)],
            intent={**INTENT, "entropy_budget": 5.0},
            env={"HOLON_REPLAN_FEEDBACK": feedback},
        )
        self.assertEqual(h.exit_code, 0, h.stderr)
        self.assertIn("## Re-plan request", h.turn_prompts[0])
        self.assertIn(feedback, h.turn_prompts[0])
        self.assertIn("entropy budget of 5.0", h.turn_prompts[0])
        row = h.ledger_lines("plans.jsonl")[0]
        self.assertNotIn("replan_feedback", row)
        self.assertNotIn(feedback, json.dumps(row))
        self.assertNotIn(feedback, json.dumps(h.ledger_lines("intents.jsonl")))

    def test_replan_feedback_reaches_the_temporary_intent_file(self):
        # R2-I18-M-P2: the agent's intent metadata (the temp intent file) carries the feedback too.
        feedback = "The previous plan has entropy 12.6, above the entropy budget 5."
        h = self.harness([write_file(VALID_PLAN)], env={"HOLON_REPLAN_FEEDBACK": feedback})
        self.assertEqual(h.exit_code, 0, h.stderr)
        intent_metadata = h.turn_prompts[0].split("Intent metadata:\n", 1)[1]
        self.assertIn(json.dumps({"replan_feedback": feedback})[1:-1], intent_metadata)

    def test_whitespace_only_replan_feedback_is_ignored(self):
        # R2-I18-M-P3: blank feedback adds no Re-plan request and no replan_feedback key.
        h = self.harness([write_file(VALID_PLAN)], env={"HOLON_REPLAN_FEEDBACK": " \n\t "})
        self.assertEqual(h.exit_code, 0, h.stderr)
        self.assertNotIn("## Re-plan request", h.turn_prompts[0])
        self.assertNotIn("replan_feedback", h.turn_prompts[0])

    def test_plans_started_in_the_same_second_use_distinct_temp_files(self):
        # R2-I18-N1: the prompt and intent temp paths are unique per run, not keyed by the second, and are removed.
        paths = []

        def build_cmd(model, prompt_file, intent_file, prompt):
            paths.append((prompt_file, intent_file))
            self.assertTrue(os.path.exists(prompt_file) and os.path.exists(intent_file))
            return ["pi", "-p", "--model", model, prompt]

        with patch.object(planner, "time", MagicMock(time=MagicMock(return_value=1792992691.0))):
            first = self.harness([write_file(VALID_PLAN)], build_cmd=build_cmd)
            second = self.harness([write_file(VALID_PLAN)], build_cmd=build_cmd)
        self.assertEqual((first.exit_code, second.exit_code), (0, 0), first.stderr + second.stderr)
        self.assertEqual(len(paths), 2)
        self.assertEqual(len({p for pair in paths for p in pair}), 4)
        for prompt_file, intent_file in paths:
            self.assertIn("plan_prompt-1792992691-", os.path.basename(prompt_file))
            self.assertIn("intent-1792992691-", os.path.basename(intent_file))
            self.assertFalse(os.path.exists(prompt_file) or os.path.exists(intent_file))

    def test_temp_files_are_removed_when_writing_the_prompt_raises(self):
        # N-19-3: both temp files exist before the prompt is written, so a failure while writing it leaks neither.
        for target in ("get_file_structure", "build_replan_paragraph"):
            with self.subTest(target=target):
                created = self._temp_files_left_when_raising(target)
                self.assertEqual(len(created), 2)
                self.assertFalse(any(os.path.exists(path) for path in created), created)

    def test_prompt_temp_file_is_removed_when_creating_the_intent_temp_file_raises(self):
        # N-20-4: the intent temp file is created after the prompt temp file, so mkstemp failing on it (ENOSPC, inode
        # exhaustion) must not leak the prompt temp file.
        original = planner._unique_temp_file
        created = []

        def record(prefix, suffix):
            if created:
                raise OSError(28, "No space left on device")
            created.append(original(prefix, suffix))
            return created[-1]

        h = _PlannerHarness(self, [])
        with (
            patch.object(planner, "_unique_temp_file", side_effect=record),
            self.assertRaisesRegex(OSError, "No space left on device"),
        ):
            h.run()
        h.tmp.cleanup()
        self.assertEqual(len(created), 1)
        self.assertFalse(os.path.exists(created[0]), created)

    def _temp_files_left_when_raising(self, target):
        original = planner._unique_temp_file
        created = []

        def record(prefix, suffix):
            created.append(original(prefix, suffix))
            return created[-1]

        h = _PlannerHarness(self, [], env={"HOLON_REPLAN_FEEDBACK": "Entropy 12.6 is above the budget."})
        with (
            patch.object(planner, "_unique_temp_file", side_effect=record),
            patch.object(planner, target, side_effect=RuntimeError("boom")),
            self.assertRaisesRegex(RuntimeError, "boom"),
        ):
            h.run()
        h.tmp.cleanup()
        return created

    def test_prompt_requires_incremental_file_writes(self):
        h = self.harness([write_file(VALID_PLAN)])
        prompt = h.turn_prompts[0]
        self.assertIn("WRITE PROTOCOL", prompt)
        self.assertIn("plans/P-", prompt)
        self.assertNotIn("Output the complete markdown plan directly", prompt)

    def test_truncated_final_message_after_valid_plan_file_still_commits(self):
        truncated = '{"type":"message_end","stopReason":"length"}'
        turn = write_file(VALID_PLAN, returncode=1, stdout=truncated)

        def truncating_turn(cmd, watch_path):
            result = turn(cmd, watch_path)
            result.stop_reason = "length"
            return result

        h = self.harness([truncating_turn])
        self.assertEqual(h.exit_code, 0, h.stderr)
        self.assertEqual(len(h.ledger_lines("plans.jsonl")), 1)
        self.assertTrue(h.committed())
        self.assertIn("treating the exit status as informational", h.stdout)

    def test_no_file_and_empty_stdout_fails_metrics_validation(self):
        h = self.harness([stdout_only("")])
        self.assertEqual(h.exit_code, 1)
        self.assertIn("metrics validation failed", h.stderr)
        self.assertEqual(h.ledger_lines("plans.jsonl"), [])
        self.assertFalse(h.committed())

    def test_agent_failure_without_output_fails_fast(self):
        h = self.harness([stdout_only("", returncode=1)])
        self.assertEqual(h.exit_code, 1)
        self.assertFalse(h.committed())

    def test_conversational_stdout_fails_validation(self):
        h = self.harness([stdout_only("I will search for files in the repository to locate safety.md and files.")])
        self.assertEqual(h.exit_code, 1)
        self.assertIn("metrics validation failed", h.stderr)
        self.assertEqual(h.ledger_lines("plans.jsonl"), [])

    def test_step_table_before_overall_section_does_not_reach_the_ledger(self):
        # C4-I1 (Bean 0146 / PR #91): the gate and the plans.jsonl row both read `## Overall Plan Metrics`.
        step_first = VALID_PLAN.replace(
            "## Overall Plan Metrics",
            "## Step 0: Survey\n\n| metric | value |\n| --- | --- |\n| p_success_pred | 0.1 |\n"
            "| entropy_pred | 9.0 |\n| impact_pred | 1.0 |\n| cost_pred | 7.0 |\n| learning_value_pred | 0.2 |\n\n"
            "## Overall Plan Metrics",
            1,
        )
        self.assertNotEqual(step_first, VALID_PLAN)
        h = self.harness([write_file(step_first)])
        self.assertEqual(h.exit_code, 0, h.stderr)
        rows = h.ledger_lines("plans.jsonl")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(
            (row["p_success"], row["entropy"], row["impact"], row["cost"], row["learning_value"]),
            (0.8, 2.5, 10.0, 0.5, 0.5),
        )

    def test_missing_overall_metric_is_not_back_filled(self):
        without_cost = VALID_PLAN.replace("| cost_pred           | 0.5   |\n", "")
        self.assertNotEqual(without_cost, VALID_PLAN)
        h = self.harness([write_file(without_cost), append_file("")])
        self.assertEqual(h.exit_code, 1)
        self.assertIn("metrics validation failed", h.stderr)
        self.assertIn("cost", h.stderr.split("metrics validation failed")[1])
        self.assertEqual(h.ledger_lines("plans.jsonl"), [])

    def test_missing_ev_row_is_recomputed_not_failed(self):
        # C3-N3: EV is recomputed from the five inputs, so a plan without an ev_pred row is valid.
        without_ev = VALID_PLAN.replace("| ev_pred             | 6.0   |\n", "")
        self.assertNotEqual(without_ev, VALID_PLAN)
        h = self.harness([write_file(without_ev)])
        self.assertEqual(h.exit_code, 0, h.stderr)
        self.assertEqual(len(h.turn_prompts), 1)
        rows = h.ledger_lines("plans.jsonl")
        self.assertEqual(len(rows), 1)
        self.assertIsInstance(rows[0]["ev"], float)

    def test_partial_plan_triggers_one_targeted_reprompt(self):
        rest = "\n### Strategy Rationale\n\nAppended by the follow-up turn.\n"
        h = self.harness([write_file(PARTIAL_PLAN), append_file(rest)])
        self.assertEqual(h.exit_code, 0, h.stderr)
        self.assertEqual(len(h.turn_prompts), 2)
        reprompt = h.turn_prompts[1].split("Intent metadata:")[-1]
        self.assertIn("Strategy Rationale", reprompt)
        self.assertNotIn("Overall Plan Metrics", reprompt)
        self.assertNotIn("## Step", reprompt)
        self.assertEqual(len(h.ledger_lines("plans.jsonl")), 1)

    def test_stdout_fallback_logs_warning(self):
        h = self.harness([stdout_only(f"Here is the plan:\n```markdown\n{VALID_PLAN}\n```\n")])
        self.assertEqual(h.exit_code, 0, h.stderr)
        self.assertIn("WARNING: agent did not write plans/P-", h.stderr)
        self.assertIn("falling back", h.stderr)
        self.assertEqual(len(h.ledger_lines("plans.jsonl")), 1)

    def test_model_name_is_sanitised_in_branch(self):
        test_cases = [
            ("gemini/3.5-flash (Medium) [Special]*?", "gemini_3.5-flash-Medium-Special"),
            ("gemini..3.5", "gemini.3.5"),
            (".gemini.pro.", "gemini.pro"),
            ("gemini 3.5  flash", "gemini-3.5-flash"),
            ("gemini\t3.5\nflash", "gemini-3.5-flash"),
            ("a" * 70, "a" * 64),
            ("a" * 63 + "-extra", "a" * 63),
            ("*?($", "unknown-model"),
        ]
        for model_name, expected_safe_model in test_cases:
            with self.subTest(model_name=model_name):
                h = self.harness([write_file(VALID_PLAN)], model=model_name)
                checkout_cmds = [" ".join(c) for c in h.git_cmds if c[:3] == ["git", "checkout", "-b"]]
                self.assertTrue(checkout_cmds, f"No 'git checkout -b' command found in: {h.git_cmds}")
                self.assertIn(expected_safe_model, checkout_cmds[0])

    def test_converges_prettier_on_plan_file(self):
        h = self.harness([write_file(VALID_PLAN)])
        h.mock_converge.assert_called_once()
        call_files = h.mock_converge.call_args[0][0]
        self.assertEqual(len(call_files), 1)
        self.assertTrue(call_files[0].startswith("plans/P-") and call_files[0].endswith(".md"))
        self.assertEqual(h.mock_converge.call_args[1].get("repo_dir"), h.repo_dir)

    def test_breach_then_successful_resume_commits_without_budget_record(self):
        stall = BudgetBreach("plan", "stall", 90, 91)
        rest = "\n### Strategy Rationale\n\nWritten by the resume turn.\n"
        h = self.harness([write_file(PARTIAL_PLAN, returncode=-15, breach=stall), append_file(rest)])
        self.assertEqual(h.exit_code, 0, h.stderr)
        self.assertIn("partial", h.turn_prompts[1])
        self.assertEqual(h.ledger_lines("executions.jsonl"), [])
        self.assertEqual(len(h.ledger_lines("plans.jsonl")), 1)

    def test_breach_and_failed_resume_records_one_budget_action_and_salvages(self):
        turn_breach = BudgetBreach("plan", "turn", 300, 300)
        partial = "# Plan for I-12345\n\n## Step 1: Partial\n\nCut off before the metrics table.\n"
        h = self.harness(
            [
                write_file(partial, returncode=-15, breach=turn_breach),
                write_file(None, returncode=-15, breach=turn_breach),
            ]
        )
        self.assertEqual(h.exit_code, 1)
        self.assertEqual(h.ledger_lines("plans.jsonl"), [])
        records = h.ledger_lines("executions.jsonl")
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertTrue(record["action_id"].startswith("ACT-BUDGET-"))
        self.assertEqual(record["entry_type"], "action")
        self.assertEqual(record["outcome"], "failure")
        self.assertEqual(record["stage"], "plan")
        self.assertEqual(record["budget_name"], "turn")
        self.assertEqual(record["budget_seconds"], 300)
        self.assertEqual(record["elapsed_seconds"], 300)
        # The salvaged partial plan and the ledger record are committed together so neither is lost with the sandbox.
        add_cmds = [c for c in h.git_cmds if c[:2] == ["git", "add"]]
        self.assertTrue(any("holon-knowledge/ledger/executions.jsonl" in c for c in add_cmds))
        self.assertTrue(any(any(arg.startswith("plans/P-") for arg in c) for c in add_cmds))
        self.assertTrue(h.committed())
        self.assertIn("plan: failed", h.stdout)
        self.assertIn("resume did not recover", record["action"])

    def test_breach_without_stage_time_for_resume_names_no_resume(self):
        stage_breach = BudgetBreach("plan", "stage", 2100, 2100)
        partial = "# Plan for I-12345\n\n## Step 1: Partial\n\nCut off before the metrics table.\n"
        h = self.harness(
            [write_file(partial, returncode=-15, breach=stage_breach)],
            env={"HOLON_STAGE_BUDGET_SECONDS": "0.000001"},
        )
        self.assertEqual(h.exit_code, 1)
        records = h.ledger_lines("executions.jsonl")
        self.assertEqual(len(records), 1)
        self.assertIn("no stage time was left to resume", records[0]["action"])
        self.assertNotIn("resume did not recover", h.stderr)


# --------------------------------------------------------------------------------------
# Credential leaks in the echoed clone command and its failure diagnostics
# --------------------------------------------------------------------------------------


@pytest.fixture
def clone_leak_env(monkeypatch):
    sample = make_gh_probe()
    for name in ("GH_TOKEN", "HOLON_AGENT_KEY", "HOLON_MITM_WEB_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", sample)
    url = f"https://x-access-token:{sample}@github.com/myorg/myrepo.git"
    argv = ["git", "clone", "--branch", "I-1792762862-x/_", "--single-branch", "--depth", "1", url, "."]
    failed = MagicMock(
        returncode=128,
        stdout=f"Cloning into '.' from {url}",
        stderr=f"fatal: unable to access '{url}/': The requested URL returned error: 403",
    )
    return sample, argv, failed


def test_run_cmd_clone_echo_and_failure_carry_no_token_fragment(clone_leak_env, capsys):
    sample, argv, failed = clone_leak_env
    with patch("subprocess.run", return_value=failed), pytest.raises(SystemExit):
        planner.run_cmd(argv, check=True)
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert_no_fragment(output, sample)
    assert "Running: git clone --branch I-1792762862-x/_ --single-branch" in output
    assert "github.com/myorg/myrepo.git" in output
    assert "returned error: 403" in output


# --------------------------------------------------------------------------------------
# Credential leaks in the echoed agent command
# --------------------------------------------------------------------------------------


def _fake_provider_value() -> str:
    # Assembled at runtime so no provider-shaped literal is committed.
    return "sk" + "-ant-" + "oat01-" + uuid.uuid4().hex + uuid.uuid4().hex


def test_agent_cmd_echo_masks_settings_json_before_truncation():
    value = _fake_provider_value()
    settings_blob = json.dumps({"env": {"CLAUDE_CODE_OAUTH_TOKEN": value}})
    argv = ["claude", "--model", "claude-opus-5-5", "--settings", settings_blob, "-p", "x" * 500]
    shown = planner.format_agent_cmd_for_display(argv)
    assert_no_fragment(shown, value)
    assert shown.startswith("claude --model claude-opus-5-5 --settings ")
    assert "CLAUDE_CODE_OAUTH_TOKEN" in shown
    assert shown.endswith("x" * 100 + "...")


def test_agent_cmd_echo_masks_bare_provider_value_in_long_arg():
    value = _fake_provider_value()
    # The value straddles the 100-character clamp, the case a truncate-then-redact order leaks.
    argv = ["pi", "-p", "a" * 90 + value]
    shown = planner.format_agent_cmd_for_display(argv)
    assert_no_fragment(shown, value)
    assert shown.startswith("pi -p " + "a" * 90)


@pytest.mark.parametrize("agent_fails", [False, True], ids=["agent_ok", "agent_fails_echoing_value"])
def test_planner_main_agent_echo_carries_no_settings_value(monkeypatch, agent_fails):
    monkeypatch.setenv("HOLON_REPO_URL", "https://github.com/myorg/myrepo.git")
    value = _fake_provider_value()
    settings_blob = json.dumps({"env": {"CLAUDE_CODE_OAUTH_TOKEN": value}})
    # A failing agent writes no plan file and no stdout, which routes main() to the stdout/stderr dump.
    turn = (
        (lambda cmd, watch_path: TurnResult(returncode=1, stdout="", stderr=f"auth rejected for {value}"))
        if agent_fails
        else write_file(VALID_PLAN)
    )
    h = _PlannerHarness(
        unittest.TestCase(),
        [turn],
        model="claude-opus-5-5",
        build_cmd=lambda model, pf, inf, prompt: ["claude", "--settings", settings_blob, "-p", prompt],
    ).run()
    try:
        combined = h.stdout + h.stderr
        assert "Running command: claude --settings " in combined
        if agent_fails:
            assert "Error: agent command failed or returned empty. code=1" in combined
        assert_no_fragment(combined, value)
    finally:
        h.cleanup()
