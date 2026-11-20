"""Plan lookups in calibration keyed on the intent and plan id, never the plan id alone."""

import json
import logging
from pathlib import Path

import pytest
from sandbox_executor.calibration import PredictedMetrics, extract_branch_components, parse_predicted_metrics
from sandbox_executor.calibration_ev import is_matching_record
from sandbox_executor.calibration_execution_owner import unique_plan_branch_of_execution
from sandbox_executor.calibration_predicted import cross_check_selected_row, overall_plan_metrics

PREDICTED_LOGGER = "sandbox_executor.calibration_predicted"
INTENT = "I-1795184210-fix"
PLAN_ID = "P-1795184234-CLA-CO5.5"
PLAN_BRANCH = f"{INTENT}/{PLAN_ID}/_"
PLAN_FILE = f"plans/{INTENT}/{PLAN_ID}.md"


def _write_plan(root: Path, rel: str, ev: float) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# Plan\n\n## Overall Plan Metrics\n\n| metric | value |\n| ev_pred | {ev} |\n", encoding="utf-8")


def test_twin_intents_planned_in_one_second_resolve_to_their_own_plan_rows(tmp_path: Path) -> None:
    """Same runner, model and second under two intents: one plan id, two plan files, two rows, resolved apart."""
    plan_id = "P-1794567531-CLA-CS5.5"
    ledger = tmp_path / "holon-knowledge" / "ledger" / "plans.jsonl"
    ledger.parent.mkdir(parents=True)
    rows = [
        {"plan_id": plan_id, "intent_branch": f"I-1794567515-{slug}/_", "ev": ev, "plan_file": f"plans/{slug}.md"}
        for slug, ev in (("alpha", 11.0), ("beta", 22.0))
    ]
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    alpha, alpha_meta = parse_predicted_metrics(f"I-1794567515-alpha/{plan_id}/E-1-CLA-CS5.5/_", str(tmp_path))
    beta, beta_meta = parse_predicted_metrics(f"I-1794567515-beta/{plan_id}/_", str(tmp_path))
    assert (alpha.ev, beta.ev) == (11.0, 22.0)
    assert (alpha_meta["intent_branch"], beta_meta["intent_branch"]) == (
        rows[0]["intent_branch"],
        rows[1]["intent_branch"],
    )
    assert parse_predicted_metrics(f"I-1794567515-gamma/{plan_id}/_", str(tmp_path))[1] == {}


def test_plan_markdown_fallback_reads_the_nested_layout_then_the_legacy_flat_file(tmp_path: Path) -> None:
    _write_plan(tmp_path, "plans/I-1-alpha/P-2-CLA-CF5.md", 5.0)
    _write_plan(tmp_path, "plans/P-3-hex-claude-agent-claude-fable-5.md", 7.0)
    nested, nested_meta = parse_predicted_metrics("I-1-alpha/P-2-CLA-CF5/_", str(tmp_path))
    legacy, legacy_meta = parse_predicted_metrics("I-1-beta/P-3-hex-claude-agent-claude-fable-5/_", str(tmp_path))
    assert (nested.ev, nested_meta["plan_file"]) == (5.0, "plans/I-1-alpha/P-2-CLA-CF5.md")
    assert (legacy.ev, legacy_meta["plan_file"]) == (7.0, "plans/P-3-hex-claude-agent-claude-fable-5.md")
    assert parse_predicted_metrics("I-1-beta/P-2-CLA-CF5/_", str(tmp_path)) == (PredictedMetrics(), {})


@pytest.mark.parametrize(
    ("branch", "plan_branch", "plan_parent"),
    [
        ("I-1-a/P-2-CLA-CS5.5/E-3-CLA-CS5.5/_", "I-1-a/P-2-CLA-CS5.5/_", "I-1-a"),
        ("I-1-a--1-child/P-2-CLA-CS5.5/E-3-CLA-CS5.5/_", "I-1-a--1-child/P-2-CLA-CS5.5/_", "I-1-a--1-child"),
        ("I-1-a/P-2-CLA-CS5.5/P-4-CLA-CF5/E-5-CLA-CF5/_", "I-1-a/P-2-CLA-CS5.5/P-4-CLA-CF5/_", "I-1-a/P-2-CLA-CS5.5"),
        (
            "I-1-a/P-2-ab12cd34-claude-agent-m/E-3-ef56ab78-claude-agent-m/_",
            "I-1-a/P-2-ab12cd34-claude-agent-m/_",
            "I-1-a",
        ),
    ],
)
def test_branch_components_keep_the_full_parent_of_the_plan(branch: str, plan_branch: str, plan_parent: str) -> None:
    components = extract_branch_components(branch)
    assert (components["plan_branch"], components["plan_parent"]) == (plan_branch, plan_parent)


def test_a_bare_execution_id_owned_by_two_plans_resolves_to_neither(tmp_path: Path) -> None:
    ledger = tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"
    ledger.parent.mkdir(parents=True)
    rows = [
        {"entry_type": "summary", "execution_id": "E-9-CLA-CS5.5", "plan_branch": f"I-1-{slug}/P-2-CLA-CS5.5/_"}
        for slug in ("alpha", "beta")
    ]
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    assert unique_plan_branch_of_execution("E-9-CLA-CS5.5", str(ledger)) == ""
    ledger.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    assert unique_plan_branch_of_execution("E-9-CLA-CS5.5", str(ledger)) == rows[0]["plan_branch"]


def test_execution_rows_never_match_across_plans_even_with_one_execution_id() -> None:
    row = {"entry_type": "summary", "execution_id": "E-9-CLA-CS5.5", "plan_branch": "I-1-alpha/P-2-CLA-CS5.5/_"}
    assert is_matching_record(row, "E-9-CLA-CS5.5", "I-1-alpha/P-2-CLA-CS5.5/_")
    assert not is_matching_record(row, "E-9-CLA-CS5.5", "I-1-beta/P-2-CLA-CS5.5/_")
    # A review row of the plan matches through its plan; a bare id lookup without a plan matches on the id.
    assert is_matching_record({**row, "execution_id": "REV-1"}, "E-9-CLA-CS5.5", "I-1-alpha/P-2-CLA-CS5.5/_")
    assert is_matching_record(row, "E-9-CLA-CS5.5", "")


def _write_ledger(root: Path, name: str, rows: list[dict]) -> None:
    path = root / "holon-knowledge" / "ledger" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _plans_row(ev: float, **extra: object) -> dict:
    row = {"plan_id": PLAN_ID, "intent_branch": f"{INTENT}/_", "plan_file": PLAN_FILE, "ev": ev, "cost": 10.0}
    return {**row, **extra}


@pytest.mark.parametrize("corrected_first", [True, False])
def test_the_highest_ledger_revision_plans_row_wins_regardless_of_line_order(
    tmp_path: Path, corrected_first: bool
) -> None:
    corrected = _plans_row(52.26, ledger_revision=2, created_at="2026-11-20T15:00:00Z", corrects_revision=1)
    original = _plans_row(7.38, created_at="2026-11-01T10:00:00Z")
    _write_ledger(tmp_path, "plans.jsonl", [corrected, original] if corrected_first else [original, corrected])
    predicted, record = parse_predicted_metrics(PLAN_BRANCH, str(tmp_path))
    assert predicted.ev == 52.26
    assert record["ledger_revision"] == 2


def test_a_revision_tie_goes_to_the_later_created_at_and_an_unparseable_one_loses(tmp_path: Path) -> None:
    rows = [
        _plans_row(1.0, ledger_revision=2, created_at="2026-11-20T15:00:00+00:00"),
        _plans_row(2.0, ledger_revision=2, created_at="2026-11-20T16:00:00Z"),
        _plans_row(3.0, ledger_revision=2, created_at="not-a-date"),
        _plans_row(4.0, ledger_revision="bogus", created_at="2027-01-01T00:00:00Z"),
    ]
    _write_ledger(tmp_path, "plans.jsonl", rows)
    assert parse_predicted_metrics(PLAN_BRANCH, str(tmp_path))[0].ev == 2.0


def test_a_plans_row_disagreeing_with_the_overall_table_logs_both_values(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=PREDICTED_LOGGER)
    _write_ledger(tmp_path, "plans.jsonl", [_plans_row(7.38)])
    plan = tmp_path / PLAN_FILE
    plan.parent.mkdir(parents=True)
    plan.write_text(
        "## Step 1\n\n| metric | value |\n| ev_pred | 7.38 |\n\n"
        "## Overall Plan Metrics\n\n| metric | value |\n| cost_pred | 10.0 |\n| ev_pred | 52.26 |\n\n## Notes\n",
        encoding="utf-8",
    )
    predicted, _ = parse_predicted_metrics(PLAN_BRANCH, str(tmp_path))
    assert predicted.ev == 7.38
    warnings = [r.getMessage() for r in caplog.records if r.name == PREDICTED_LOGGER]
    assert len(warnings) == 1
    assert all(text in warnings[0] for text in ("7.38", "52.26", "Overall", PLAN_FILE))


def test_float_noise_within_tolerance_logs_no_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger=PREDICTED_LOGGER)
    _write_plan(tmp_path, PLAN_FILE, 54.62)
    _write_ledger(tmp_path, "plans.jsonl", [_plans_row(54.620000000000005)])
    assert parse_predicted_metrics(PLAN_BRANCH, str(tmp_path))[0].ev == 54.620000000000005
    assert not [r for r in caplog.records if r.name == PREDICTED_LOGGER]


def test_an_execution_predicted_metrics_mismatch_names_both_values(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=PREDICTED_LOGGER)
    execution_id = "E-1795184420-CLA-CO5.5"
    _write_ledger(tmp_path, "plans.jsonl", [_plans_row(41.85)])
    summary = {"entry_type": "summary", "execution_id": execution_id, "plan_branch": PLAN_BRANCH}
    _write_ledger(tmp_path, "executions.jsonl", [{**summary, "predicted_metrics": {"cost": 12.5, "ev": 41.85}}])
    parse_predicted_metrics(PLAN_BRANCH, str(tmp_path), execution_id=execution_id)
    warnings = [r.getMessage() for r in caplog.records if r.name == PREDICTED_LOGGER]
    assert len(warnings) == 1
    assert all(text in warnings[0] for text in ("cost=10", "cost=12.5", execution_id))
    mismatches = cross_check_selected_row(_plans_row(41.85), PLAN_BRANCH, str(tmp_path), execution_id)
    assert [(m.field, m.selected, m.other) for m in mismatches] == [("cost", 10.0, 12.5)]


def test_a_plan_file_without_an_overall_section_is_not_compared(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=PREDICTED_LOGGER)
    content = "## Step 1\n\n| metric | value |\n| ev_pred | 52.26 |\n"
    plan = tmp_path / PLAN_FILE
    plan.parent.mkdir(parents=True)
    plan.write_text(content, encoding="utf-8")
    _write_ledger(tmp_path, "plans.jsonl", [_plans_row(7.38)])
    assert overall_plan_metrics(content) == {}
    assert cross_check_selected_row(_plans_row(7.38), PLAN_BRANCH, str(tmp_path)) == []
    assert parse_predicted_metrics(PLAN_BRANCH, str(tmp_path))[0].ev == 7.38
    assert not [r for r in caplog.records if r.name == PREDICTED_LOGGER]
