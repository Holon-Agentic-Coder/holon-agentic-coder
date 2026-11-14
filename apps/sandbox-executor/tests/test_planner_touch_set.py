"""The planner records the plan's touch set on its plans.jsonl row, and the lazy imports never cycle."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sandbox_executor.entrypoint import planner


def test_touch_set_field_with_the_section(tmp_path):
    plan = tmp_path / "P-1.md"
    plan.write_text(
        "# Plan\n\n## Touch Set\n\n- `src/a.py` symbols: run\n- `docs/`\n\n## Step 1: x\n", encoding="utf-8"
    )
    assert planner._touch_set_field(str(plan)) == {
        "touch_set": [{"path": "src/a.py", "symbols": ["run"]}, {"path": "docs/"}]
    }


def test_touch_set_field_without_the_section_is_null_with_a_warning(tmp_path, capsys):
    plan = tmp_path / "P-1.md"
    plan.write_text("# Plan\n\n## Step 1: x\n", encoding="utf-8")
    assert planner._touch_set_field(str(plan)) == {"touch_set": None}
    assert "no '## Touch Set' section" in capsys.readouterr().err


def test_touch_set_field_with_a_placeholder_is_an_empty_list(tmp_path):
    plan = tmp_path / "P-1.md"
    plan.write_text("## Touch Set\n\n- none\n", encoding="utf-8")
    assert planner._touch_set_field(str(plan)) == {"touch_set": []}


def test_touch_set_field_never_fails_the_plan(tmp_path, capsys):
    assert planner._touch_set_field(str(tmp_path / "missing.md")) == {"touch_set": None}
    assert "could not parse the plan's touch set" in capsys.readouterr().err


@pytest.mark.parametrize(
    "module", ["sandbox_executor.entrypoint.planner", "sandbox_executor.calibration", "sandbox_executor.flow.touch_set"]
)
def test_importing_first_in_a_fresh_interpreter_does_not_cycle(module):
    code = f"import {module}; from sandbox_executor.flow.touch_set import parse_touch_set_section"
    src_dir = str(Path(planner.__file__).resolve().parents[2])
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [src_dir, os.environ.get("PYTHONPATH")]))}
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, env=env, check=False
    )
    assert result.returncode == 0, result.stderr
