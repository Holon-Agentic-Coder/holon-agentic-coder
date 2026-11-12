"""CI's hygiene lint command is covered by the resolver's pre-push verification, or consciously excluded."""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import pytest
from sandbox_executor.entrypoint.review_markdown_gate import MarkdownGateOutcome

REPO_ROOT = Path(__file__).resolve().parents[3]

#: What the resolver runs before pushing: 'uv run task lint' plus the changed-Markdown Prettier gate (lint-md).
RESOLVER_COVERS = frozenset({"lint-md", "uv-task-lint"})
#: Hygiene checks the resolver deliberately skips, each with the reason.
NOT_RUN_BY_RESOLVER = {
    "lint-json": "needs jq, which the review-loop image does not ship",
    "lint-yaml": "validates YAML with Prettier across the whole tree; the resolver edits code and Markdown",
    "lint-shell": "needs shellcheck, which the review-loop image does not ship",
}


def hygiene_checks(makefile: str) -> set[str]:
    """Return the checks CI's ``make lint`` runs: its prerequisites and the Python ``uv run task lint`` call."""
    match = re.search(r"^lint:([^\n]*)\n((?:\t[^\n]*\n?)*)", makefile, flags=re.MULTILINE)
    assert match, "Makefile has no lint target"
    checks = set(match.group(1).split())
    if "uv run task lint" in match.group(2):
        checks.add("uv-task-lint")
    return checks


def uncovered_checks(checks: set[str]) -> set[str]:
    """Return the checks neither run by the resolver nor on the reviewed exclusion list."""
    return checks - RESOLVER_COVERS - set(NOT_RUN_BY_RESOLVER)


def test_every_hygiene_check_is_covered_or_excluded() -> None:
    checks = hygiene_checks((REPO_ROOT / "Makefile").read_text(encoding="utf-8"))
    assert uncovered_checks(checks) == set()
    assert checks >= RESOLVER_COVERS


def test_a_new_hygiene_check_must_be_covered_or_excluded() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    mutated = makefile.replace("lint: lint-json", "lint: lint-new lint-json", 1)
    assert uncovered_checks(hygiene_checks(mutated)) == {"lint-new"}


def test_hygiene_workflow_runs_make_lint() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "test-hygiene.yml").read_text(encoding="utf-8")
    assert re.search(r"run:\s*make lint\s*$", workflow, flags=re.MULTILINE)


def test_resolver_runs_the_same_lint_task_as_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    from sandbox_executor.entrypoint import review_loop

    tasks = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["taskipy"]["tasks"]
    assert "lint" in tasks
    order: list[str] = []

    def fake_run_cmd(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        order.append(" ".join(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    def fake_gate(*_: object) -> MarkdownGateOutcome:
        order.append("markdown-gate")
        return MarkdownGateOutcome()

    monkeypatch.setattr(review_loop, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(review_loop, "run_markdown_gate", fake_gate)
    review_loop._verify_before_push("/tmp")
    assert order[:2] == ["uv run task lint", "markdown-gate"]
