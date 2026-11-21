import pytest
from sandbox_executor.budget_limits import (
    UNLIMITED,
    format_budget_minutes,
    is_unlimited,
    unbounded_supervision_warning,
    unlimited_stage_warning,
)


@pytest.mark.parametrize("raw", ["unlimited", "UNLIMITED", " Unlimited\t", "\nunLimited "])
def test_is_unlimited_accepts_the_sentinel_in_any_case_and_padding(raw):
    assert is_unlimited(raw)


@pytest.mark.parametrize("raw", ["unlimit", "unlimited!", "un limited", "none", "inf", "nan", "", "0", "-1", None])
def test_is_unlimited_rejects_everything_else(raw):
    assert not is_unlimited(raw)


def test_format_budget_minutes():
    assert format_budget_minutes(None) == UNLIMITED
    assert format_budget_minutes(2700) == "45min"
    assert format_budget_minutes(90) == "1.5min"


def test_unlimited_stage_warning_names_the_stage():
    message = unlimited_stage_warning("execute")
    assert "'execute'" in message
    assert "no wall-clock ceiling" in message


@pytest.mark.parametrize(
    ("turn_seconds", "stall_seconds", "stages", "warns"),
    [
        (None, 0, ["execute"], True),
        (None, 0, [], False),
        (None, 90, ["execute"], False),
        (3600, 0, ["execute"], False),
        (3600, 90, ["execute"], False),
    ],
)
def test_unbounded_supervision_warning_only_when_every_limit_is_off(turn_seconds, stall_seconds, stages, warns):
    message = unbounded_supervision_warning(turn_seconds, stall_seconds, stages)
    assert (message is not None) is warns
    if warns:
        assert message is not None
        for name in ("HOLON_TURN_TIMEOUT_SECONDS", "HOLON_TURN_STALL_SECONDS", "execute", "hung agent"):
            assert name in message
