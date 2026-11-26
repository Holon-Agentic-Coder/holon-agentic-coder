"""The approval carry-over helpers: the decision, the append-only ACT-CARRY row and the consensus annotation.

The fingerprint is stubbed here (``tests/test_approval_fingerprint.py`` covers it with real git), so these cases run
no git and touch no repository, except the bookkeeping-only coverage cases, which commit in a ``tmp_path`` repository.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.review import approval_carry_over
from sandbox_executor.flow.review.approval_carry_over import (
    CarryOver,
    apply_carry_over,
    carry_approval_over,
    carry_over_message,
    carry_over_record,
    decide_carry_over,
    record_stage5_carry_over,
)
from sandbox_executor.flow.review.approval_coverage import BOOKKEEPING, UNKNOWN_CHANGE, approval_covers
from sandbox_executor.flow.review.approval_fingerprint import fingerprints_match, is_bookkeeping_only_move
from sandbox_executor.ledger import ActionRecord
from sandbox_executor.review_accounting import accounting_of
from sandbox_executor.review_report import _mergeability_word

from tests.flow.helpers import _git
from tests.leak_assertions import assert_no_leak, make_gh_probe

APPROVED = "a" * 40
MERGED = "b" * 40
FINGERPRINT = "pf2:" + "c" * 40
CARRY = CarryOver(APPROVED, MERGED, FINGERPRINT)


def _consensus(**overrides) -> dict:
    data = {
        "approved": True,
        "target_commit": APPROVED,
        "approval_fingerprint": FINGERPRINT,
        "base_branch": "main",
        "ci_checks": {"total": 3, "passed": 3, "failed": 0, "pending": 0},
        "mergeability": {"merge_state_status": "CLEAN", "mergeable": "MERGEABLE", "is_dirty": False},
    }
    return data | overrides


@pytest.fixture
def merged_fingerprint(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Stub the merged head's fingerprint as ``FINGERPRINT`` and record each call."""
    calls: list[tuple] = []

    def fake(repo_dir, head, base_branch):
        calls.append((repo_dir, head, base_branch))
        return FINGERPRINT

    monkeypatch.setattr(approval_carry_over, "approval_fingerprint_of", fake)
    return calls


def test_the_same_fingerprint_carries_the_approval(merged_fingerprint) -> None:
    assert decide_carry_over("/repo", _consensus(), MERGED, "main") == CARRY
    assert merged_fingerprint == [("/repo", MERGED, "main")]


@pytest.mark.parametrize(
    "overrides",
    [
        {"approval_fingerprint": None},  # A legacy approval: no fingerprint recorded.
        {"approval_fingerprint": "pf1:" + "d" * 40},  # The PR-own change differs.
        {"target_commit": ""},
    ],
)
def test_no_carry_over_without_a_matching_recorded_fingerprint(merged_fingerprint, overrides) -> None:
    assert decide_carry_over("/repo", _consensus(**overrides), MERGED, "main") is None


def test_no_carry_over_without_a_merged_head(merged_fingerprint) -> None:
    assert decide_carry_over("/repo", _consensus(), "", "main") is None
    assert merged_fingerprint == []


def test_an_unreadable_merged_fingerprint_never_carries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approval_carry_over, "approval_fingerprint_of", lambda *_: None)
    assert decide_carry_over("/repo", _consensus(), MERGED, "main") is None


def test_none_never_matches_none() -> None:
    assert fingerprints_match(None, None) is False


def test_the_record_is_a_valid_append_only_action_row() -> None:
    record = carry_over_record("feature/x", CARRY)

    ActionRecord.from_dict(record)  # Validates category, outcome and origin; raises on a bad row.
    assert record["action_id"].startswith("ACT-CARRY-")
    assert record["outcome"] == "success" and record["entry_type"] == "action"
    assert record["action_origin"] == "pr_review"
    assert record["labels"] == ["review", "conflict-gate", "approval-carry-over"]
    assert (record["approved_head"], record["merged_head"]) == (APPROVED, MERGED)
    assert record["approval_fingerprint"] == FINGERPRINT
    assert APPROVED in record["action"] and MERGED in record["action"] and FINGERPRINT in record["action"]


def test_the_record_free_text_is_redacted() -> None:
    secret = make_gh_probe()
    record = carry_over_record(f"feature/x token={secret}", CARRY)

    assert_no_leak(secret, record["action"])


def test_apply_never_touches_target_commit() -> None:
    consensus = _consensus()

    apply_carry_over(consensus, CARRY)

    assert consensus["target_commit"] == APPROVED
    assert (consensus["carried_from"], consensus["carried_to"]) == (APPROVED, MERGED)
    assert consensus["ci_checks"]["measured"] is False
    assert MERGED in consensus["ci_checks"]["reason"]
    assert _mergeability_word(consensus) == "UNMEASURED"
    assert MERGED in consensus["mergeability"]["reason"]


def test_the_message_names_both_shas_the_fingerprint_and_the_ci_wait() -> None:
    message = carry_over_message(CARRY)

    assert APPROVED in message and MERGED in message and FINGERPRINT in message
    assert f"CI on {MERGED} is still required" in message


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def approved_repo(tmp_path: Path, hermetic_git_config: str) -> tuple[Path, str]:
    """A repository on ``feature`` whose head (the approved head) carries the PR's code change."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, {"README.md": "base\n"}, "base")
    _git(repo, "checkout", "-q", "-b", "feature")
    return repo, _commit(repo, {"apps/x/pr.py": "x = 1\n"}, "pr change")


def _covering_context(repo: Path, **consensus: object) -> FlowContext:
    context = FlowContext(repo_dir=str(repo), execution_branch="feature", agent="a", model="m")
    payload = {"consensus": _consensus(base_branch="main") | consensus}
    context.stage_results["review"] = StageResult(stage=FlowStage.REVIEW, status=StageStatus.APPROVED, payload=payload)
    return context


def test_a_bookkeeping_commit_after_the_approval_is_covered(approved_repo) -> None:
    repo, approved = approved_repo
    head = _commit(repo, {"executions/E-1.md": "# record\n", "plans/I-1/P-2.md": "# plan\n"}, "bookkeeping")
    context = _covering_context(repo, target_commit=approved, approval_fingerprint=None)
    assert is_bookkeeping_only_move(str(repo), approved, head)
    assert approval_covers(context, str(repo), head)[:2] == (True, BOOKKEEPING)


def test_a_bookkeeping_move_counts_bookkeeping_churn_once_per_head(approved_repo) -> None:
    repo, approved = approved_repo
    head = _commit(repo, {"executions/E-1.md": "# record\n"}, "bookkeeping")
    context = _covering_context(repo, target_commit=approved)
    approval_covers(context, str(repo), head)
    approval_covers(context, str(repo), head)
    accounting = accounting_of(context)
    assert accounting is not None and accounting["non_defect_churn"]["bookkeeping"] == 1
    assert accounting["approval_carry_overs"] == 0


def test_a_code_file_under_a_bookkeeping_root_is_not_bookkeeping(approved_repo) -> None:
    repo, approved = approved_repo
    head = _commit(repo, {"plans/x.py": "print(1)\n", "executions/E-1.md": "# record\n"}, "plans code")
    coverage = approval_covers(_covering_context(repo, target_commit=approved), str(repo), head)
    assert not is_bookkeeping_only_move(str(repo), approved, head)
    assert coverage.how != BOOKKEEPING and not coverage.covers


def test_a_target_merge_with_code_falls_back_to_the_fingerprint(approved_repo) -> None:
    repo, approved = approved_repo
    _git(repo, "checkout", "-q", "main")
    _commit(repo, {"apps/x/main.py": "y = 2\n"}, "main code")
    _git(repo, "checkout", "-q", "feature")
    _git(repo, "merge", "-q", "--no-ff", "main", "-m", "merge main")
    head = _commit(repo, {"executions/E-1.md": "# record\n"}, "bookkeeping")
    coverage = approval_covers(_covering_context(repo, target_commit=approved), str(repo), head)
    assert not is_bookkeeping_only_move(str(repo), approved, head)
    assert coverage.how == UNKNOWN_CHANGE and not coverage.covers  # No origin/main here, so no current fingerprint.


def test_an_unapproved_consensus_never_covers(approved_repo) -> None:
    repo, approved = approved_repo
    head = _commit(repo, {"executions/E-1.md": "# record\n"}, "bookkeeping")
    coverage = approval_covers(_covering_context(repo, target_commit=approved, approved=False), str(repo), head)
    assert not coverage.covers


def test_git_failures_are_never_bookkeeping_only(approved_repo, tmp_path: Path) -> None:
    repo, approved = approved_repo
    assert not is_bookkeeping_only_move(str(repo), approved, approved)  # No change at all.
    assert not is_bookkeeping_only_move(str(repo), approved, "0" * 40)
    assert not is_bookkeeping_only_move(str(tmp_path / "missing"), approved, "b" * 40)


def test_a_target_merge_of_only_knowledge_and_execution_records_carries_over(approved_repo) -> None:
    from sandbox_executor.flow.review.approval_fingerprint import approval_fingerprint_of

    repo, approved = approved_repo
    _git(repo, "update-ref", "refs/remotes/origin/main", "main")
    recorded = approval_fingerprint_of(str(repo), approved, "main")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, {"holon-knowledge/kb/note.md": "learnt\n", "executions/E-9.md": "# other flow\n"}, "main bookkeeping")
    _git(repo, "update-ref", "refs/remotes/origin/main", "main")
    _git(repo, "checkout", "-q", "feature")
    _git(repo, "merge", "-q", "--no-ff", "main", "-m", "merge main")
    merged = _git(repo, "rev-parse", "HEAD")
    consensus = _consensus(target_commit=approved, approval_fingerprint=recorded)

    assert recorded is not None and recorded.startswith("pf2:")
    assert decide_carry_over(str(repo), consensus, merged, "main") == CarryOver(approved, merged, recorded)


def test_a_carry_over_counts_once_across_stage4_and_stage5(merged_fingerprint, monkeypatch, tmp_path: Path) -> None:
    from sandbox_executor.flow.review import records

    monkeypatch.setattr(records, "record_review_action", lambda *_args, **_kwargs: None)
    context = _covering_context(tmp_path, target_commit=APPROVED)
    assert carry_approval_over(context, str(tmp_path / "executions.jsonl"), _consensus(), "feature", MERGED)
    assert record_stage5_carry_over(context, "main", MERGED) == CARRY
    accounting = accounting_of(context)
    assert accounting is not None
    assert (accounting["approval_carry_overs"], accounting["non_defect_churn"]["target_merge"]) == (1, 1)
