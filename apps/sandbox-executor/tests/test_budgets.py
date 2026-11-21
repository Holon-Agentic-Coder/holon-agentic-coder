import math
import unittest

import pytest
from sandbox_executor.budgets import (
    DEFAULT_ENTROPY_BUDGET,
    DEFAULT_TURN_SECONDS,
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
        self.assertEqual(DEFAULT_TURN_SECONDS, 3600.0)
        self.assertEqual(budgets.turn.turn_seconds, 3600)
        self.assertEqual(budgets.turn.stall_seconds, 90)
        self.assertEqual(budgets.stage_seconds("intent"), 600)
        self.assertEqual(budgets.stage_seconds("plan"), 2100)
        self.assertEqual(budgets.stage_seconds("execute"), 2700)
        self.assertEqual(budgets.stage_seconds("review"), 7200)
        self.assertEqual(budgets.stage_seconds("review_iteration"), 7200)
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
                self.assertEqual(budgets.turn.turn_seconds, 3600)
                self.assertEqual(budgets.stage_seconds("execute"), 2700)

    def test_values_above_the_bound_keep_defaults_after_conversion(self):
        # 1e308 minutes is finite but overflows to inf seconds after * 60; any value above
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
            self.assertEqual(budgets.turn.turn_seconds, 3600)
            timer = StageTimer("plan", budgets.stage_seconds("plan"), clock=FakeClock())
            self.assertEqual(stage_budget_handdown_env(timer), {ENV_STAGE_BUDGET_SECONDS: "2040"})
        self.assertEqual(load_budgets({plan: str(MAX_BUDGET_SECONDS / 60)}).stage_seconds("plan"), MAX_BUDGET_SECONDS)

    def test_invalid_value_logs_a_warning(self):
        with self.assertLogs("sandbox_executor.budgets", level="WARNING") as logs:
            load_budgets({ENV_TURN_TIMEOUT: "soon"})
        self.assertIn(ENV_TURN_TIMEOUT, logs.output[0])

    def test_invalid_stage_override_warning_names_the_real_fallback(self):
        # An invalid HOLON_STAGE_BUDGET_SECONDS leaves the per-stage budgets in force; no fake -1.0 default.
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

    def test_summary_format_matches_the_documented_example(self):
        self.assertEqual(format_stage_summary("plan", "ok", 246, 2100), "plan: ok (4.1 min / 35 min)")
        self.assertEqual(format_stage_summary("review", "failed", 0, 450), "review: failed (0.0 min / 7.5 min)")

    def test_format_minutes_never_rounds_a_sub_minute_budget_to_zero(self):
        # A 20 s ceiling must not print as "0 min".
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
    """The host hands the container the remaining stage time as HOLON_STAGE_BUDGET_SECONDS."""

    def test_no_active_timer_hands_down_nothing(self):
        self.assertIsNone(current_stage_timer())
        self.assertEqual(stage_budget_handdown_env(), {})

    def test_non_finite_timer_hands_down_nothing(self):
        # A timer built in code with an infinite budget must not raise OverflowError from floor(inf).
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
    # The decomposition rule (and so its budget inputs) moved from entrypoint.executor to decomposition_rule.
    from sandbox_executor import decomposition_rule
    from sandbox_executor.entrypoint import planner

    assert DEFAULT_ENTROPY_BUDGET == 15.0
    assert decomposition_rule.DEFAULT_ENTROPY_BUDGET is DEFAULT_ENTROPY_BUDGET
    assert planner.resolve_entropy_budget is resolve_entropy_budget
    assert decomposition_rule.resolve_entropy_budget is resolve_entropy_budget


def test_replan_and_depth_bounds_are_pinned_to_the_documented_values():
    # docs/budgets.md and docs/examples.md state these values; a change must update the docs with them.
    from sandbox_executor.budgets import ENV_REPLAN_FEEDBACK, MAX_REPLAN_ATTEMPTS, MAX_SUB_INTENT_DEPTH

    assert MAX_REPLAN_ATTEMPTS == 2
    assert MAX_SUB_INTENT_DEPTH == 2
    # HOLON_* variables are forwarded into role containers, so the feedback variable must keep that prefix.
    assert ENV_REPLAN_FEEDBACK == "HOLON_REPLAN_FEEDBACK"


# --- The unlimited sentinel -----------------------------------------------------------------------------------------

_SENTINELS = ("unlimited", "UNLIMITED", " UnLimited ", "\tunlimited\n")


@pytest.mark.parametrize("raw", _SENTINELS)
@pytest.mark.parametrize("stage", sorted(ENV_STAGE_BUDGET_MINUTES))
def test_each_stage_variable_accepts_the_sentinel(stage, raw, caplog):
    with caplog.at_level("WARNING", logger="sandbox_executor.budgets"):
        budgets = load_budgets({ENV_STAGE_BUDGET_MINUTES[stage]: raw})
    assert budgets.stage_seconds(stage) is None
    assert budgets.stages.for_stage(stage) is None
    assert budgets.unlimited_stages() == [stage]
    # Every other stage keeps its default.
    others = [name for name in ENV_STAGE_BUDGET_MINUTES if name != stage]
    assert all(budgets.stage_seconds(name) == StageBudgets().for_stage(name) for name in others)
    assert [r.levelname for r in caplog.records] == ["WARNING"]
    assert ENV_STAGE_BUDGET_MINUTES[stage] in caplog.records[0].getMessage()


@pytest.mark.parametrize("raw", _SENTINELS)
def test_the_stage_override_sentinel_makes_every_stage_unlimited(raw):
    budgets = load_budgets({ENV_STAGE_BUDGET_SECONDS: raw, ENV_STAGE_BUDGET_MINUTES["execute"]: "30"})
    assert budgets.stage_override_unlimited
    assert budgets.stage_override_seconds is None
    assert all(budgets.stage_seconds(name) is None for name in ENV_STAGE_BUDGET_MINUTES)
    assert budgets.stage_seconds("review") is None


@pytest.mark.parametrize("raw", _SENTINELS)
def test_the_turn_timeout_accepts_the_sentinel(raw):
    budgets = load_budgets({ENV_TURN_TIMEOUT: raw})
    assert budgets.turn.turn_seconds is None
    assert budgets.turn.stall_seconds == 90
    assert budgets.stage_seconds("execute") == 2700


def test_unset_and_blank_still_mean_the_default():
    for env in ({}, {ENV_STAGE_BUDGET_MINUTES["execute"]: "  ", ENV_TURN_TIMEOUT: ""}):
        budgets = load_budgets(env)
        assert budgets.stage_seconds("execute") == 2700
        assert budgets.turn.turn_seconds == DEFAULT_TURN_SECONDS
        assert not budgets.stage_override_unlimited


@pytest.mark.parametrize("raw", ["inf", "-inf", "nan", "Infinity", "unlimit", "none"])
def test_infinite_and_near_miss_values_still_keep_the_default(raw, caplog):
    env = {ENV_STAGE_BUDGET_MINUTES["execute"]: raw, ENV_TURN_TIMEOUT: raw, ENV_STAGE_BUDGET_SECONDS: raw}
    with caplog.at_level("WARNING", logger="sandbox_executor.budgets"):
        budgets = load_budgets(env)
    assert budgets.stage_seconds("execute") == 2700
    assert budgets.turn.turn_seconds == DEFAULT_TURN_SECONDS
    assert not budgets.stage_override_unlimited
    assert len(caplog.records) == 3
    assert all(raw not in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("variable", [ENV_TURN_STALL, "HOLON_REVIEW_CI_WAIT_SECONDS"])
def test_stall_and_ci_wait_reject_the_sentinel(variable, caplog):
    from sandbox_executor.budgets import resolve_review_ci_wait_seconds

    with caplog.at_level("WARNING", logger="sandbox_executor.budgets"):
        budgets = load_budgets({variable: "unlimited"})
        ci_wait = resolve_review_ci_wait_seconds({variable: "unlimited"})
    assert budgets.turn.stall_seconds == 90
    assert ci_wait == 1800
    assert any("not a number" in record.getMessage() for record in caplog.records)


def test_an_unlimited_stage_timer_never_breaches():
    clock = FakeClock()
    timer = StageTimer("execute", None, clock=clock)
    clock.now += MAX_BUDGET_SECONDS + 1
    assert timer.unlimited
    assert not timer.breached()
    assert timer.remaining() is None
    assert timer.has_time_left()
    assert timer.remaining_capped(30) == 30


def test_a_finite_stage_timer_caps_and_runs_out():
    clock = FakeClock()
    timer = StageTimer("execute", 60, clock=clock)
    clock.now += 15
    assert timer.remaining_capped(30) == 30
    clock.now += 30
    assert timer.remaining_capped(30) == 15
    assert timer.has_time_left()
    clock.now += 20
    assert not timer.has_time_left()
    assert timer.remaining_capped(30) == 0


def test_the_summary_line_shows_unlimited():
    assert format_stage_summary("execute", "ok", 73.2 * 60, None) == "execute: ok (73.2 min / unlimited)"
    clock = FakeClock()
    timer = StageTimer("execute", None, clock=clock)
    clock.now += 73.2 * 60
    assert timer.summary("ok") == "execute: ok (73.2 min / unlimited)"


def test_an_unlimited_timer_hands_down_the_sentinel_and_the_container_honours_it():
    clock = FakeClock()
    timer = StageTimer("execute", None, clock=clock)
    clock.now += 10 * 24 * 3600
    with active_stage_timer(timer):
        env = stage_budget_handdown_env()
    assert env == {ENV_STAGE_BUDGET_SECONDS: "unlimited"}
    container = load_budgets(env)
    assert all(container.stage_seconds(name) is None for name in ENV_STAGE_BUDGET_MINUTES)


def _error_records(caplog):
    return [record for record in caplog.records if record.levelname == "ERROR"]


def test_every_limit_disabled_logs_one_error(caplog):
    env = {ENV_TURN_TIMEOUT: "unlimited", ENV_TURN_STALL: "0", ENV_STAGE_BUDGET_MINUTES["execute"]: "unlimited"}
    with caplog.at_level("WARNING", logger="sandbox_executor.budgets"):
        load_budgets(env)
    errors = _error_records(caplog)
    assert len(errors) == 1
    assert "execute" in errors[0].getMessage()
    assert "hung agent" in errors[0].getMessage()


@pytest.mark.parametrize(
    "env",
    [
        {ENV_TURN_TIMEOUT: "unlimited", ENV_TURN_STALL: "90", ENV_STAGE_BUDGET_MINUTES["execute"]: "unlimited"},
        {ENV_TURN_TIMEOUT: "3600", ENV_TURN_STALL: "0", ENV_STAGE_BUDGET_MINUTES["execute"]: "unlimited"},
        {ENV_TURN_TIMEOUT: "unlimited", ENV_TURN_STALL: "0"},
    ],
)
def test_a_remaining_limit_logs_no_error_but_still_warns(env, caplog):
    with caplog.at_level("WARNING", logger="sandbox_executor.budgets"):
        load_budgets(env)
    assert _error_records(caplog) == []
    assert any(record.levelname == "WARNING" for record in caplog.records)
