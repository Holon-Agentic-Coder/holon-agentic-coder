"""Tests for the review accounting record: counting, de-duplication, the checkpoint round trip and rendering."""

from __future__ import annotations

import re

import pytest
from sandbox_executor.flow import FlowContext
from sandbox_executor.review_accounting import (
    CHURN_REASONS,
    COUNTER_KEYS,
    SEEN_EVENTS_LIMIT,
    accounting_of,
    count_accounting,
    render_accounting_lines,
)


def test_counters_start_at_zero():
    context = FlowContext()
    count_accounting(context, "full_reviews")
    accounting = accounting_of(context)
    assert accounting is not None
    assert {key: accounting[key] for key in COUNTER_KEYS if key != "full_reviews"} == dict.fromkeys(
        (key for key in COUNTER_KEYS if key != "full_reviews"), 0
    )
    assert accounting["non_defect_churn"] == dict.fromkeys(CHURN_REASONS, 0)
    assert "seen_events" not in accounting


@pytest.mark.parametrize("key", COUNTER_KEYS)
def test_count_increments_each_counter(key):
    context = FlowContext()
    assert count_accounting(context, key) and count_accounting(context, key)
    assert accounting_of(context)[key] == 2


@pytest.mark.parametrize("reason", CHURN_REASONS)
def test_count_increments_each_churn_reason(reason):
    context = FlowContext()
    count_accounting(context, "non_defect_churn", reason)
    assert accounting_of(context)["non_defect_churn"][reason] == 1


@pytest.mark.parametrize(
    ("key", "reason"),
    [("reviews", None), ("non_defect_churn", None), ("non_defect_churn", "flake"), ("full_reviews", "ci_noise")],
)
def test_unknown_key_or_reason_raises(key, reason):
    with pytest.raises(ValueError):
        count_accounting(FlowContext(), key, reason)


def test_a_repeated_event_counts_once():
    context = FlowContext()
    assert count_accounting(context, "pipeline_runs", event="run:abc:1") is True
    assert count_accounting(context, "pipeline_runs", event="run:abc:1") is False
    assert count_accounting(context, "pipeline_runs", event="run:abc:2") is True
    assert accounting_of(context)["pipeline_runs"] == 2


def test_seen_events_are_capped():
    context = FlowContext()
    for index in range(SEEN_EVENTS_LIMIT + 5):
        count_accounting(context, "pipeline_runs", event=f"run:{index}")
    seen = context.review_state["accounting"]["seen_events"]
    assert len(seen) == SEEN_EVENTS_LIMIT and seen[-1] == f"run:{SEEN_EVENTS_LIMIT + 4}" and "run:0" not in seen


def test_record_survives_a_checkpoint_round_trip():
    context = FlowContext()
    count_accounting(context, "scoped_reviews", event="review:abc:1")
    count_accounting(context, "non_defect_churn", "bookkeeping")
    restored = FlowContext.from_dict(context.to_dict())
    assert accounting_of(restored) == accounting_of(context)
    assert count_accounting(restored, "scoped_reviews", event="review:abc:1") is False


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"review_accounting": None},
        {"review_accounting": "3 full"},
        {"accounting": {"full_reviews": 1}},
        {"review_accounting": {**dict.fromkeys(COUNTER_KEYS, 0), "non_defect_churn": {"ci_noise": 1}}},
        {
            "review_accounting": {
                **dict.fromkeys(COUNTER_KEYS, True),
                "non_defect_churn": dict.fromkeys(CHURN_REASONS, 0),
            }
        },
    ],
)
def test_accounting_of_a_legacy_or_malformed_payload_is_none(payload):
    assert accounting_of(payload) is None


def test_accounting_of_a_context_without_a_record_is_none():
    context = FlowContext()
    assert accounting_of(context) is None
    context.review_state["accounting"] = ["not", "a", "dict"]
    assert accounting_of(context) is None


def test_accounting_of_reads_a_payload_and_a_loop_result():
    context = FlowContext()
    count_accounting(context, "full_reviews")
    accounting = accounting_of(context)
    assert accounting_of({"review_accounting": accounting}) == accounting
    assert accounting_of({"accounting": accounting}) == accounting


def test_render_none_is_not_recorded_without_digits():
    lines = render_accounting_lines(None)
    assert any("not recorded" in line for line in lines)
    assert not re.search(r"\d", "".join(lines))


def test_render_counts():
    context = FlowContext()
    count_accounting(context, "full_reviews")
    count_accounting(context, "scoped_reviews")
    count_accounting(context, "scoped_reviews")
    count_accounting(context, "pipeline_runs")
    count_accounting(context, "pipeline_reruns")
    count_accounting(context, "approval_carry_overs")
    count_accounting(context, "non_defect_churn", "ci_noise")
    lines = render_accounting_lines(accounting_of(context))
    assert lines == [
        "  Reviews        : 1 full, 2 scoped; carry-overs: 1",
        "  Pipelines      : 1 run(s), 1 rerun(s)",
        "  Non-defect churn: target merge 0, bookkeeping 0, CI noise 1",
    ]


def test_a_record_started_after_passes_already_ran_is_partial():
    context = FlowContext(review_iterations_done=2)  # A resumed checkpoint that predates the record.
    count_accounting(context, "full_reviews")
    accounting = accounting_of(FlowContext.from_dict(context.to_dict()))
    assert accounting is not None and accounting["partial"] is True
    assert render_accounting_lines(accounting)[0] == "  Review churn   : partial: counting began after a resume"


def test_a_record_started_before_any_pass_is_complete():
    context = FlowContext()
    count_accounting(context, "full_reviews")
    context.review_iterations_done = 3
    count_accounting(context, "scoped_reviews")
    accounting = accounting_of(context)
    assert accounting is not None and accounting["partial"] is False
    assert not any("partial" in line for line in render_accounting_lines(accounting))
