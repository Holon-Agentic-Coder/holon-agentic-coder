"""Tests for scripts/check_flow_module_budgets.py, the per-module size ratchet of the flow package.

CI's hygiene job runs ruff directly rather than ``task lint``, so this test is what enforces the ratchet there.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.check_flow_module_budgets import main


def _package(root: Path, modules: dict[str, int]) -> Path:
    for module, lines in modules.items():
        path = root / "flow" / module
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(f"x{i} = {i}\n" for i in range(lines)), encoding="utf-8")
    return root / "flow"


def _budgets(root: Path, budgets: dict[str, int]) -> Path:
    path = root / "budgets.json"
    path.write_text(json.dumps({"description": "test", "budgets": budgets}), encoding="utf-8")
    return path


def test_the_live_flow_package_is_within_its_recorded_budgets(capsys: pytest.CaptureFixture[str]):
    assert main([]) == 0
    assert "Flow module budgets passed" in capsys.readouterr().out


def test_a_module_at_or_under_its_budget_passes(tmp_path: Path):
    package = _package(tmp_path, {"__init__.py": 3, "stages/plan.py": 5})
    assert (
        main(
            [
                "--package-dir",
                str(package),
                "--budgets",
                str(_budgets(tmp_path, {"__init__.py": 3, "stages/plan.py": 9})),
            ]
        )
        == 0
    )


@pytest.mark.parametrize(
    ("modules", "budgets", "message"),
    [
        ({"pipeline.py": 11}, {"pipeline.py": 10}, "pipeline.py: 11 lines exceeds its budget of 10"),
        ({"pipeline.py": 4, "review/new.py": 2}, {"pipeline.py": 4}, "review/new.py: no budget entry"),
        ({"pipeline.py": 4}, {"pipeline.py": 4, "gone.py": 7}, "gone.py: budget entry names a missing module"),
    ],
    ids=["over-budget", "missing-entry", "stale-entry"],
)
def test_each_violation_fails_with_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], modules, budgets, message
):
    package = _package(tmp_path, modules)
    assert main(["--package-dir", str(package), "--budgets", str(_budgets(tmp_path, budgets))]) == 1
    errors = [line for line in capsys.readouterr().out.splitlines() if line.startswith("::error::")]
    assert len(errors) == 1
    assert message in errors[0]
