"""Tests for sandbox_executor.module_budget_guard, the flow's policy for the frozen module budget configuration."""

from __future__ import annotations

import os

import pytest
from sandbox_executor.module_budget_guard import (
    ALLOW_MODULE_BUDGET_CHANGE_ENV,
    MODULE_BUDGET_CONFIG,
    budget_config_changes,
    module_budget_change_authorised,
    module_budget_flag,
    refusal_message,
    with_module_budget_flag,
    without_budget_config,
)
from sandbox_executor.touch_set import TouchSet, TouchSetEntry

AUTHORISED = {"module_budget_change": "Maintainer, 2026-11-20: raise pipeline.py to 440 lines."}


@pytest.mark.parametrize(
    ("intent_data", "expected"),
    [
        (None, False),
        ({}, False),
        ({"module_budget_change": ""}, False),
        ({"module_budget_change": "  \n"}, False),
        ({"module_budget_change": True}, False),
        (AUTHORISED, True),
    ],
    ids=["no-intent", "no-field", "empty", "whitespace", "not-a-string", "quoted-instruction"],
)
def test_only_a_quoted_instruction_authorises(intent_data, expected):
    assert module_budget_change_authorised(intent_data) is expected


def test_the_flag_is_set_only_when_authorised_and_an_inherited_one_is_removed():
    assert with_module_budget_flag({}, AUTHORISED) == {ALLOW_MODULE_BUDGET_CHANGE_ENV: "1"}
    assert with_module_budget_flag({ALLOW_MODULE_BUDGET_CHANGE_ENV: "1", "OTHER": "x"}, {}) == {"OTHER": "x"}


@pytest.mark.parametrize("previous", [None, "1"])
def test_the_dispatch_flag_is_scoped_and_restored(monkeypatch: pytest.MonkeyPatch, previous: str | None):
    if previous is None:
        monkeypatch.delenv(ALLOW_MODULE_BUDGET_CHANGE_ENV, raising=False)
    else:
        monkeypatch.setenv(ALLOW_MODULE_BUDGET_CHANGE_ENV, previous)
    with module_budget_flag({}):
        assert ALLOW_MODULE_BUDGET_CHANGE_ENV not in os.environ
    with module_budget_flag(AUTHORISED):
        assert os.environ[ALLOW_MODULE_BUDGET_CHANGE_ENV] == "1"
    assert os.environ.get(ALLOW_MODULE_BUDGET_CHANGE_ENV) == previous


def test_budget_config_changes_matches_the_normalised_config_path_only():
    changed = ["scripts/check_module_budgets.py", f"./{MODULE_BUDGET_CONFIG}", "holon-config/hygiene/other.json"]
    assert budget_config_changes(changed) == [f"./{MODULE_BUDGET_CONFIG}"]
    assert budget_config_changes(["lib/a.py"]) == []


def test_without_budget_config_drops_only_the_exact_entry():
    touch_set = TouchSet(
        (TouchSetEntry("lib/a.py"), TouchSetEntry(MODULE_BUDGET_CONFIG), TouchSetEntry("holon-config/"))
    )
    kept, removed = without_budget_config(touch_set)
    assert [entry.path for entry in kept.entries] == ["lib/a.py", "holon-config/"]
    assert removed == [MODULE_BUDGET_CONFIG]
    assert without_budget_config(TouchSet((TouchSetEntry("lib/a.py"),))) == (TouchSet((TouchSetEntry("lib/a.py"),)), [])


def test_the_refusal_says_split_and_names_the_field():
    message = refusal_message()
    assert MODULE_BUDGET_CONFIG in message
    assert "split the module" in message
    assert "module_budget_change" in message
