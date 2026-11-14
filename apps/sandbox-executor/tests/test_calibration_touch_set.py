"""Stage 5 touch-set accuracy: scoring from the plan row and the report section (calibration_touch_set.py)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.calibration import PredictedMetrics, generate_calibration, parse_actual_metrics
from sandbox_executor.calibration_touch_set import touch_set_metrics, touch_set_report_lines

_EXEC_BRANCH = "I-1-ts/P-2/E-3/_"
_NONE = {"precision": None, "recall": None, "unpredicted_paths": [], "unused_entries": []}


def test_scores_a_plan_row_with_a_touch_set():
    plan_row = {"plan_id": "P-2", "touch_set": [{"path": "src/a.py"}, {"path": "docs/"}, {"path": "src/b.py"}]}
    score = touch_set_metrics(plan_row, ["src/a.py", "docs/x.md", "src/c.py", "plans/P-2.md"], ".")
    assert score == {
        "precision": pytest.approx(2 / 3),
        "recall": pytest.approx(2 / 3),
        "unpredicted_paths": ["src/c.py"],
        "unused_entries": ["src/b.py"],
    }


def test_historic_row_without_a_touch_set_scores_none():
    assert touch_set_metrics({"plan_id": "P-2", "p_success": 0.9}, ["src/a.py"], ".") == _NONE


def test_malformed_row_scores_none():
    assert touch_set_metrics({"plan_id": "P-2", "touch_set": "src/a.py"}, ["src/a.py"], ".") == _NONE
    assert touch_set_metrics({"plan_id": "P-2", "touch_set": [{"symbols": []}]}, ["src/a.py"], ".") == _NONE


def test_markdown_fallback_parses_the_plan_file(tmp_path):
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "P-2.md").write_text("## Touch Set\n\n- `src/a.py`\n", encoding="utf-8")
    score = touch_set_metrics({"plan_file": "plans/P-2.md"}, ["src/a.py"], str(tmp_path))
    assert (score["precision"], score["recall"]) == (1.0, 1.0)


def test_unreadable_plan_file_scores_none(tmp_path):
    assert touch_set_metrics({"plan_file": "plans/missing.md"}, ["src/a.py"], str(tmp_path)) == _NONE


def test_report_lines_with_values_cap_long_lists():
    score = {
        "precision": 0.5,
        "recall": 0.25,
        "unpredicted_paths": [f"src/m{i}.py" for i in range(23)],
        "unused_entries": ["docs/"],
    }
    text = "\n".join(touch_set_report_lines({"touch_set": score}))
    assert "## Touch Set Accuracy" in text
    assert "`50.0%`" in text
    assert "`25.0%`" in text
    assert "- `src/m19.py`" in text
    assert "src/m20.py" not in text
    assert "- and 3 more" in text
    assert "- `docs/`" in text


def test_report_lines_without_a_touch_set_show_na():
    lines = touch_set_report_lines({"touch_set": _NONE})
    assert lines.count("## Touch Set Accuracy") == 1
    assert sum("n/a" in line for line in lines) == 2
    assert not any("outside the touch set" in line for line in lines)
    assert touch_set_report_lines({}) == lines


def test_changed_paths_exclude_metadata():
    numstat = "3\t0\tholon-knowledge/ledger/executions.jsonl\n42\t0\texecutions/E-3.md\n20\t2\tsrc/a.py\n"

    def fake_run(cmd, **kwargs):
        if "diff" in cmd:
            return MagicMock(returncode=0, stdout=numstat, stderr="")
        return MagicMock(returncode=1, stdout="", stderr="")

    with (
        patch("os.path.exists", return_value=False),
        patch("sandbox_executor.calibration.read_git_file", return_value=None),
        patch("subprocess.run", side_effect=fake_run),
    ):
        _, meta = parse_actual_metrics("E-3", "", PredictedMetrics())
    assert meta["changed_paths"] == ["src/a.py"]


@pytest.fixture
def touch_set_repo(tmp_path, monkeypatch):
    """A throwaway repository whose plan row declares a touch set and whose execution branch changes two files."""
    for key in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{key}_NAME", "Touch Set Test")
        monkeypatch.setenv(f"GIT_{key}_EMAIL", "touch-set-test@example.invalid")

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True)

    def write(rel: str, text: str) -> None:
        path = Path(tmp_path, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    git("init", "-q", "-b", "main")
    plan_row = {"plan_id": "P-2", "intent_branch": "I-1-ts", "touch_set": [{"path": "src/a.py"}, {"path": "docs/"}]}
    write("holon-knowledge/ledger/plans.jsonl", json.dumps(plan_row) + "\n")
    summary = {"execution_id": "E-3", "plan_branch": "I-1-ts/P-2/_", "entry_type": "summary", "exit_code": 0}
    write("holon-knowledge/ledger/executions.jsonl", json.dumps(summary) + "\n")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    git("checkout", "-q", "-b", "I-1-ts/P-2/_")
    git("checkout", "-q", "-b", _EXEC_BRANCH)
    write("src/a.py", "print('a')\n")
    write("src/extra.py", "print('extra')\n")
    write("executions/E-3.md", "# notes\n")
    git("add", "-A")
    git("commit", "-q", "-m", "feature")
    return tmp_path


def test_generate_calibration_reports_touch_set_accuracy(touch_set_repo):
    report = generate_calibration(_EXEC_BRANCH, target_worktree=str(touch_set_repo))
    assert report.touch_set == {
        "precision": 0.5,
        "recall": 0.5,
        "unpredicted_paths": ["src/extra.py"],
        "unused_entries": ["docs/"],
    }
    assert report.to_dict()["touch_set"] == report.touch_set
    assert "## Touch Set Accuracy" in report.markdown_content
    assert "- `src/extra.py`" in report.markdown_content
