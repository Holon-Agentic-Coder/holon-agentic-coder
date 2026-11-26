from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sandbox_executor.harness_reference_patterns import HARNESS_PATTERNS, find_references, sample_reference
from sandbox_executor.intent_payload_rules import (
    check_intent_payload,
    harness_reference_violations,
    hub_file_warnings,
    intent_texts,
    scope_shape_violations,
)


def _clean_intent() -> dict[str, Any]:
    return {
        "goal": "Make the scope check reject directories and globs.",
        "description": "Two invariants about intent files are enforced by nothing.",
        "rationale": "Hand-kept rules slip.",
        "scope": [
            "apps/sandbox-executor/src/sandbox_executor/intent_payload_rules.py (new)",
            "apps/sandbox-executor/src/sandbox_executor/intent_scope_check.py",
            "apps/sandbox-executor/tests/test_intent_payload_rules.py (new)",
        ],
        "acceptance_criteria": ["intent_payload_rules.py rejects a directory entry.", "Lint passes."],
    }


def test_existing_directory_entry_is_refused(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "core.py").write_text("", encoding="utf-8")

    violations = scope_shape_violations(["src/pkg", "src/pkg/core.py"], str(tmp_path))

    assert len(violations) == 1
    assert "'src/pkg' is an existing directory" in violations[0]


def test_directory_check_needs_a_worktree() -> None:
    assert scope_shape_violations(["src/pkg"]) == []


@pytest.mark.parametrize(
    ("entry", "problem"),
    [
        ("src/*.py", "contains a glob character"),
        ("src/core?.py", "contains a glob character"),
        ("src/core[12].py", "contains a glob character"),
        ("src/{a,b}.py", "contains a glob character"),
        ("src/pkg/", "ends in a slash"),
    ],
)
def test_shape_violations_are_refused(entry: str, problem: str) -> None:
    findings = check_intent_payload({**_clean_intent(), "scope": [entry]})

    assert findings.refused
    assert findings.violations == [
        f"scope entry '{entry}' {problem}: list each file on its own; a directory or glob widens the touch set and "
        "blocks every other flow at Stage 3 admission"
    ]


@pytest.mark.parametrize("entry", ["src/pkg/ (new)", "new: src/*.py"])
def test_new_marker_is_stripped_before_the_check(entry: str) -> None:
    assert len(scope_shape_violations([entry])) == 1


def test_new_marker_on_a_plain_file_passes() -> None:
    assert scope_shape_violations(["src/pkg/new_module.py (new)", "new: docs/page.txt"]) == []


@pytest.mark.parametrize(
    "entry",
    [
        "apps/sandbox-executor/src/sandbox_executor/flow/lazy_exports.py",
        "apps/sandbox-executor/tests/conftest.py",
        "docs/safety.md",
        "scripts/pre_commit_check.py",
    ],
)
def test_hub_files_give_warnings_only(entry: str) -> None:
    findings = check_intent_payload({**_clean_intent(), "scope": [entry]})

    assert not findings.refused
    assert len(findings.warnings) == 1
    assert findings.warnings[0].startswith(f"{entry}: hub file")


def test_non_hub_files_give_no_warning() -> None:
    assert hub_file_warnings(["docs/guide/page.md", "apps/x/docs_helper.py"]) == []


def test_reconstructed_intent_with_harness_reference_is_refused() -> None:
    task_directory = sample_reference("task-tracking directory")
    task_id = sample_reference("task id")
    payload = {
        **_clean_intent(),
        "description": f"Follow up on {task_directory}.",
        "acceptance_criteria": ["Lint passes.", f"Closes {task_id}."],
    }

    findings = check_intent_payload(payload)

    assert findings.refused
    assert [message.split("'")[1] for message in findings.violations] == ["description", "acceptance_criteria"]
    for message in findings.violations:
        assert task_directory not in message
        assert task_id not in message
        assert find_references(message) == []
    assert "task-tracking directory" in findings.violations[0]
    assert "task id" in findings.violations[1]


@pytest.mark.parametrize("label", [pattern.label for pattern in HARNESS_PATTERNS])
def test_every_pattern_is_refused_in_every_text_field(label: str) -> None:
    for name in ("goal", "description", "rationale"):
        violations = harness_reference_violations({name: sample_reference(label)})
        assert len(violations) == 1
        assert label in violations[0]


def test_scope_entry_with_harness_reference_is_not_echoed() -> None:
    entry = sample_reference("task-tracking directory").split()[-1].rsplit("/", 1)[0] + "/"

    findings = check_intent_payload({**_clean_intent(), "scope": [entry]})

    assert len(findings.violations) == 2
    assert findings.violations[0].startswith("scope entry 1 ends in a slash")
    assert all(find_references(message) == [] for message in findings.violations)


def test_intent_texts_reads_non_list_values_as_one_string() -> None:
    texts = list(intent_texts({"goal": "g", "scope": "a.py", "acceptance_criteria": None, "rationale": 3}))

    assert texts == [("goal", "g"), ("scope", "a.py")]


def test_clean_intent_passes(tmp_path: Path) -> None:
    findings = check_intent_payload(_clean_intent(), str(tmp_path))

    assert not findings.refused
    assert findings.violations == []
    assert findings.warnings == []
