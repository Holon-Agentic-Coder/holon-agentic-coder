"""Parity of ``query_ledger`` with its frozen pre-refactor copy on the repository's real ledgers.

The oracle in ``tests/ledger_query_reference.py`` is the previous implementation, so this check stays valid as the
append-only ledgers grow, where a golden snapshot would break on the next append. Each ledger is copied once into a
temporary directory and both implementations read that copy, so a concurrent append cannot split the comparison.

The live query reads the pinned file in every case. The oracle's answers come from ``reference_results``: each pinned
ledger is parsed once per module and each filter set is applied to those rows on first use
(``tests/ledger_parity_rows.py``); ``test_row_reference_matches_file_reference`` checks that this gives what the
oracle gives when it reads the file itself.
"""

from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from sandbox_executor.cli import main as cli_main
from sandbox_executor.ledger import format_query_results_table, query_ledger

from tests.ledger_parity_rows import ReferenceResults, reference_query_rows, reference_rows
from tests.ledger_query_reference import reference_query_ledger

REPO_ROOT = Path(__file__).resolve().parents[3]
LEDGER_DIR = REPO_ROOT / "holon-knowledge" / "ledger"
LEDGER_NAMES = ("executions", "intents", "plans")


def _most_common_values(path: Path) -> dict[str, str]:
    """Return the most frequent category, label, origin and plan-branch stem of a ledger, in one read-only pass."""
    counters: dict[str, Counter[str]] = {key: Counter() for key in ("category", "label", "origin", "target")}
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                if isinstance(rec.get("category"), str):
                    counters["category"][rec["category"]] += 1
                if isinstance(rec.get("action_origin"), str):
                    counters["origin"][rec["action_origin"]] += 1
                labels = rec.get("labels")
                for label in labels.split(",") if isinstance(labels, str) else labels or []:
                    counters["label"][str(label).strip().lower()] += 1
                branch = rec.get("plan_branch") or rec.get("target")
                if isinstance(branch, str) and branch:
                    counters["target"][branch.split("/")[0][:14]] += 1
    fallbacks = {"category": "code", "label": "review", "origin": "pr_review", "target": "I-17"}
    return {key: (counter.most_common(1)[0][0] if counter else fallbacks[key]) for key, counter in counters.items()}


_DISCOVERED = _most_common_values(LEDGER_DIR / "executions.jsonl")

FILTER_MATRIX: dict[str, dict[str, Any]] = {
    "no-filter": {},
    "entry-type-action": {"entry_type": "action"},
    "entry-type-summary": {"entry_type": "summary"},
    "entry-type-all": {"entry_type": "all"},
    "entry-type-mixed-case": {"entry_type": "ACTION"},
    "category-frequent": {"category": _DISCOVERED["category"]},
    "category-test": {"category": "test"},
    "labels-single": {"labels": _DISCOVERED["label"]},
    "labels-comma-list": {"labels": f"{_DISCOVERED['label']}, holon-flow ,cli"},
    "labels-list": {"labels": ["Finding", "security"]},
    "target-substring": {"target": _DISCOVERED["target"]},
    "outcome-success": {"outcome": "success"},
    "outcome-failure": {"outcome": "failure"},
    "outcome-failed": {"outcome": "failed"},
    "outcome-proposed": {"outcome": "Proposed"},
    "origin-frequent": {"origin": _DISCOVERED["origin"]},
    "review-severity": {"review_severity": "important"},
    "query-frequent-word": {"query": "review"},
    "query-rare-id": {"query": "E-1795185523-CLA-CO5.5"},
    "limit-5": {"limit": 5},
    "limit-0": {"limit": 0},
    "combined-type-outcome-query": {"entry_type": "action", "outcome": "failure", "query": "review"},
    "combined-category-labels-limit": {"category": _DISCOVERED["category"], "labels": "finding", "limit": 7},
}


def _run(implementation: Any, path: str, filters: dict[str, Any]) -> tuple[str, Any]:
    """Run one implementation, capturing a raised error as its type and message so errors are compared too."""
    try:
        return "ok", implementation(path, **filters)
    except Exception as exc:  # the oracle's own errors (e.g. a null review_severity) are part of the contract
        return "raised", (type(exc).__name__, str(exc))


@pytest.fixture(scope="module")
def pinned_ledgers(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Copy each real ledger once, so both implementations read identical bytes even if the flow appends meanwhile."""
    pinned_dir = tmp_path_factory.mktemp("pinned-ledgers")
    pinned: dict[str, Path] = {}
    for name in LEDGER_NAMES:
        source = LEDGER_DIR / f"{name}.jsonl"
        if source.exists():
            pinned[name] = Path(shutil.copyfile(source, pinned_dir / source.name))
    return pinned


def _pinned(pinned_ledgers: dict[str, Path], name: str) -> str:
    if name not in pinned_ledgers:
        pytest.skip(f"{name}.jsonl is only present in the real checkout")
    return str(pinned_ledgers[name])


@pytest.fixture(scope="module")
def reference_results(pinned_ledgers: dict[str, Path]) -> ReferenceResults:
    """One answer cache per module (per xdist worker), filled only for the cases the worker runs."""
    return ReferenceResults(lambda name: _pinned(pinned_ledgers, name), _run)


@pytest.mark.parametrize("ledger_name", LEDGER_NAMES)
@pytest.mark.parametrize("filter_id", FILTER_MATRIX)
def test_query_matches_reference_on_real_ledgers(
    pinned_ledgers: dict[str, Path], reference_results: ReferenceResults, ledger_name: str, filter_id: str
) -> None:
    """The live query and the frozen oracle agree on records, table output and raised errors."""
    path = _pinned(pinned_ledgers, ledger_name)
    filters = FILTER_MATRIX[filter_id]
    status, live = _run(query_ledger, path, filters)
    reference_status, reference, reference_table = reference_results.answer(ledger_name, filter_id, filters)
    assert (status, live) == (reference_status, reference)
    if status == "ok":
        assert format_query_results_table(live) == reference_table


@pytest.mark.parametrize("ledger_name", LEDGER_NAMES)
def test_row_reference_matches_file_reference(pinned_ledgers: dict[str, Path], ledger_name: str) -> None:
    """The oracle over rows parsed once answers as the oracle reading the file, across label, entry-type and limit."""
    path = _pinned(pinned_ledgers, ledger_name)
    rows = reference_rows(path)
    for filter_id in ("no-filter", "labels-single", "entry-type-action", "limit-5"):
        filters = FILTER_MATRIX[filter_id]
        from_rows = _run(lambda _path, **f: reference_query_rows(rows, **f), path, filters)
        assert from_rows == _run(reference_query_ledger, path, filters), filter_id


@pytest.mark.parametrize("ledger_name", LEDGER_NAMES)
def test_cli_json_output_matches_reference(
    pinned_ledgers: dict[str, Path],
    reference_results: ReferenceResults,
    ledger_name: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``holon ledger query --json`` prints the oracle's records and exits 0."""
    path = _pinned(pinned_ledgers, ledger_name)
    argv = ["ledger", "query", "--ledger-file", path, "--json", "--entry-type", "all", "--query", "review"]
    with pytest.raises(SystemExit) as exit_info:
        cli_main(argv)
    assert exit_info.value.code == 0
    status, expected, _table = reference_results.answer(
        ledger_name, "cli-json", {"entry_type": "all", "query": "review"}
    )
    assert status == "ok"
    assert capsys.readouterr().out == json.dumps(expected, indent=2) + "\n"


def test_misspelt_filter_raises_type_error_in_both(tmp_path: Path) -> None:
    """An unknown filter is rejected by both implementations instead of silently matching everything."""
    ledger = tmp_path / "executions.jsonl"
    ledger.write_text('{"entry_type": "action"}\n', encoding="utf-8")
    with pytest.raises(TypeError):
        query_ledger(str(ledger), catgory="code")
    with pytest.raises(TypeError):
        reference_query_ledger(str(ledger), catgory="code")


SYNTHETIC_ROWS = [
    {"entry_type": "action", "category": "Code", "labels": "UI, db", "outcome": "failed", "target": "src/a.py"},
    {"labels": ["Api", " "], "status": "Failure", "plan_branch": "I-1/P-2/_", "summary": "Needle"},
    {"entry_type": "summary", "labels": 7, "outcome": "success", "action_origin": "ci_fix", "review_severity": "Nit"},
    {"entry_type": "ACTION", "error_context": "needle trace", "mistake_category": "typo"},
]


@pytest.mark.parametrize("filters", FILTER_MATRIX.values(), ids=FILTER_MATRIX.keys())
def test_query_matches_reference_on_synthetic_edge_rows(tmp_path: Path, filters: dict[str, Any]) -> None:
    """Edge rows absent from the real ledgers (blank and corrupt lines, odd label types) also agree."""
    ledger = tmp_path / "edge.jsonl"
    lines = [json.dumps(row) for row in SYNTHETIC_ROWS] + ["", "{not json"]
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert _run(query_ledger, str(ledger), filters) == _run(reference_query_ledger, str(ledger), filters)
