import math
import unittest

import pytest
from sandbox_executor.budgets import (
    DEFAULT_ENTROPY_BUDGET,
    ENV_STAGE_BUDGET_MINUTES,
    ENV_STAGE_BUDGET_SECONDS,
    ENV_TURN_STALL,
    ENV_TURN_TIMEOUT,
    MAX_BUDGET_SECONDS,
    BudgetBreach,
    StageBudgets,
    StageTimer,
    active_stage_timer,
    current_stage_timer,
    format_minutes,
    format_stage_summary,
    load_budgets,
    resolve_entropy_budget,
    stage_budget_handdown_env,
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

    def test_values_above_the_bound_keep_defaults_after_conversion(self):
        # C21-N3 (R1-N1/R2-N5): 1e308 minutes is finite but overflows to inf seconds after * 60; any value above
        # MAX_BUDGET_SECONDS once converted is ignored with a variable-named warning, so no inf reaches arithmetic.
        plan = ENV_STAGE_BUDGET_MINUTES["plan"]
        too_many_minutes = str(MAX_BUDGET_SECONDS / 60 + 1)
        for env in (
            {plan: "1e308"},
            {plan: too_many_minutes},
            {ENV_TURN_TIMEOUT: "1e308"},
            {ENV_STAGE_BUDGET_SECONDS: str(MAX_BUDGET_SECONDS + 1)},
        ):
            with self.subTest(env=env), self.assertLogs("sandbox_executor.budgets", level="WARNING") as logs:
                budgets = load_budgets(env)
            self.assertIn(next(iter(env)), logs.output[0])
            self.assertNotIn(next(iter(env.values())), logs.output[0])
            self.assertEqual(budgets.stage_seconds("plan"), 2100)
            self.assertEqual(budgets.turn.turn_seconds, 300)
            timer = StageTimer("plan", budgets.stage_seconds("plan"), clock=FakeClock())
            self.assertEqual(stage_budget_handdown_env(timer), {ENV_STAGE_BUDGET_SECONDS: "2040"})
        self.assertEqual(load_budgets({plan: str(MAX_BUDGET_SECONDS / 60)}).stage_seconds("plan"), MAX_BUDGET_SECONDS)

    def test_invalid_value_logs_a_warning(self):
        with self.assertLogs("sandbox_executor.budgets", level="WARNING") as logs:
            load_budgets({ENV_TURN_TIMEOUT: "soon"})
        self.assertIn(ENV_TURN_TIMEOUT, logs.output[0])

    def test_invalid_stage_override_warning_names_the_real_fallback(self):
        # C4-N2: an invalid HOLON_STAGE_BUDGET_SECONDS leaves the per-stage budgets in force; no fake -1.0 default.
        for raw in ("abc", "0", "-3", "inf"):
            with self.subTest(raw=raw), self.assertLogs("sandbox_executor.budgets", level="WARNING") as logs:
                budgets = load_budgets({ENV_STAGE_BUDGET_SECONDS: raw})
            self.assertIsNone(budgets.stage_override_seconds)
            self.assertEqual(budgets.stage_seconds("execute"), 2700)
            self.assertNotIn("-1.0", logs.output[0])
            self.assertIn("per-stage budgets apply", logs.output[0])

    def test_zero_stall_disables_stall_detection(self):
        self.assertEqual(load_budgets({ENV_TURN_STALL: "0"}).turn.stall_seconds, 0)

    def test_unknown_stage(self):
        with self.assertRaises(KeyError):
            StageBudgets().for_stage("deploy")

    def test_summary_format_matches_the_bean_example(self):
        self.assertEqual(format_stage_summary("plan", "ok", 246, 2100), "plan: ok (4.1 min / 35 min)")
        self.assertEqual(format_stage_summary("review", "failed", 0, 450), "review: failed (0.0 min / 7.5 min)")

    def test_format_minutes_never_rounds_a_sub_minute_budget_to_zero(self):
        # C7-N2: a 20 s ceiling must not print as "0 min".
        self.assertEqual(format_minutes(2100), "35")
        self.assertEqual(format_minutes(450), "7.5")
        self.assertEqual(format_minutes(20), "0.3")

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


class TestStageBudgetHanddown(unittest.TestCase):
    """C6-I1: the host hands the container the remaining stage time as HOLON_STAGE_BUDGET_SECONDS."""

    def test_no_active_timer_hands_down_nothing(self):
        self.assertIsNone(current_stage_timer())
        self.assertEqual(stage_budget_handdown_env(), {})

    def test_non_finite_timer_hands_down_nothing(self):
        # A timer built in code with an infinite budget must not raise OverflowError from floor(inf) (C21-N3).
        with self.assertLogs("sandbox_executor.budgets", level="WARNING"):
            self.assertEqual(stage_budget_handdown_env(StageTimer("plan", float("inf"), clock=FakeClock())), {})

    def test_active_timer_hands_down_remaining_minus_margin_floored(self):
        clock = FakeClock()
        timer = StageTimer("execute", 2700, clock=clock)
        with active_stage_timer(timer):
            self.assertIs(current_stage_timer(), timer)
            clock.now += 600.4
            self.assertEqual(stage_budget_handdown_env(), {ENV_STAGE_BUDGET_SECONDS: "2039"})
            inner = StageTimer("plan", 100, clock=clock)
            with active_stage_timer(inner):
                # Short budgets keep at most a tenth of themselves back.
                self.assertEqual(stage_budget_handdown_env(), {ENV_STAGE_BUDGET_SECONDS: "90"})
            self.assertIs(current_stage_timer(), timer)
            clock.now += 5000
            # Never 0, which the container would ignore in favour of the full per-stage budget.
            self.assertEqual(stage_budget_handdown_env(), {ENV_STAGE_BUDGET_SECONDS: "1"})
        self.assertIsNone(current_stage_timer())

    def test_handed_down_value_is_the_container_ceiling(self):
        clock = FakeClock()
        timer = StageTimer("plan", 2100, clock=clock)
        clock.now += 100
        env = stage_budget_handdown_env(timer)
        self.assertEqual(load_budgets(env).stage_seconds("plan"), 1940)


# --- Entropy budget resolution --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("plan_row", "intent_row", "expected"),
    [
        ({"entropy_budget": 16.0}, {"entropy_budget": 5.0}, 16.0),
        ({}, {"entropy_budget": 16}, 16.0),
        ({}, {"entropy_budget": "7.5"}, 7.5),
        (None, None, DEFAULT_ENTROPY_BUDGET),
        ({}, {}, DEFAULT_ENTROPY_BUDGET),
        ({"entropy_budget": 0}, {"entropy_budget": 9.0}, 0.0),
    ],
)
def test_resolve_entropy_budget_order(plan_row, intent_row, expected):
    assert resolve_entropy_budget(plan_row, intent_row) == expected


@pytest.mark.parametrize("bad", [None, "lots", math.nan, math.inf, -1.0, True, [], {}])
def test_resolve_entropy_budget_invalid_values_fall_through(bad):
    assert resolve_entropy_budget({"entropy_budget": bad}, {"entropy_budget": 12.0}) == 12.0
    assert resolve_entropy_budget({"entropy_budget": bad}, {"entropy_budget": bad}) == DEFAULT_ENTROPY_BUDGET


def test_default_entropy_budget_is_the_single_shared_constant():
    from sandbox_executor.entrypoint import executor, planner

    assert DEFAULT_ENTROPY_BUDGET == 15.0
    assert executor.DEFAULT_ENTROPY_BUDGET is DEFAULT_ENTROPY_BUDGET
    assert planner.resolve_entropy_budget is resolve_entropy_budget
    assert executor.resolve_entropy_budget is resolve_entropy_budget
