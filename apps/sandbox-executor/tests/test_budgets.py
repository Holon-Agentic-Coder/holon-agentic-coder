import unittest

from sandbox_executor.budgets import (
    ENV_STAGE_BUDGET_MINUTES,
    ENV_STAGE_BUDGET_SECONDS,
    ENV_TURN_STALL,
    ENV_TURN_TIMEOUT,
    BudgetBreach,
    StageBudgets,
    StageTimer,
    format_stage_summary,
    load_budgets,
)


class FakeClock:
    def __init__(self, start=100.0):
        self.now = start

    def __call__(self):
        return self.now


class TestBudgets(unittest.TestCase):
    def test_defaults(self):
        budgets = load_budgets({})
        self.assertEqual(budgets.turn.turn_seconds, 300)
        self.assertEqual(budgets.turn.stall_seconds, 90)
        self.assertEqual(budgets.stage_seconds("intent"), 600)
        self.assertEqual(budgets.stage_seconds("plan"), 2100)
        self.assertEqual(budgets.stage_seconds("execute"), 2700)
        self.assertEqual(budgets.stage_seconds("review"), 900)
        self.assertEqual(budgets.stage_seconds("calibrate"), 600)

    def test_env_overrides(self):
        env = {
            ENV_TURN_TIMEOUT: "120",
            ENV_TURN_STALL: "30",
            ENV_STAGE_BUDGET_MINUTES["plan"]: "20",
            ENV_STAGE_BUDGET_MINUTES["review_iteration"]: "7.5",
        }
        budgets = load_budgets(env)
        self.assertEqual(budgets.turn.turn_seconds, 120)
        self.assertEqual(budgets.turn.stall_seconds, 30)
        self.assertEqual(budgets.stage_seconds("plan"), 1200)
        self.assertEqual(budgets.stage_seconds("review_iteration"), 450)
        self.assertEqual(budgets.stage_seconds("execute"), 2700)

    def test_stage_seconds_override_wins(self):
        budgets = load_budgets({ENV_STAGE_BUDGET_SECONDS: "45", ENV_STAGE_BUDGET_MINUTES["plan"]: "20"})
        self.assertEqual(budgets.stage_seconds("plan"), 45)

    def test_invalid_values_keep_defaults(self):
        for raw in ("abc", "-5", "0", "nan", "inf", ""):
            with self.subTest(raw=raw), self.assertNoLogs("sandbox_executor.budgets", level="ERROR"):
                budgets = load_budgets({ENV_TURN_TIMEOUT: raw, ENV_STAGE_BUDGET_MINUTES["execute"]: raw})
                self.assertEqual(budgets.turn.turn_seconds, 300)
                self.assertEqual(budgets.stage_seconds("execute"), 2700)

    def test_invalid_value_logs_a_warning(self):
        with self.assertLogs("sandbox_executor.budgets", level="WARNING") as logs:
            load_budgets({ENV_TURN_TIMEOUT: "soon"})
        self.assertIn(ENV_TURN_TIMEOUT, logs.output[0])

    def test_zero_stall_disables_stall_detection(self):
        self.assertEqual(load_budgets({ENV_TURN_STALL: "0"}).turn.stall_seconds, 0)

    def test_unknown_stage(self):
        with self.assertRaises(KeyError):
            StageBudgets().for_stage("deploy")

    def test_summary_format_matches_the_bean_example(self):
        self.assertEqual(format_stage_summary("plan", "ok", 246, 2100), "plan: ok (4.1 min / 35 min)")
        self.assertEqual(format_stage_summary("review", "failed", 0, 450), "review: failed (0.0 min / 7.5 min)")

    def test_stage_timer_arithmetic(self):
        clock = FakeClock()
        timer = StageTimer("execute", 60, clock=clock)
        clock.now += 15
        self.assertEqual(timer.elapsed(), 15)
        self.assertEqual(timer.remaining(), 45)
        self.assertFalse(timer.breached())
        clock.now += 50
        self.assertTrue(timer.breached())
        self.assertEqual(timer.remaining(), 0)
        breach = timer.breach()
        self.assertIsInstance(breach, BudgetBreach)
        self.assertEqual((breach.stage, breach.budget_name, breach.budget_seconds), ("execute", "stage", 60))
        self.assertEqual(timer.stop(), 65)
        clock.now += 100
        self.assertEqual(timer.elapsed(), 65)
        self.assertEqual(timer.summary("failed"), "execute: failed (1.1 min / 1 min)")
