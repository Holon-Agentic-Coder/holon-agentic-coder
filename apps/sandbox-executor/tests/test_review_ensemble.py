"""Tests for the reviewer ensemble: trailer parsing, reviewer turns, isolation and the unanimity consensus.

Every reviewer turn runs through fakes: a fake runner factory and a fake turn runner that writes chosen report text,
writes nothing, raises or reports a budget breach. No real runner, gh, git remote or Docker is touched.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from sandbox_executor import review_ensemble
from sandbox_executor.budgets import BudgetBreach, Budgets, StageTimer, TurnBudgets
from sandbox_executor.review_ensemble import (
    DEFAULT_ENSEMBLE_SIZE,
    TRAILER_CLOSE,
    TRAILER_OPEN,
    ConsensusVerdict,
    ParsedTrailer,
    ReviewCounts,
    ReviewerResult,
    ReviewerStatus,
    ReviewerVerdict,
    ReviewFinding,
    ReviewRequest,
    TrailerError,
    build_reviewer_prompt,
    decide_consensus,
    parse_review_trailer,
    render_review_trailer,
    reviewer_report_rel,
    run_ensemble,
    run_reviewer,
)
from sandbox_executor.turns import TurnResult

TEMPLATE_PATH = Path(__file__).resolve().parents[3] / "holon-config" / "prompts" / "review_loop.template.md"
SECRET = "ghp_" + "Z9" * 18

CLEAN = ParsedTrailer(ReviewerVerdict.APPROVED, ReviewCounts())
ONE_NIT = ParsedTrailer(
    ReviewerVerdict.CHANGES_REQUESTED,
    ReviewCounts(nit=1),
    (ReviewFinding("nit", "src/app.py", 3, "Rename the helper."),),
)


def report(body: str, parsed: ParsedTrailer = CLEAN) -> str:
    """A review report with ``body`` followed by the canonical trailer."""
    return f"# Review\n\n{body}\n\n{render_review_trailer(parsed)}\n"


def trailer(payload: str) -> str:
    """A report whose trailer block holds ``payload`` verbatim."""
    return f"# Review\n\nText.\n\n{TRAILER_OPEN}\n{payload}\n{TRAILER_CLOSE}\n"


# --- Fakes ---------------------------------------------------------------------------------------------------------


class FakeRunner:
    """Stands in for an ``AgentRunner``: the argv carries the prompt, and credential cleanups are counted."""

    def __init__(self) -> None:
        self.cleanups = 0
        self.built: list[tuple[str, str, str]] = []

    def build_cmd(self, model_name: str, prompt_file: str, intent_file: str, full_prompt: str) -> list[str]:
        self.built.append((model_name, prompt_file, intent_file))
        return ["fake-agent", full_prompt]

    def cleanup_ephemeral_credentials(self) -> None:
        self.cleanups += 1


@dataclass
class FakeRunnerFactory:
    """Returns one :class:`FakeRunner` per call, or raises ``SystemExit`` like ``get_runner`` for an unknown agent."""

    unknown: bool = False
    runners: list[FakeRunner] = field(default_factory=list)

    def __call__(self, agent: str) -> FakeRunner:
        if self.unknown:
            raise SystemExit(1)
        runner = FakeRunner()
        self.runners.append(runner)
        return runner


@dataclass
class Call:
    """What the fake turn runner saw on one call."""

    prompt: str
    watch_path: str
    existing_reports: list[str]


class FakeTurnRunner:
    """Plays one action per call: ``("write", text)``, ``("nothing",)``, ``("raise", exc)`` or ``("breach", text)``.

    Calls beyond the scripted actions write nothing (follow-up turns after a breach).
    """

    def __init__(self, repo_dir: Path, actions: Sequence[tuple[Any, ...]]) -> None:
        self.repo_dir = repo_dir
        self.actions = list(actions)
        self.calls: list[Call] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> TurnResult:
        assert {"watch_path", "cwd", "turn_budgets", "stage_timer", "stage"} <= set(kwargs)
        assert kwargs["cwd"] == str(self.repo_dir)
        self.calls.append(Call(cmd[-1], kwargs["watch_path"], self._existing_reports()))
        action = self.actions.pop(0) if self.actions else ("nothing",)
        return self._play(action, kwargs["watch_path"])

    def _existing_reports(self) -> list[str]:
        return sorted(str(path) for path in (self.repo_dir / ".holon" / "review").rglob("report.md"))

    def _play(self, action: tuple[Any, ...], watch_path: str) -> TurnResult:
        kind = action[0]
        if kind == "raise":
            raise action[1]
        if kind in ("write", "breach") and action[1] is not None:
            Path(watch_path).write_text(action[1], encoding="utf-8")
        breach = BudgetBreach("review", "turn", 300.0, 301.0) if kind == "breach" else None
        return TurnResult(returncode=0, breach=breach)


def make_request(repo_dir: Path, diff: str = "diff --git a/src/app.py b/src/app.py\n+print('hi')\n") -> ReviewRequest:
    return ReviewRequest(
        repo_dir=str(repo_dir),
        head_sha="abc1234",
        base_ref="main",
        pr_title="Add greeting",
        pr_body="Prints a greeting.",
        diff_text=diff,
        iteration=1,
    )


def seams(turn_runner: FakeTurnRunner, factory: FakeRunnerFactory | None = None) -> dict[str, Any]:
    """Fake seams with generous budgets built directly, never from the environment."""
    return {
        "template_text": TEMPLATE_PATH.read_text(encoding="utf-8"),
        "turn_runner": turn_runner,
        "runner_factory": factory or FakeRunnerFactory(),
        "budgets": Budgets(turn=TurnBudgets(turn_seconds=3600.0, stall_seconds=0.0)),
        "stage_timer": StageTimer("review", 3600.0),
    }


def reviewer(tmp_path: Path, actions: Sequence[tuple[Any, ...]], **extra: Any) -> tuple[ReviewerResult, Any]:
    turn_runner = FakeTurnRunner(tmp_path, actions)
    factory = extra.pop("factory", FakeRunnerFactory())
    result = run_reviewer(make_request(tmp_path), 1, "claude-agent", "model-x", **seams(turn_runner, factory))
    return result, factory


# --- Trailer -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("parsed", [CLEAN, ONE_NIT], ids=["clean", "one-nit"])
def test_trailer_round_trips(parsed: ParsedTrailer) -> None:
    assert parse_review_trailer(report("Body.", parsed)) == parsed


def test_trailer_tolerates_a_json_fence_and_trailing_whitespace() -> None:
    text = trailer('```json\n{"verdict": "APPROVED", "critical": 0, "important": 0, "nit": 0, "findings": []}\n```')
    assert parse_review_trailer(text + "\n\n   \n") == CLEAN


def _payload(**overrides: Any) -> str:
    obj: dict[str, Any] = {"verdict": "APPROVED", "critical": 0, "important": 0, "nit": 0, "findings": []}
    obj.update(overrides)
    return json.dumps({key: value for key, value in obj.items() if value is not ...})


_NIT = {"severity": "nit", "file": "a.py", "line": 1, "summary": "x"}

REJECTED = {
    "missing block": "# Review\n\nLooks good.\n",
    "missing closing marker": f"# Review\n\n{TRAILER_OPEN}\n{_payload()}\n",
    "missing opening marker": f"# Review\n\n{_payload()}\n{TRAILER_CLOSE}\n",
    "trailer not final": report("Body.") + "\nOne more thing.\n",
    "two closing markers": f"{TRAILER_OPEN}\n{_payload()}\n{TRAILER_CLOSE}\n{TRAILER_CLOSE}\n",
    "invalid json": trailer("{verdict: APPROVED"),
    "non-object json": trailer("[]"),
    "unknown verdict": trailer(_payload(verdict="LGTM")),
    "comment verdict": trailer(_payload(verdict="COMMENT")),
    "unknown key": trailer(_payload(extra=1)),
    "missing key": trailer(_payload(nit=...)),
    "negative count": trailer(_payload(verdict="CHANGES_REQUESTED", critical=-1)),
    "bool count": trailer(_payload(nit=True)),
    "string count": trailer(_payload(nit="0")),
    "counts disagree with findings": trailer(_payload(verdict="CHANGES_REQUESTED", nit=2, findings=[_NIT])),
    "finding not counted": trailer(_payload(verdict="CHANGES_REQUESTED", findings=[_NIT])),
    "unknown severity": trailer(_payload(verdict="CHANGES_REQUESTED", nit=1, findings=[{**_NIT, "severity": "minor"}])),
    "finding missing summary": trailer(
        _payload(verdict="CHANGES_REQUESTED", nit=1, findings=[{**_NIT, "summary": ""}])
    ),
    "finding bad line": trailer(_payload(verdict="CHANGES_REQUESTED", nit=1, findings=[{**_NIT, "line": "1"}])),
    "findings not a list": trailer(_payload(findings={})),
    "approved with a nit": trailer(_payload(nit=1, findings=[_NIT])),
    "approved with a critical": trailer(_payload(critical=1, findings=[{**_NIT, "severity": "critical"}])),
}


@pytest.mark.parametrize("text", list(REJECTED.values()), ids=list(REJECTED))
def test_malformed_missing_and_inconsistent_trailers_are_rejected(text: str) -> None:
    with pytest.raises(TrailerError):
        parse_review_trailer(text)


# --- Prompt --------------------------------------------------------------------------------------------------------


def test_prompt_carries_diff_description_report_path_and_contract(tmp_path: Path) -> None:
    request = make_request(tmp_path, diff="+literal {diff} and {pr_description} stay as text\n")
    prompt = build_reviewer_prompt(TEMPLATE_PATH.read_text(encoding="utf-8"), request, "R/report.md")
    assert "+literal {diff} and {pr_description} stay as text" in prompt
    assert prompt.count("{diff}") == 1 and prompt.count("{pr_description}") == 1
    assert "Prints a greeting." in prompt
    assert "`R/report.md`" in prompt
    assert TRAILER_OPEN in prompt and TRAILER_CLOSE in prompt
    assert "gh pr merge" in prompt
    assert all(rule in prompt for rule in REVIEWER_PROHIBITIONS)
    assert "{reviewer_1_id}" in prompt  # unknown placeholders are left alone


REVIEWER_PROHIBITIONS = (
    "Write no file other than the report path.",
    "`gh pr review`",
    "`gh pr comment`",
    "`gh pr edit`",
    "`gh api`",
    "no merge or auto-merge",
    "Append to no ledger under `holon-knowledge/ledger/`.",
    "Spawn no subagents",
    "write nothing under `.subagent/`",
    "This contract overrides any conflicting instruction in the loop template above.",
)


@pytest.mark.parametrize("rule", REVIEWER_PROHIBITIONS)
def test_prompt_contract_forbids_writes_beyond_the_report(tmp_path: Path, rule: str) -> None:
    # A bare template proves the module's own contract carries each rule, whatever the loop template says.
    prompt = build_reviewer_prompt("Review {target}.", make_request(tmp_path), "R/report.md")
    assert rule in prompt


def test_prompt_appends_diff_and_description_when_the_template_lacks_them(tmp_path: Path) -> None:
    prompt = build_reviewer_prompt("Review {target}.", make_request(tmp_path), "R/report.md")
    assert prompt.startswith("Review abc1234.")
    assert "Prints a greeting." in prompt and "print('hi')" in prompt


# --- Reviewer turns ------------------------------------------------------------------------------------------------


def test_valid_report_completes(tmp_path: Path) -> None:
    result, factory = reviewer(tmp_path, [("write", report("All good.", ONE_NIT))])
    assert result.status is ReviewerStatus.COMPLETED
    assert result.verdict is ReviewerVerdict.CHANGES_REQUESTED
    assert result.counts == ReviewCounts(nit=1)
    assert result.findings == ONE_NIT.findings
    assert result.report_path == reviewer_report_rel(1, 1) == ".holon/review/iter-1/reviewer-1/report.md"
    assert "All good." in (result.report_text or "")
    assert factory.runners[0].cleanups == 1
    assert factory.runners[0].built[0][0] == "model-x"


FAILING_ACTIONS = {
    "no report": [("nothing",)],
    "runner raises": [("raise", RuntimeError("agent crashed"))],
    "budget breach": [("breach", None)],
    "breach salvaged by follow-up": [("breach", "# Partial\n"), ("write", report("Salvaged."))],
    "unparseable report": [("write", trailer(_payload(verdict="LGTM")))],
    "report without trailer": [("write", "# Review\n\nLooks fine to me, approved.\n" * 3)],
}


@pytest.mark.parametrize("actions", list(FAILING_ACTIONS.values()), ids=list(FAILING_ACTIONS))
def test_failures_give_failed_without_verdict(tmp_path: Path, actions: list[tuple[Any, ...]]) -> None:
    result, factory = reviewer(tmp_path, actions)
    assert result.status is ReviewerStatus.FAILED
    assert result.verdict is None
    assert result.failure_reason
    assert factory.runners[0].cleanups == 1


def test_failure_reason_is_redacted(tmp_path: Path) -> None:
    result, _ = reviewer(tmp_path, [("raise", RuntimeError(f"token was: {SECRET}"))])
    assert result.status is ReviewerStatus.FAILED
    assert SECRET not in (result.failure_reason or "")


def test_unknown_agent_fails_the_reviewer_instead_of_exiting(tmp_path: Path) -> None:
    result, factory = reviewer(tmp_path, [], factory=FakeRunnerFactory(unknown=True))
    assert result.status is ReviewerStatus.FAILED
    assert "SystemExit" in (result.failure_reason or "")
    assert factory.runners == []


def test_failed_constructor_never_carries_a_verdict() -> None:
    result = ReviewerResult.failed(1, "a", "m", "r", "x" * 2000)
    assert result.verdict is None and result.counts.total == 0
    assert len(result.failure_reason or "") <= 500


# --- Ensemble isolation --------------------------------------------------------------------------------------------


def test_no_reviewer_sees_another_reviewers_report(tmp_path: Path) -> None:
    marker = "DISTINCTIVE-REPORT-TEXT-{}"
    actions = [("write", report(marker.format(index))) for index in (1, 2, 3)]
    turn_runner = FakeTurnRunner(tmp_path, actions)
    results = run_ensemble(make_request(tmp_path), 3, "claude-agent", "model-x", **seams(turn_runner))

    assert [result.status for result in results] == [ReviewerStatus.COMPLETED] * 3
    for call in turn_runner.calls:
        assert "DISTINCTIVE-REPORT-TEXT" not in call.prompt
        assert call.existing_reports == []
    assert [Path(call.watch_path).parent.name for call in turn_runner.calls] == [
        "reviewer-1",
        "reviewer-2",
        "reviewer-3",
    ]
    assert [marker.format(index) in (result.report_text or "") for index, result in enumerate(results, 1)] == [True] * 3
    assert not list((tmp_path / ".holon" / "review").rglob("*.md"))


def test_stale_reports_from_an_earlier_run_are_removed_first(tmp_path: Path) -> None:
    stale = tmp_path / ".holon" / "review" / "iter-1" / "reviewer-2" / "report.md"
    stale.parent.mkdir(parents=True)
    stale.write_text(report("STALE"), encoding="utf-8")
    turn_runner = FakeTurnRunner(tmp_path, [("write", report("fresh"))])
    run_ensemble(make_request(tmp_path), 1, "claude-agent", "model-x", **seams(turn_runner))
    assert turn_runner.calls[0].existing_reports == []
    assert not stale.exists()


def test_failed_reviewer_does_not_stop_the_ensemble(tmp_path: Path) -> None:
    actions = [("write", report("one")), ("raise", RuntimeError("boom")), ("write", report("three"))]
    results = run_ensemble(
        make_request(tmp_path), 3, "claude-agent", "model-x", **seams(FakeTurnRunner(tmp_path, actions))
    )
    assert [result.status for result in results] == [
        ReviewerStatus.COMPLETED,
        ReviewerStatus.FAILED,
        ReviewerStatus.COMPLETED,
    ]
    assert decide_consensus(results, 3, mergeable=True, ci_green=True) is ConsensusVerdict.FAILED


def test_unremovable_earlier_report_fails_the_remaining_reviewers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_remove = review_ensemble._remove_scratch
    first_scratch = os.path.dirname(reviewer_report_rel(1, 1))
    turn_runner = FakeTurnRunner(tmp_path, [("write", report(f"Clean {index}.")) for index in (1, 2, 3)])

    def remove_scratch(repo_dir: str, rel: str) -> None:
        # Reviewer 1 prepares its own directory first; only removing it after its turn fails.
        if rel == first_scratch and turn_runner.calls:
            raise OSError("permission denied")
        real_remove(repo_dir, rel)

    monkeypatch.setattr(review_ensemble, "_remove_scratch", remove_scratch)
    results = run_ensemble(make_request(tmp_path), 3, "claude-agent", "model-x", **seams(turn_runner))

    assert len(turn_runner.calls) == 1
    assert [result.status for result in results] == [
        ReviewerStatus.COMPLETED,
        ReviewerStatus.FAILED,
        ReviewerStatus.FAILED,
    ]
    assert all("isolation" in (result.failure_reason or "") for result in results[1:])
    assert decide_consensus(results, 3, mergeable=True, ci_green=True) is ConsensusVerdict.FAILED


def test_zero_size_dispatches_nothing(tmp_path: Path) -> None:
    turn_runner = FakeTurnRunner(tmp_path, [])
    assert run_ensemble(make_request(tmp_path), 0, "claude-agent", "model-x", **seams(turn_runner)) == []
    assert turn_runner.calls == []


def test_three_clean_reviewers_end_to_end_approve(tmp_path: Path) -> None:
    actions = [("write", report(f"Clean {index}.")) for index in (1, 2, 3)]
    factory = FakeRunnerFactory()
    results = run_ensemble(
        make_request(tmp_path),
        DEFAULT_ENSEMBLE_SIZE,
        "claude-agent",
        "model-x",
        **seams(FakeTurnRunner(tmp_path, actions), factory),
    )
    assert [runner.cleanups for runner in factory.runners] == [1, 1, 1]
    assert decide_consensus(results, DEFAULT_ENSEMBLE_SIZE, mergeable=True, ci_green=True) is ConsensusVerdict.APPROVED


# --- Consensus -----------------------------------------------------------------------------------------------------


def completed(index: int, parsed: ParsedTrailer = CLEAN) -> ReviewerResult:
    return ReviewerResult.completed(index, "a", "m", f"r{index}", "text", parsed)


def failed(index: int) -> ReviewerResult:
    return ReviewerResult.failed(index, "a", "m", f"r{index}", "invalid review trailer")


CLEAN_THREE = [completed(1), completed(2), completed(3)]
CHANGES = ParsedTrailer(ReviewerVerdict.CHANGES_REQUESTED, ReviewCounts())

CONSENSUS_CASES = {
    "three clean reviewers approve": (CLEAN_THREE, 3, True, True, ConsensusVerdict.APPROVED),
    "one failed reviewer blocks": ([completed(1), failed(2), completed(3)], 3, True, True, ConsensusVerdict.FAILED),
    "all failed cannot approve": ([failed(1), failed(2), failed(3)], 3, True, True, ConsensusVerdict.FAILED),
    "changes requested blocks": (
        [completed(1), completed(2, CHANGES), completed(3)],
        3,
        True,
        True,
        ConsensusVerdict.CHANGES_REQUESTED,
    ),
    "a single nit blocks": (
        [completed(1), completed(2), completed(3, ONE_NIT)],
        3,
        True,
        True,
        ConsensusVerdict.CHANGES_REQUESTED,
    ),
    "zero dispatched": ([], 0, True, True, ConsensusVerdict.FAILED),
    "zero dispatched of three": ([], 3, True, True, ConsensusVerdict.FAILED),
    "fewer results than configured": (CLEAN_THREE[:2], 3, True, True, ConsensusVerdict.FAILED),
    "more results than configured": (CLEAN_THREE, 2, True, True, ConsensusVerdict.FAILED),
    "not mergeable": (CLEAN_THREE, 3, False, True, ConsensusVerdict.CHANGES_REQUESTED),
    "mergeability unknown": (CLEAN_THREE, 3, None, True, ConsensusVerdict.CHANGES_REQUESTED),
    "red ci": (CLEAN_THREE, 3, True, False, ConsensusVerdict.CHANGES_REQUESTED),
    "ci unknown": (CLEAN_THREE, 3, True, None, ConsensusVerdict.CHANGES_REQUESTED),
}


@pytest.mark.parametrize(
    ("results", "size", "mergeable", "ci_green", "expected"), list(CONSENSUS_CASES.values()), ids=list(CONSENSUS_CASES)
)
def test_decide_consensus(
    results: list[ReviewerResult], size: int, mergeable: bool | None, ci_green: bool | None, expected: ConsensusVerdict
) -> None:
    assert decide_consensus(results, size, mergeable, ci_green) is expected


def test_one_unparseable_reviewer_blocks(tmp_path: Path) -> None:
    actions = [("write", report("one")), ("write", trailer("{not json")), ("write", report("three"))]
    results = run_ensemble(
        make_request(tmp_path), 3, "claude-agent", "model-x", **seams(FakeTurnRunner(tmp_path, actions))
    )
    assert results[1].status is ReviewerStatus.FAILED
    assert decide_consensus(results, 3, mergeable=True, ci_green=True) is ConsensusVerdict.FAILED


def test_reviewer_paths_reject_non_integer_indices(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        reviewer_report_rel("../../x", 1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        reviewer_report_rel(1, True)
    assert not os.path.exists(tmp_path / ".holon")
