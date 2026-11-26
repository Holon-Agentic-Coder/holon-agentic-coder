from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sandbox_executor.harness_reference_patterns import HARNESS_PATTERNS, find_references, sample_reference

from scripts.check_no_harness_references import main

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github" / "workflows" / "test-no-harness-references.yml"
PLAN = REPO / "plans" / "I-1795735563-intent-scope-and-harness-refs" / "P-1795735599-CLA-CO5.5.md"
TOUCH_SET = (
    "apps/sandbox-executor/src/sandbox_executor/harness_reference_patterns.py",
    "apps/sandbox-executor/src/sandbox_executor/intent_payload_rules.py",
    "apps/sandbox-executor/src/sandbox_executor/intent_scope_check.py",
    "apps/sandbox-executor/src/sandbox_executor/cli/commands/intent_check.py",
    "apps/sandbox-executor/src/sandbox_executor/flow/intent_questions.py",
    "scripts/check_no_harness_references.py",
    ".github/workflows/test-no-harness-references.yml",
    "apps/sandbox-executor/tests/test_intent_payload_rules.py",
    "apps/sandbox-executor/tests/test_check_no_harness_references.py",
    "apps/sandbox-executor/tests/test_intent_scope_check.py",
    "apps/sandbox-executor/tests/cli/test_intent_check_cli.py",
    "apps/sandbox-executor/tests/flow/test_intent_questions.py",
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _commit(repo: Path, files: dict[str, str]) -> str:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "change")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-b", "main", "-q")
    _commit(path, {"docs/guide.txt": "one\ntwo\n"})
    return path


def _scan(repo: Path, head: str, monkeypatch: pytest.MonkeyPatch, body: str = "") -> int:
    monkeypatch.setenv("PR_BODY", body)
    return main(["--base", "main", "--head", head, "--repo", str(repo)])


@pytest.mark.parametrize("label", [pattern.label for pattern in HARNESS_PATTERNS])
def test_added_line_with_a_reference_fails_without_echoing_it(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], label: str
) -> None:
    sample = sample_reference(label)
    _git(repo, "checkout", "-q", "-b", "feature")
    head = _commit(repo, {"docs/guide.txt": f"one\n{sample}\ntwo\n", "src/clean.py": "VALUE = 1\n"})

    assert _scan(repo, head, monkeypatch) == 1
    out = capsys.readouterr().out
    assert f"docs/guide.txt:2: {label}" in out
    assert "src/clean.py" not in out
    assert sample not in out


def test_pull_request_body_with_a_reference_fails(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = sample_reference("task id")
    _git(repo, "checkout", "-q", "-b", "feature")
    head = _commit(repo, {"src/clean.py": "VALUE = 1\n"})

    assert _scan(repo, head, monkeypatch, body=f"Closes {sample}") == 1
    out = capsys.readouterr().out
    assert "pull request body: task id" in out
    assert sample not in out


def test_clean_diff_and_body_pass(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _git(repo, "checkout", "-q", "-b", "feature")
    head = _commit(repo, {"src/clean.py": "HOME_DIR = '~/.config/tool'\nYEAR = 2026\n"})

    assert _scan(repo, head, monkeypatch, body="Adds a clean module.") == 0


def test_removed_reference_lines_are_not_findings(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sample = sample_reference("scratch directory")
    _commit(repo, {"docs/old.txt": f"{sample}\n"})
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "docs" / "old.txt").unlink()
    head = _commit(repo, {})

    assert _scan(repo, head, monkeypatch) == 0


@pytest.mark.parametrize(("base", "head"), [("no-such-revision", "HEAD"), ("", "HEAD"), ("main", " ")])
def test_unreadable_diff_fails_closed(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], base: str, head: str
) -> None:
    monkeypatch.setenv("PR_BODY", "")
    assert main(["--base", base, "--head", head, "--repo", str(repo)]) == 2
    assert capsys.readouterr().out.startswith("cannot read the pull request diff")


def test_every_file_of_the_change_is_free_of_references() -> None:
    paths = [REPO / rel for rel in TOUCH_SET] + [PLAN]
    assert {path.relative_to(REPO).as_posix(): find_references(path.read_text("utf-8")) for path in paths} == {
        path.relative_to(REPO).as_posix(): [] for path in paths
    }


def test_workflow_runs_on_pull_requests_in_flow_form_and_passes_the_body_through_env() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "\non: pull_request\n" in text
    assert "\n  pull_request:" not in text
    assert "PR_BODY: ${{ github.event.pull_request.body }}" in text
    assert 'python scripts/check_no_harness_references.py --base "$BASE_SHA" --head "$HEAD_SHA"' in text
    assert "fetch-depth: 0" in text
