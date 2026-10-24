import io
import json
import os
import pathlib
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import MagicMock, patch

from sandbox_executor.budgets import BudgetBreach
from sandbox_executor.entrypoint import planner
from sandbox_executor.turns import TurnResult


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

    def __init__(self, testcase, agent_turns, model="gemini", env=None):
        self.testcase = testcase
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
            (ledger / "intents.jsonl").write_text(json.dumps(INTENT) + "\n")
            prompts = pathlib.Path(cwd) / "holon-config" / "prompts"
            prompts.mkdir(parents=True, exist_ok=True)
            (prompts / "planner.template.md").write_text("Template {intent_json} {plan_id} {safe_model}")
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
        runner.build_cmd.side_effect = lambda model, pf, inf, prompt: ["pi", "-p", "--model", model, prompt]
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
