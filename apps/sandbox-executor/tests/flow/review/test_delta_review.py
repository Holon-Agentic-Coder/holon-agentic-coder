"""Tests for scoped delta review: the limits, the fail-closed mode decision, the record, the request and forwarding.

Every repository is a fresh one under ``tmp_path``; nothing reads or writes this checkout.
"""

from __future__ import annotations

import json
import logging
import string
from pathlib import Path

import pytest
from sandbox_executor.flow import FlowContext
from sandbox_executor.flow.review.delta_review import (
    SCOPED_CHAIN_LIMIT,
    addressed_findings,
    adopt_reviewed_head,
    begin_review_pass,
    current_decision,
    decide_review_mode,
    delta_state_of,
    end_review_pass,
    record_addressed_findings,
    set_current,
)
from sandbox_executor.flow.review.delta_review_forwarding import (
    FORWARDED_REL,
    discard_forwarded_decision,
    forward_scoped_decision,
    load_forwarded_decision,
)
from sandbox_executor.flow.review.delta_review_limits import (
    resolve_scoped_review_context_bytes,
    resolve_scoped_review_max_lines,
)
from sandbox_executor.flow.review.delta_review_mode import FULL, SCOPED, ReviewMode, decision_from_dict
from sandbox_executor.flow.review.delta_review_request import build_delta_request
from sandbox_executor.review_accounting import accounting_of, restart_review_passes
from sandbox_executor.review_report import FindingRow
from sandbox_executor.review_request import ScopedDelta, format_delta_body
from sandbox_executor.review_target import ReviewTarget

from tests import seeded_secrets
from tests.flow.helpers import _git
from tests.review_fakes import clean_reviewer, failed_reviewer, objecting_reviewer

APP = "src/app.py"
H1, H2 = "1" * 40, "2" * 40


def _commit(repo: Path, files: dict[str, str | bytes], message: str = "change") -> str:
    """Write ``files``, commit them and return the new head."""
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository on ``main`` whose first commit has a ten-line ``src/app.py``."""
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _commit(work, {APP: "".join(f"line {n}\n" for n in range(10))}, "base")
    return work


def _reviewed(repo: Path, chain: int = 0) -> FlowContext:
    """A context whose last completed full pass reviewed the current head of ``repo``."""
    context = FlowContext(repo_dir=str(repo))
    head = _git(repo, "rev-parse", "HEAD")
    delta_state_of(context).update(last_reviewed_head=head, anchor_head=head, scoped_chain=chain, last_critical=False)
    return context


def _decide(context: FlowContext, repo: Path, env: dict[str, str] | None = None) -> ReviewMode:
    return decide_review_mode(context, str(repo), _git(repo, "rev-parse", "HEAD"), env=env or {})


# --- limits ----------------------------------------------------------------------------------------------------------


def test_limits_default_when_unset():
    assert resolve_scoped_review_max_lines({}) == 400
    assert resolve_scoped_review_context_bytes({}) == 40000


def test_max_lines_zero_disables():
    assert resolve_scoped_review_max_lines({"HOLON_SCOPED_REVIEW_MAX_LINES": "0"}) == 0


@pytest.mark.parametrize("raw", ["many", "-1", "1.5"])
def test_invalid_max_lines_warns_and_keeps_the_default(raw, caplog):
    with caplog.at_level(logging.WARNING):
        assert resolve_scoped_review_max_lines({"HOLON_SCOPED_REVIEW_MAX_LINES": raw}) == 400
    assert "HOLON_SCOPED_REVIEW_MAX_LINES" in caplog.text


@pytest.mark.parametrize("raw", ["0", "999", "-5", "lots"])
def test_invalid_context_bytes_warns_and_keeps_the_default(raw, caplog):
    with caplog.at_level(logging.WARNING):
        assert resolve_scoped_review_context_bytes({"HOLON_SCOPED_REVIEW_CONTEXT_BYTES": raw}) == 40000
    assert "HOLON_SCOPED_REVIEW_CONTEXT_BYTES" in caplog.text


# --- the decision ----------------------------------------------------------------------------------------------------


def test_small_descendant_delta_is_scoped(repo):
    context = _reviewed(repo)
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, {APP: "line 0\nchanged\n"}, "fix")
    decision = _decide(context, repo)
    assert (decision.mode, decision.base, decision.head, decision.paths) == (SCOPED, base, head, (APP,))
    assert decision.reason == f"delta {decision.delta_lines} lines"


def test_zero_limit_disables_scoped_review(repo):
    context = _reviewed(repo)
    _commit(repo, {APP: "fixed\n"})
    assert _decide(context, repo, {"HOLON_SCOPED_REVIEW_MAX_LINES": "0"}).reason == "disabled"


def test_unknown_last_reviewed_head_is_full(repo):
    decision = _decide(FlowContext(repo_dir=str(repo)), repo)
    assert (decision.mode, decision.reason) == (FULL, "last reviewed head unknown")


def test_missing_anchor_is_full(repo):
    context = _reviewed(repo)
    delta_state_of(context)["anchor_head"] = ""
    _commit(repo, {APP: "fixed\n"})
    assert _decide(context, repo).reason == "last reviewed head unknown"


def test_unmoved_head_is_full(repo):
    assert _decide(_reviewed(repo), repo).reason == "head not moved"


def test_rewritten_history_is_full(repo):
    _commit(repo, {APP: "first\n"}, "reviewed")
    context = _reviewed(repo)
    (repo / APP).write_text("amended\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-a", "--amend", "-m", "amended")
    assert _decide(context, repo).reason == "history rewritten"


def test_chain_limit_forces_full(repo):
    context = _reviewed(repo, chain=SCOPED_CHAIN_LIMIT)
    _commit(repo, {APP: "fixed\n"})
    assert _decide(context, repo).reason == "scoped chain limit"


def _merge_side_branch(repo: Path) -> None:
    """Merge a side branch (as a target-branch merge would) into ``main`` with a merge commit."""
    _git(repo, "checkout", "-q", "-b", "side", "HEAD~1")
    _commit(repo, {"lib/other.py": "y = 1\n"}, "target change")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge target", "side")


def test_target_merge_in_delta_is_full(repo):
    base = _commit(repo, {"src/base.py": "b = 1\n"}, "reviewed")
    context = _reviewed(repo)
    _merge_side_branch(repo)
    _commit(repo, {APP: "fixed\n"})
    assert _git(repo, "merge-base", "--is-ancestor", base, "HEAD") == ""
    assert _decide(context, repo).reason == "target merge in delta"


def test_a_fix_on_an_adopted_merged_head_is_scoped(repo):
    _commit(repo, {"src/base.py": "b = 1\n"}, "reviewed")
    context = _reviewed(repo)
    delta_state_of(context).update(scoped_chain=2)
    _merge_side_branch(repo)
    merged = _git(repo, "rev-parse", "HEAD")
    adopt_reviewed_head(context, merged)  # The diff approval holds on the merged head (same PR-own change).
    assert delta_state_of(context)["anchor_head"] == merged and delta_state_of(context)["scoped_chain"] == 0
    assert delta_state_of(context)["last_critical"] is False
    _commit(repo, {APP: "fixed\n"}, "fix the CI defect")
    decision = _decide(context, repo)
    assert decision.mode == SCOPED and decision.paths == (APP,)


def test_an_agent_resolved_merge_scope_restricts_the_delta(repo):
    _commit(repo, {"src/base.py": "b = 1\n"}, "reviewed")
    context = _reviewed(repo)
    context.review_scope_paths = ["src/base.py"]
    _merge_side_branch(repo)
    _commit(repo, {APP: "fixed\n"})
    decision = _decide(context, repo)
    assert decision.mode == SCOPED
    assert decision.paths == (APP,)  # The side branch's lib/other.py stays out; src/base.py did not change.


def test_bookkeeping_only_delta_is_full(repo):
    context = _reviewed(repo)
    _commit(repo, {"plans/P-1.md": "# plan\n", "holon-knowledge/ledger/executions.jsonl": "{}\n"})
    assert _decide(context, repo).reason == "no reviewable change"


def test_delta_one_line_over_the_limit_is_full_and_at_the_limit_scoped(repo):
    context = _reviewed(repo)
    _commit(repo, {APP: (repo / APP).read_text(encoding="utf-8") + "a\nb\nc\nd\ne\n"})
    assert _decide(context, repo, {"HOLON_SCOPED_REVIEW_MAX_LINES": "4"}).reason == "delta 5 lines over limit 4"
    assert _decide(context, repo, {"HOLON_SCOPED_REVIEW_MAX_LINES": "5"}).mode == SCOPED


def test_binary_change_counts_as_over_the_limit(repo):
    context = _reviewed(repo)
    _commit(repo, {"assets/blob.bin": b"\x00\x01\x02binary"})
    assert _decide(context, repo).reason == "delta binary lines over limit 400"


@pytest.mark.parametrize("last_critical", [True, None, "no"])
def test_critical_or_unrecorded_severity_on_the_last_pass_is_full(repo, last_critical):
    context = _reviewed(repo)
    delta_state_of(context)["last_critical"] = last_critical
    _commit(repo, {APP: "line 0\nfixed\n"})
    assert _decide(context, repo).reason == "critical finding on last pass"


def test_unknown_commit_fails_closed(repo):
    context = _reviewed(repo)
    delta_state_of(context)["last_reviewed_head"] = "f" * 40
    assert _decide(context, repo).reason == "delta check failed"


def test_begin_review_pass_logs_and_records_the_mode(repo):
    context = FlowContext(repo_dir=str(repo))
    begin_review_pass(context, str(repo), _git(repo, "rev-parse", "HEAD"), 4)
    assert current_decision(context).mode == FULL
    assert any("Review mode at iteration 4: full (last reviewed head unknown)." in line for line in context.logs)


def test_begin_review_pass_counts_full_and_scoped_passes_once_per_head_and_label(repo):
    context = _reviewed(repo)
    begin_review_pass(context, str(repo), _git(repo, "rev-parse", "HEAD"), 1)
    head = _commit(repo, {APP: "line 0\nchanged\n"}, "fix")
    begin_review_pass(context, str(repo), head, 2)
    begin_review_pass(context, str(repo), head, 2)  # A resumed pass with the same label.
    accounting = accounting_of(context)
    assert (accounting["full_reviews"], accounting["scoped_reviews"]) == (1, 1)


def test_a_pass_after_a_review_restart_counts_again_at_the_same_head_and_label(repo):
    """A consensus withdrawal or scoped restart resets the label to 1: the same head and label is a new pass."""
    context, head = FlowContext(repo_dir=str(repo)), _git(repo, "rev-parse", "HEAD")
    begin_review_pass(context, str(repo), head, 1)
    context.review_iterations_done = 1
    restart_review_passes(context)
    begin_review_pass(context, str(repo), head, 1)
    begin_review_pass(context, str(repo), head, 1)  # A resumed pass after the restart.
    assert accounting_of(context)["full_reviews"] == 2
    assert (context.review_iterations_done, context.carried_review_findings) == (0, [])


# --- the record ------------------------------------------------------------------------------------------------------


def test_failed_reviewer_clears_the_last_reviewed_head(repo):
    context = _reviewed(repo)
    end_review_pass(context, "h2", [clean_reviewer(1), failed_reviewer(2)], 2)
    assert delta_state_of(context)["last_reviewed_head"] == ""


def test_missing_reviewer_clears_the_last_reviewed_head(repo):
    context = _reviewed(repo)
    end_review_pass(context, "h2", [clean_reviewer(1)], 2)
    assert delta_state_of(context)["last_reviewed_head"] == ""


def test_full_pass_anchors_and_resets_the_chain_and_scoped_pass_extends_it():
    context = FlowContext(repo_dir=".")
    delta_state_of(context).update(scoped_chain=2, addressed=[{"id": "X"}])
    set_current(context, ReviewMode(FULL, "head not moved", H1))
    end_review_pass(context, H1, [clean_reviewer(1)], 1)
    assert {k: delta_state_of(context)[k] for k in ("anchor_head", "scoped_chain", "last_reviewed_head")} == {
        "anchor_head": H1,
        "scoped_chain": 0,
        "last_reviewed_head": H1,
    }
    assert delta_state_of(context)["addressed"] == []
    set_current(context, ReviewMode(SCOPED, "delta 1 lines", H2, H1, (APP,), 1))
    end_review_pass(context, H2, [clean_reviewer(1)], 1)
    assert (delta_state_of(context)["anchor_head"], delta_state_of(context)["scoped_chain"]) == (H1, 1)


def test_end_review_pass_records_a_critical_finding():
    context = FlowContext(repo_dir=".")
    end_review_pass(context, "h1", [clean_reviewer(1), objecting_reviewer(2, "critical")], 2)
    assert delta_state_of(context)["last_critical"] is True
    end_review_pass(context, "h2", [clean_reviewer(1), objecting_reviewer(2, "important")], 2)
    assert delta_state_of(context)["last_critical"] is False


def test_addressed_findings_are_bounded_and_redacted():
    context = FlowContext(repo_dir=".")
    rows = [FindingRow(f"F-{n}", "important", APP, n, (1,), "s" * 400, "fix it") for n in range(60)]
    record_addressed_findings(context, rows)
    stored = delta_state_of(context)["addressed"]
    assert len(stored) == 50 and len(stored[0]["summary"]) == 300
    assert stored[0] == {"id": "F-0", "severity": "important", "path": APP, "line": 0, "summary": "s" * 300}


# --- the request -----------------------------------------------------------------------------------------------------


def _fake_token() -> str:
    rng = seeded_secrets.generator("delta-review:context-file")
    return "gh" + "p_" + "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(36))


def _scoped_request(repo: Path, context: FlowContext, iteration: int = 2):
    head = _git(repo, "rev-parse", "HEAD")
    decision = decide_review_mode(context, str(repo), head, env={})
    assert decision.scoped, decision
    set_current(context, decision)
    target = ReviewTarget(raw="main", head_branch="main", head_sha=head)
    return build_delta_request(context, target, current_decision(context), iteration, ("Title", "PR body")), decision


def test_request_holds_only_the_fix_with_full_context_and_addressed_findings(repo):
    _commit(repo, {"src/old.py": "OLD_CHANGE = 1\n"}, "reviewed earlier")
    context = _reviewed(repo)
    record_addressed_findings(context, [FindingRow("SEC-7", "critical", APP, 3, (1,), "Line 3 leaks.", "")])
    lines = (repo / APP).read_text(encoding="utf-8").splitlines()
    lines[3] = "line 3 fixed"
    _commit(repo, {APP: "\n".join(lines) + "\n", "plans/P-1.md": "# bookkeeping\n"}, "fix")

    request, decision = _scoped_request(repo, context)

    assert "+line 3 fixed" in request.diff_text and "-line 3" in request.diff_text
    assert "OLD_CHANGE" not in request.diff_text and "plans/P-1.md" not in request.diff_text
    assert request.base_ref == decision.base and request.head_sha == decision.head
    assert "line 9" in request.pr_body  # Unchanged context the diff never shows.
    assert "## Scoped Delta Review" in request.pr_body and request.pr_body.startswith("PR body")
    assert "- `SEC-7` (critical, `src/app.py:3`): Line 3 leaks." in request.pr_body


def test_context_files_are_truncated_redacted_and_bounded(repo):
    context = _reviewed(repo)
    token = _fake_token()
    _commit(repo, {APP: f"token = '{token}'\n" + "x" * 1500 + "\n", "src/b.py": "b\n", "src/c.py": "c\n"})
    head = _git(repo, "rev-parse", "HEAD")
    decision = decide_review_mode(context, str(repo), head, env={"HOLON_SCOPED_REVIEW_CONTEXT_BYTES": "1000"})
    set_current(context, decision)
    target = ReviewTarget(raw="main", head_branch="main", head_sha=head)

    request = build_delta_request(context, target, current_decision(context), 2, ("", "body"))

    assert token not in request.pr_body and token not in request.diff_text
    assert "[... truncated:" in request.pr_body
    assert "`src/b.py`" in request.pr_body and "`src/c.py`" in request.pr_body


def test_request_for_another_head_is_refused(repo):
    context = _reviewed(repo)
    _commit(repo, {APP: "fixed\n"})
    target = ReviewTarget(raw="main", head_branch="main", head_sha="0" * 40)
    with pytest.raises(RuntimeError):
        build_delta_request(context, target, _decide(context, repo), 2, ("", ""))


def test_delta_body_fences_content_with_backticks():
    delta = ScopedDelta("b", "h", (("README.txt", "```\ncode\n```"),), (), ("big.py",))
    body = format_delta_body("Body", delta)
    assert "````\n```\ncode\n```\n````" in body
    assert "None recorded." in body and "Not shown (context limit): `big.py`" in body


# --- forwarding ------------------------------------------------------------------------------------------------------


def test_forwarded_decision_round_trips_for_the_same_head(repo):
    context = _reviewed(repo)
    record_addressed_findings(context, [FindingRow("F-1", "nit", APP, 1, (1,), "Rename.", "")])
    head = _commit(repo, {APP: "fixed\n"})
    begin_review_pass(context, str(repo), head, 2)

    assert forward_scoped_decision(context) == FORWARDED_REL
    container = FlowContext(repo_dir=str(repo))
    assert load_forwarded_decision(container, str(repo), FORWARDED_REL) is True
    discard_forwarded_decision(context)
    assert not (repo / FORWARDED_REL).exists()

    decision = begin_review_pass(container, str(repo), head, 2)
    assert decision == current_decision(context)
    assert [row["id"] for row in addressed_findings(container)] == ["F-1"]


def test_forwarded_decision_for_another_head_is_full(repo):
    context = _reviewed(repo)
    head = _commit(repo, {APP: "fixed\n"})
    begin_review_pass(context, str(repo), head, 2)
    forward_scoped_decision(context)
    container = FlowContext(repo_dir=str(repo))
    assert load_forwarded_decision(container, str(repo), FORWARDED_REL)
    assert decide_review_mode(container, str(repo), "0" * 40).reason == "forwarded head mismatch"


def test_full_decision_is_not_forwarded(repo):
    context = FlowContext(repo_dir=str(repo))
    set_current(context, ReviewMode(FULL, "last reviewed head unknown", "h"))
    assert forward_scoped_decision(context) is None
    assert not (repo / FORWARDED_REL).exists()


def test_malformed_forwarded_file_loads_nothing(repo):
    (repo / ".holon" / "review").mkdir(parents=True)
    (repo / FORWARDED_REL).write_text("{not json", encoding="utf-8")
    context = FlowContext(repo_dir=str(repo))
    assert load_forwarded_decision(context, str(repo), FORWARDED_REL) is False
    assert "forwarded" not in delta_state_of(context)


@pytest.mark.parametrize("rel_path", ["/etc/passwd", "../outside.json", ".holon/../../outside.json"])
def test_escaping_forwarded_paths_are_rejected(repo, rel_path):
    assert load_forwarded_decision(FlowContext(repo_dir=str(repo)), str(repo), rel_path) is False


@pytest.mark.parametrize(
    "data",
    [
        None,
        {"mode": "scoped", "reason": "r", "head": "h", "base": "b", "paths": ["a"], "context_bytes": 1},
        {"mode": "scoped", "reason": "r", "head": H2, "base": "--output=x", "paths": ["a"], "context_bytes": 1},
        {"mode": "scoped"},
        {"mode": "scoped", "reason": "r", "head": "h", "base": "", "paths": ["a"], "context_bytes": 1},
        {"mode": "scoped", "reason": "r", "head": "h", "base": "b", "paths": "a", "context_bytes": 1},
        {"mode": "partial", "reason": "r", "head": "h", "base": "b", "paths": ["a"], "context_bytes": 1},
    ],
)
def test_malformed_decisions_are_full(data):
    assert decision_from_dict(data).mode == FULL


def test_scoped_decision_serialises_to_json():
    decision = ReviewMode(SCOPED, "delta 1 lines", H2, H1, (APP,), 1, 10)
    assert decision_from_dict(json.loads(json.dumps(decision.to_dict()))) == decision


def test_container_entrypoint_ignores_an_invalid_delta_request(repo, capsys):
    from sandbox_executor.entrypoint.review_loop import _load_delta_request

    context = FlowContext(repo_dir=str(repo))
    _load_delta_request(context, str(repo), "../escape.json")
    _load_delta_request(context, str(repo), None)
    assert "forwarded" not in delta_state_of(context)
    assert capsys.readouterr().err.count("Ignoring --delta-request=../escape.json") == 1
