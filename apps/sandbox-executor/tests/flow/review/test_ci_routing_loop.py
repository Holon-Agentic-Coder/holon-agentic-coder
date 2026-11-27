"""Stage 4 routing of a red pipeline after a clean review: noise is rerun once, a defect goes to the resolver."""

from unittest.mock import patch

import pytest
from sandbox_executor.flow.review.ci_outcome import describe_ci_outcome

from tests.flow.github.fake_github import checks_for
from tests.review_fakes import install_fake_reviewers
from tests.review_loop_stubs import EXEC_TARGET, _ensemble_ctx, _LoopStubs, _rows


@pytest.fixture(autouse=True)
def _sandboxed_clean_reviewers(monkeypatch):
    """Clean fake reviewers in a simulated sandbox, as in tests/test_review_loop.py."""
    install_fake_reviewers(monkeypatch)


_RUN_LINK = "https://github.com/o/r/actions/runs/11/job/5"


def _one_check(bucket: str, state: str) -> dict:
    passed, failed = (1, 0) if bucket == "pass" else (0, 1)
    check = {"name": "unit", "bucket": bucket, "state": state, "link": _RUN_LINK}
    return {
        "source": "github_cli",
        "pending": 0,
        "failed": failed,
        "passed": passed,
        "total": 1,
        "all_passed": bucket == "pass",
        "checks": [check],
    }


def _failed_steps(steps):
    gate = all(s.startswith("CI Timing Gate") for s in steps)
    detail = {"name": "unit", "failed_steps": steps, "duration_seconds": 400.0, "timing_gate": gate}
    return lambda repo_dir, proxy_env: lambda check: dict(detail)


def _routed_loop(tmp_path, statuses, steps, container=False):
    """Run one clean-review iteration on a red pipeline; return the result, the resolver calls and the fake.

    ``container`` runs the loop as the sandboxed review-loop container does: no injected ensemble seam.
    """
    from sandbox_executor.flow import run_review_loop

    from tests.review_fakes import FakeEnsemble

    resolver_calls, now = [], [0.0]

    def fake_resolver(target, iteration, findings, action_ids, rejected_findings):
        resolver_calls.append({"findings": findings, "iteration": iteration})
        return {"dispositions": [], "head_sha": target.head_sha, "pushed": False}

    def sleep(seconds):
        now[0] += seconds

    ensemble = FakeEnsemble()
    detail, slowest = _failed_steps(steps), lambda check: [("tests/test_slow.py::test_x", 90.0)]
    with (
        _LoopStubs() as stubs,
        patch("sandbox_executor.flow.review.ci_noise_rerun.gh_detail_lookup", detail),
        patch("sandbox_executor.flow.review.loop.gh_timing_seams", lambda r, p: (detail(r, p), slowest)),
        patch("sandbox_executor.flow.review.loop.run_ensemble", ensemble),
    ):
        stubs.github.checks_sequence = [checks_for(status) for status in statuses]
        res = run_review_loop(
            _ensemble_ctx(tmp_path),
            target=EXEC_TARGET,
            max_iterations=1,
            ensemble_size=3,
            run_ensemble_fn=None if container else ensemble,
            run_resolver_fn=fake_resolver,
            clock=lambda: now[0],
            sleep=sleep,
        )
    reruns = [args[0] for name, args in stubs.github.calls if name == "rerun_failed"]
    return res, resolver_calls, ensemble, reruns


def test_a_red_timing_gate_after_a_clean_review_is_rerun_once_without_another_ensemble(tmp_path):
    red, green = _one_check("fail", "FAILURE"), _one_check("pass", "SUCCESS")
    res, resolver_calls, ensemble, reruns = _routed_loop(tmp_path, [red, red, green], ["CI Timing Gate (fast)"])
    assert res["consensus"]["approved"] is True
    assert reruns == ["11"] and len(ensemble.calls) == 1 and resolver_calls == []
    routed = _rows(tmp_path, "ACT-CIROUTE")
    assert [r["ci_failure_routing"]["decision"] for r in routed] == ["rerun"]


def test_a_timing_gate_still_red_after_its_rerun_goes_to_the_resolver_with_the_timing_finding(tmp_path):
    red = _one_check("fail", "FAILURE")
    res, resolver_calls, ensemble, reruns = _routed_loop(tmp_path, [red], ["CI Timing Gate (fast)"])
    assert res["consensus"]["approved"] is False
    assert reruns == ["11"] and len(ensemble.calls) == 1
    ci_rows = [f for f in resolver_calls[0]["findings"] if f.id == "FIND-CI-001"]
    assert ci_rows and "timing gate" in ci_rows[0].summary and "tests/test_slow.py::test_x" in ci_rows[0].summary


def test_the_review_loop_container_leaves_the_noise_rerun_to_the_host(tmp_path):
    red = _one_check("fail", "FAILURE")
    res, _, ensemble, reruns = _routed_loop(tmp_path, [red], ["CI Timing Gate (fast)"], container=True)
    assert res["consensus"]["approved"] is False
    assert reruns == [] and len(ensemble.calls) == 1
    assert _rows(tmp_path, "ACT-CIROUTE") == []


def test_a_failed_test_step_goes_straight_to_the_resolver_without_a_rerun(tmp_path):
    red = _one_check("fail", "FAILURE")
    res, resolver_calls, _, reruns = _routed_loop(tmp_path, [red], ["Run tests"])
    assert reruns == [] and len(resolver_calls) == 1
    assert [r["ci_failure_routing"]["classification"] for r in _rows(tmp_path, "ACT-CIROUTE")] == ["defect"]
    assert res["consensus"]["approved"] is False


def test_the_stop_message_names_a_noise_class_still_red_after_its_rerun():
    red = _one_check("fail", "FAILURE")
    plain = "Autonomous review consensus failed: CI failed: unit."
    assert describe_ci_outcome(red, None, False) == plain
    routed = {"classification": "noise_infra", "decision": "resolve", "reruns": 1}
    assert describe_ci_outcome(red, None, False, routing=routed) == (
        "Autonomous review consensus failed: CI failed: CI noise (runner failure) still red after 1 rerun: unit."
    )
    assert describe_ci_outcome(red, None, False, routing={"classification": "defect", "reruns": 0}) == plain
    assert describe_ci_outcome(red, None, False, routing={"classification": "noise_timing", "reruns": 0}) == plain
