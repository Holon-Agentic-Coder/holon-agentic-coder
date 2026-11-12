"""The Stage 4 review loop posts its consensus review only on a terminal exit: approval, or the iteration cap."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sandbox_executor.flow import run_review_loop
from sandbox_executor.flow.review.posting import PostFacts, decide_post

from tests.review_fakes import FakeEnsemble, clean_reviewer, failed_reviewer, objecting_reviewer
from tests.test_review_dispatch import EXEC_TARGET
from tests.test_review_loop import _ensemble_ctx, _LoopStubs, _rows


def _rejecting_resolver(target, iteration, findings, action_ids, rejected_findings):
    """A resolver that pushes nothing and leaves every finding open."""
    return {"dispositions": [], "head_sha": target.head_sha, "pushed": False}


def _run(tmp_path: Path, ensemble: FakeEnsemble, **kwargs: Any):
    ctx = _ensemble_ctx(tmp_path)
    params: dict[str, Any] = {"max_iterations": 2, "ensemble_size": 1, "run_ensemble_fn": ensemble}
    params.update(kwargs)
    stubs = params.pop("stubs", None) or _LoopStubs()
    with stubs as active:
        res = run_review_loop(ctx, target=EXEC_TARGET, **params)
    return res, active.post, ctx


def _not_posted(tmp_path: Path, post, ctx, reason: str) -> None:
    post.assert_not_called()
    assert not _rows(tmp_path, "ACT-POST")
    assert any("Not posting a review consensus" in line and reason in line for line in ctx.logs)


def test_clean_approval_posts_exactly_once(tmp_path):
    res, post, _ = _run(tmp_path, FakeEnsemble([clean_reviewer(1)]))

    assert res["consensus"]["approved"] is True
    assert post.call_count == 1
    assert len(_rows(tmp_path, "ACT-POST")) == 1


def test_iteration_cap_with_open_findings_posts_changes_requested_once(tmp_path):
    ensemble = FakeEnsemble([objecting_reviewer(1)])
    res, post, _ = _run(tmp_path, ensemble, run_resolver_fn=_rejecting_resolver)

    assert len(ensemble.calls) == 2
    assert res["consensus"]["approved"] is False
    assert post.call_count == 1
    assert post.call_args.kwargs["consensus_data"]["ensemble_verdict"] == "CHANGES_REQUESTED"
    assert len(_rows(tmp_path, "ACT-POST")) == 1


def test_resolver_error_posts_nothing_and_writes_no_act_post(tmp_path):
    def failing_resolver(target, iteration, findings, action_ids, rejected_findings):
        return {"dispositions": [], "head_sha": target.head_sha, "pushed": False, "error": "gate failed"}

    res, post, ctx = _run(
        tmp_path, FakeEnsemble([objecting_reviewer(1)]), max_iterations=3, run_resolver_fn=failing_resolver
    )

    assert "gate failed" in res["resolver_error"]
    _not_posted(tmp_path, post, ctx, "halted")


def test_no_convergence_halt_posts_nothing(tmp_path):
    ensemble = FakeEnsemble([objecting_reviewer(1)])
    res, post, ctx = _run(tmp_path, ensemble, max_iterations=6, run_resolver_fn=_rejecting_resolver)

    assert res["no_convergence"] is True
    _not_posted(tmp_path, post, ctx, "halted")


def test_stopping_before_the_cap_without_a_resolver_pass_posts_nothing(tmp_path):
    stubs = _LoopStubs(merge={"is_dirty": True, "merge_state_status": "DIRTY", "mergeable": "CONFLICTING"})
    _, post, ctx = _run(tmp_path, FakeEnsemble([clean_reviewer(1)]), max_iterations=5, stubs=stubs)

    _not_posted(tmp_path, post, ctx, "before the iteration cap")


def test_a_post_that_raises_writes_one_failure_row_and_fails_closed(tmp_path):
    stubs = _LoopStubs()
    stubs.post.side_effect = RuntimeError("gh exploded")
    res, post, _ = _run(tmp_path, FakeEnsemble([clean_reviewer(1)]), stubs=stubs)

    assert post.call_count == 1
    rows = _rows(tmp_path, "ACT-POST")
    assert [row["outcome"] for row in rows] == ["failure"]
    assert res["consensus"]["status"] == "post_failed"
    assert res["consensus"]["approved"] is False


def test_skip_post_posts_nothing(tmp_path):
    ctx = _ensemble_ctx(tmp_path)
    with _LoopStubs() as stubs:
        run_review_loop(
            ctx,
            target=EXEC_TARGET,
            max_iterations=1,
            ensemble_size=1,
            run_ensemble_fn=FakeEnsemble([clean_reviewer(1)]),
            skip_post=True,
        )

    stubs.post.assert_not_called()
    assert not _rows(tmp_path, "ACT-POST")


def test_no_completed_reviewer_posts_nothing(tmp_path):
    _, post, ctx = _run(tmp_path, FakeEnsemble([failed_reviewer(1)]), max_iterations=1)

    _not_posted(tmp_path, post, ctx, "")


def _facts(**overrides: Any) -> PostFacts:
    base: dict[str, Any] = {
        "dry_run": False,
        "skip_post": False,
        "failed": False,
        "reviewer_completed": True,
        "consensus_reached": False,
        "halted_early": False,
        "iteration": 3,
        "last_label": 3,
    }
    base.update(overrides)
    return PostFacts(**base)


@pytest.mark.parametrize(
    ("overrides", "post", "reason"),
    [
        ({"consensus_reached": True, "iteration": 1}, True, ""),
        ({}, True, ""),
        ({"iteration": 2}, False, "before the iteration cap"),
        ({"halted_early": True}, False, "halted"),
        ({"consensus_reached": True, "halted_early": True, "iteration": 1}, True, ""),
        ({"failed": True}, False, "the review failed"),
        ({"reviewer_completed": False}, False, "no reviewer completed"),
        ({"dry_run": True}, False, ""),
        ({"skip_post": True}, False, ""),
    ],
)
def test_decide_post_matrix(overrides, post, reason):
    decision = decide_post(_facts(**overrides))

    assert decision.post is post
    assert reason in decision.reason


def test_dry_run_and_skip_post_count_a_suppressed_record():
    assert decide_post(_facts(dry_run=True)).count_suppressed is True
    assert decide_post(_facts(skip_post=True)).count_suppressed is True
    assert decide_post(_facts(failed=True)).count_suppressed is False
