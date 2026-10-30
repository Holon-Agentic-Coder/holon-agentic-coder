"""Complexity gate and ratchet tests for sandbox-executor.

Scanning constraint: the scan is a textual, line-by-line regex pass (not a tokenizer).
A string literal containing text such as ``"# noqa: C901"`` would be matched if it
appeared on a function ``def`` line; on any other line it is ignored by the ratchet
counters but would be flagged by ``test_no_complexity_noqa_outside_def_lines``. Keep
complexity-rule noqa text out of string literals in ``apps/sandbox-executor/src``.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

COMPLEXITY_RULES: frozenset[str] = frozenset({"C901", "PLR0911", "PLR0912", "PLR0913", "PLR0915"})

# Baseline established for intent I-1793334797-hac-ruff-complexity-gate-with-inline-baseline
BASELINE_COMPLEXITY_NOQA_COUNT: int = 184
BASELINE_ANNOTATED_FUNCTIONS_COUNT: int = 90

NOQA_DIRECTIVE_PATTERN = re.compile(r"#\s*noqa(?::\s*([A-Za-z0-9_,\s]+))?", re.IGNORECASE)
DEF_LINE_PATTERN = re.compile(r"^\s*(?:async\s+)?def\b")

SRC_DIR = Path(__file__).resolve().parent.parent / "src"

# Identity baseline: the exact multiset of (path relative to src/, complexity rule codes)
# annotations frozen at the baseline above. Unlike the raw counts, this snapshot also
# rejects one-in-one-out swaps: removing a legacy annotation never licenses adding a new
# one in a different file (or with different rule codes). Removals are allowed (ratchet
# down); any entry not present in this snapshot fails.
BASELINE_SNAPSHOT: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sandbox_executor/agent_runner.py", ("C901",)),
    ("sandbox_executor/agent_runner.py", ("C901",)),
    ("sandbox_executor/agent_runner.py", ("PLR0913",)),
    ("sandbox_executor/budgets.py", ("PLR0913",)),
    ("sandbox_executor/calibration.py", ("C901", "PLR0911", "PLR0912", "PLR0915")),
    ("sandbox_executor/calibration.py", ("C901", "PLR0912", "PLR0915")),
    ("sandbox_executor/calibration.py", ("C901", "PLR0912", "PLR0915")),
    ("sandbox_executor/calibration.py", ("C901", "PLR0912", "PLR0915")),
    ("sandbox_executor/calibration.py", ("C901", "PLR0912", "PLR0915")),
    ("sandbox_executor/calibration.py", ("PLR0913",)),
    ("sandbox_executor/cli/__init__.py", ("C901",)),
    ("sandbox_executor/cli/__init__.py", ("C901", "PLR0911", "PLR0912")),
    ("sandbox_executor/cli/__init__.py", ("C901", "PLR0911", "PLR0912", "PLR0913", "PLR0915")),
    ("sandbox_executor/cli/__init__.py", ("C901", "PLR0912", "PLR0915")),
    ("sandbox_executor/cli/__init__.py", ("PLR0913",)),
    ("sandbox_executor/cli/__init__.py", ("PLR0913",)),
    ("sandbox_executor/entrypoint/executor.py", ("C901",)),
    ("sandbox_executor/entrypoint/executor.py", ("C901",)),
    ("sandbox_executor/entrypoint/executor.py", ("C901",)),
    ("sandbox_executor/entrypoint/executor.py", ("C901", "PLR0911", "PLR0912", "PLR0915")),
    ("sandbox_executor/entrypoint/executor.py", ("C901", "PLR0912", "PLR0915")),
    ("sandbox_executor/entrypoint/executor.py", ("C901", "PLR0912", "PLR0915")),
    ("sandbox_executor/entrypoint/executor.py", ("PLR0911",)),
    ("sandbox_executor/entrypoint/executor.py", ("PLR0913",)),
    ("sandbox_executor/entrypoint/intent_creator.py", ("PLR0915",)),
    ("sandbox_executor/entrypoint/planner.py", ("C901", "PLR0912", "PLR0915")),
    ("sandbox_executor/entrypoint/planner.py", ("PLR0913",)),
    ("sandbox_executor/entrypoint/review_loop.py", ("C901", "PLR0912", "PLR0915")),
    ("sandbox_executor/flow.py", ("C901", "PLR0911", "PLR0912", "PLR0915")),
    ("sandbox_executor/flow.py", ("C901", "PLR0911", "PLR0912", "PLR0915")),
    ("sandbox_executor/flow.py", ("C901", "PLR0911", "PLR0912", "PLR0915")),
    ("sandbox_executor/flow.py", ("C901", "PLR0912", "PLR0913", "PLR0915")),
    ("sandbox_executor/flow.py", ("C901", "PLR0912", "PLR0915")),
    ("sandbox_executor/flow.py", ("C901", "PLR0912", "PLR0915")),
    ("sandbox_executor/flow.py", ("C901", "PLR0912", "PLR0915")),
    ("sandbox_executor/flow.py", ("C901", "PLR0912", "PLR0915")),
    ("sandbox_executor/flow.py", ("C901", "PLR0912", "PLR0915")),
    ("sandbox_executor/flow.py", ("C901", "PLR0912", "PLR0915")),
    ("sandbox_executor/flow.py", ("C901", "PLR0912", "PLR0915")),
    ("sandbox_executor/flow.py", ("PLR0911",)),
    ("sandbox_executor/flow.py", ("PLR0912", "PLR0913", "PLR0915")),
    ("sandbox_executor/flow.py", ("PLR0913",)),
    ("sandbox_executor/flow.py", ("PLR0913",)),
    ("sandbox_executor/flow.py", ("PLR0913",)),
    ("sandbox_executor/flow.py", ("PLR0913",)),
    ("sandbox_executor/flow.py", ("PLR0913",)),
    ("sandbox_executor/flow.py", ("PLR0913",)),
    ("sandbox_executor/formatting.py", ("C901", "PLR0911")),
    ("sandbox_executor/knowledge/consult.py", ("PLR0913", "PLR0915")),
    ("sandbox_executor/knowledge/index.py", ("C901", "PLR0912", "PLR0915")),
    ("sandbox_executor/knowledge/promotion.py", ("C901", "PLR0912", "PLR0915")),
    ("sandbox_executor/knowledge/records.py", ("C901", "PLR0912", "PLR0913")),
    ("sandbox_executor/knowledge/stats.py", ("PLR0913",)),
    ("sandbox_executor/ledger.py", ("C901",)),
    ("sandbox_executor/ledger.py", ("C901", "PLR0912")),
    ("sandbox_executor/ledger.py", ("C901", "PLR0912", "PLR0913", "PLR0915")),
    ("sandbox_executor/ledger.py", ("PLR0913",)),
    ("sandbox_executor/local_llm.py", ("C901", "PLR0912")),
    ("sandbox_executor/local_llm.py", ("PLR0913",)),
    ("sandbox_executor/preflight.py", ("PLR0911",)),
    ("sandbox_executor/preflight.py", ("PLR0913",)),
    ("sandbox_executor/preflight.py", ("PLR0913",)),
    ("sandbox_executor/redaction.py", ("PLR0911",)),
    ("sandbox_executor/review_ensemble.py", ("PLR0913",)),
    ("sandbox_executor/review_ensemble.py", ("PLR0913",)),
    ("sandbox_executor/review_ensemble.py", ("PLR0913",)),
    ("sandbox_executor/review_ensemble.py", ("PLR0913",)),
    ("sandbox_executor/review_ensemble.py", ("PLR0913",)),
    ("sandbox_executor/review_ensemble.py", ("PLR0913",)),
    ("sandbox_executor/token_reduction/hybrid_cache.py", ("C901", "PLR0912")),
    ("sandbox_executor/token_reduction/hybrid_cache.py", ("C901", "PLR0912")),
    ("sandbox_executor/token_reduction/mitm_addon.py", ("C901",)),
    ("sandbox_executor/token_reduction/mitm_addon.py", ("C901", "PLR0912")),
    ("sandbox_executor/token_reduction/mitm_addon.py", ("C901", "PLR0912", "PLR0915")),
    ("sandbox_executor/token_reduction/mitm_addon.py", ("C901", "PLR0912", "PLR0915")),
    ("sandbox_executor/token_reduction/mitm_addon.py", ("C901", "PLR0912", "PLR0915")),
    ("sandbox_executor/token_reduction/mitm_addon.py", ("C901", "PLR0912", "PLR0915")),
    ("sandbox_executor/token_reduction/mitm_addon.py", ("C901", "PLR0912", "PLR0915")),
    ("sandbox_executor/token_reduction/mitm_addon.py", ("PLR0911",)),
    ("sandbox_executor/token_reduction/mitm_addon.py", ("PLR0913",)),
    ("sandbox_executor/token_reduction/payload_cleaner.py", ("C901", "PLR0912")),
    ("sandbox_executor/token_reduction/payload_cleaner.py", ("C901", "PLR0912")),
    ("sandbox_executor/token_reduction/payload_cleaner.py", ("C901", "PLR0912")),
    ("sandbox_executor/token_reduction/payload_cleaner.py", ("C901", "PLR0912")),
    ("sandbox_executor/token_reduction/payload_cleaner.py", ("C901", "PLR0912")),
    ("sandbox_executor/token_reduction/payload_cleaner.py", ("C901", "PLR0912", "PLR0915")),
    ("sandbox_executor/turns.py", ("C901", "PLR0912", "PLR0913", "PLR0915")),
    ("sandbox_executor/turns.py", ("PLR0913",)),
    ("sandbox_executor/turns.py", ("PLR0913",)),
    ("sandbox_executor/turns.py", ("PLR0913",)),
)


def extract_complexity_rules_from_line(line: str) -> list[str]:
    """Extract complexity rule codes from a function definition line if # noqa is present."""
    if not DEF_LINE_PATTERN.match(line):
        return []
    match = NOQA_DIRECTIVE_PATTERN.search(line)
    if not match:
        return []
    codes_str = match.group(1)
    if not codes_str:
        return []
    rules = [c.strip() for c in codes_str.split(",") if c.strip()]
    return [r for r in rules if r in COMPLEXITY_RULES]


def scan_complexity_noqa(src_dir: Path | str) -> dict[str, Any]:
    """Scan all python files in src_dir for complexity # noqa directives on function definitions."""
    path = Path(src_dir)
    total_rule_count = 0
    annotated_functions_count = 0
    records: list[dict[str, Any]] = []

    for py_file in sorted(path.rglob("*.py")):
        with open(py_file, encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                rules = extract_complexity_rules_from_line(line)
                if rules:
                    total_rule_count += len(rules)
                    annotated_functions_count += 1
                    records.append(
                        {
                            "file": str(py_file),
                            "line": line_no,
                            "rules": rules,
                            "source": line.strip(),
                        }
                    )

    return {
        "total_rule_count": total_rule_count,
        "annotated_functions_count": annotated_functions_count,
        "records": records,
    }


def assert_complexity_baseline(
    current_count: int,
    baseline_count: int = BASELINE_COMPLEXITY_NOQA_COUNT,
    annotated_functions: int | None = None,
    baseline_functions: int = BASELINE_ANNOTATED_FUNCTIONS_COUNT,
) -> None:
    """Assert that the complexity count and annotated functions do not exceed baseline."""
    assert current_count <= baseline_count, (
        f"Complexity # noqa baseline exceeded: current={current_count} > baseline={baseline_count}. "
        "Adding new complex functions or unapproved # noqa suppressions is strictly forbidden. "
        "Please refactor the offending function to reduce complexity."
    )
    if annotated_functions is not None:
        assert annotated_functions <= baseline_functions, (
            f"Annotated functions count exceeded: current={annotated_functions} > baseline={baseline_functions}. "
            "Please refactor rather than annotating new functions."
        )


def test_complexity_noqa_baseline_ratchet():
    """Verify that complexity # noqa directives across apps/sandbox-executor/src do not exceed baseline."""
    assert SRC_DIR.is_dir(), f"Source directory not found: {SRC_DIR}"
    scan_result = scan_complexity_noqa(SRC_DIR)

    current_rules = scan_result["total_rule_count"]
    current_funcs = scan_result["annotated_functions_count"]

    assert_complexity_baseline(
        current_count=current_rules,
        baseline_count=BASELINE_COMPLEXITY_NOQA_COUNT,
        annotated_functions=current_funcs,
        baseline_functions=BASELINE_ANNOTATED_FUNCTIONS_COUNT,
    )

    if current_rules < BASELINE_COMPLEXITY_NOQA_COUNT:
        # Code was simplified! The ratchet allows updating BASELINE_COMPLEXITY_NOQA_COUNT downwards.
        print(
            f"Notice: Complexity rules reduced from {BASELINE_COMPLEXITY_NOQA_COUNT} to {current_rules}. "
            "Update BASELINE_COMPLEXITY_NOQA_COUNT to ratchet down."
        )


def test_complexity_noqa_identity_baseline():
    """Verify every complexity # noqa annotation is one frozen in BASELINE_SNAPSHOT.

    The counts in the ratchet test above bound the totals; this test additionally rejects
    one-in-one-out swaps by requiring the current multiset of (relative path, rule codes)
    annotations to be a sub-multiset of the frozen baseline. Removals pass (ratchet down,
    then prune the removed entries from BASELINE_SNAPSHOT); any new or relocated
    annotation fails.
    """
    assert SRC_DIR.is_dir(), f"Source directory not found: {SRC_DIR}"
    scan_result = scan_complexity_noqa(SRC_DIR)

    current: Counter[tuple[str, tuple[str, ...]]] = Counter(
        (Path(record["file"]).relative_to(SRC_DIR).as_posix(), tuple(record["rules"]))
        for record in scan_result["records"]
    )
    baseline: Counter[tuple[str, tuple[str, ...]]] = Counter(BASELINE_SNAPSHOT)

    new_entries = current - baseline
    assert not new_entries, (
        "Complexity # noqa annotations not present in the frozen baseline snapshot: "
        f"{sorted(new_entries.elements())}. Adding or relocating complexity suppressions is "
        "forbidden even when another suppression was removed. Refactor the function instead."
    )


def test_complexity_counting_logic_filters_correctly():
    """Verify that non-complexity # noqa comments are ignored and complexity rules are extracted."""
    # Non-complexity noqa comments
    assert extract_complexity_rules_from_line("def foo():  # noqa: F401") == []
    assert extract_complexity_rules_from_line("def foo():  # noqa: E501") == []
    assert extract_complexity_rules_from_line("def foo():  # noqa: UP036") == []
    assert extract_complexity_rules_from_line("def foo():  # noqa") == []

    # Non-def lines
    assert extract_complexity_rules_from_line("x = 1  # noqa: C901") == []
    assert extract_complexity_rules_from_line("    return True  # noqa: PLR0911") == []

    # Valid complexity rules
    assert extract_complexity_rules_from_line("def foo():  # noqa: C901") == ["C901"]
    assert extract_complexity_rules_from_line("def foo():  # noqa: C901, PLR0912") == ["C901", "PLR0912"]
    assert extract_complexity_rules_from_line("async def bar():  # noqa: PLR0911, PLR0913") == [
        "PLR0911",
        "PLR0913",
    ]
    assert extract_complexity_rules_from_line("    def baz(x, y):  # noqa: C901, PLR0912, PLR0915") == [
        "C901",
        "PLR0912",
        "PLR0915",
    ]

    # Mixed complexity and non-complexity rules
    assert extract_complexity_rules_from_line("def foo():  # noqa: F401, C901, E501") == ["C901"]


def test_complexity_ratchet_failure_mode():
    """Verify that exceeding the baseline raises an AssertionError with an informative message."""
    with pytest.raises(AssertionError, match="Complexity # noqa baseline exceeded"):
        assert_complexity_baseline(
            current_count=BASELINE_COMPLEXITY_NOQA_COUNT + 1,
            baseline_count=BASELINE_COMPLEXITY_NOQA_COUNT,
        )

    with pytest.raises(AssertionError, match="Annotated functions count exceeded"):
        assert_complexity_baseline(
            current_count=BASELINE_COMPLEXITY_NOQA_COUNT,
            baseline_count=BASELINE_COMPLEXITY_NOQA_COUNT,
            annotated_functions=BASELINE_ANNOTATED_FUNCTIONS_COUNT + 1,
            baseline_functions=BASELINE_ANNOTATED_FUNCTIONS_COUNT,
        )


def test_no_complexity_noqa_outside_def_lines():
    """Verify that complexity # noqa directives are not placed on lines other than function definitions.

    Textual-scan constraint: this check matches raw line text, so a string literal
    containing e.g. "# noqa: C901" on a non-def line is reported as a violation.
    That is intentional (see module docstring): keep complexity-rule noqa text out of
    string literals in src rather than special-casing them here.
    """
    violations: list[str] = []
    for py_file in sorted(SRC_DIR.rglob("*.py")):
        with open(py_file, encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                if DEF_LINE_PATTERN.match(line):
                    continue
                match = NOQA_DIRECTIVE_PATTERN.search(line)
                if match and match.group(1):
                    found_rules = [r.strip() for r in match.group(1).split(",") if r.strip() in COMPLEXITY_RULES]
                    if found_rules:
                        violations.append(f"{py_file}:{line_no} -> {found_rules}")

    assert not violations, f"Found complexity # noqa directives outside function definitions: {violations}"
