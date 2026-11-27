from __future__ import annotations

from sandbox_executor.flow import parked_record_from_ci_status
from sandbox_executor.flow.review.local_test_finding import MAX_IDS_NAMED, local_test_failure_finding


def _status(ids: list[str], excerpts: dict[str, str]) -> dict[str, object]:
    return {"test_failure": {"classification": "introduced", "failing_ids": ids, "excerpts": excerpts}}


def test_the_finding_names_the_failing_ids_and_quotes_their_excerpts():
    status = _status(["tests/test_a.py::test_alpha"], {"tests/test_a.py::test_alpha": "E assert 1 == 2"})

    finding = local_test_failure_finding(2, status, ["local_tests", "lint"])

    assert finding is not None
    assert finding.id == "FIND-CI-002"
    assert finding.severity == "important"
    assert "(introduced): tests/test_a.py::test_alpha." in finding.summary
    assert finding.recommendation.startswith("Fix the code so the failing tests pass")
    assert "Failed CI checks: lint." in finding.summary
    assert "`tests/test_a.py::test_alpha`: E assert 1 == 2" in finding.recommendation


def test_many_failing_ids_are_capped_with_a_count():
    ids = [f"tests/test_a.py::test_{n}" for n in range(MAX_IDS_NAMED + 2)]

    finding = local_test_failure_finding(1, _status(ids, {}))

    assert finding is not None
    assert "(+2 more)" in finding.summary
    assert "none captured" in finding.recommendation


def test_no_loop_evidence_leaves_the_generic_finding():
    assert local_test_failure_finding(1, {}) is None
    assert local_test_failure_finding(1, _status([], {})) is None


def test_blocked_evidence_marks_the_finding_as_not_fixable_in_code():
    status = {
        "test_failure": {
            "classification": "environment",
            "failing_ids": ["tests/test_a.py::test_alpha"],
            "blocked_evidence": "test failure environment (blocked): leaked GIT_AUTHOR_NAME",
        }
    }

    finding = local_test_failure_finding(1, status)

    assert finding is not None
    assert "Blocked, not fixable in code: test failure environment (blocked)" in finding.summary
    assert "Report this failure as blocked" in finding.recommendation
    assert "Fix the code" not in finding.recommendation


def test_blocked_finding_surfaces_blocked_on_when_present():
    status = {
        "test_failure": {
            "classification": "environment",
            "failing_ids": ["tests/test_a.py::test_alpha"],
            "blocked_evidence": "test failure environment (blocked): env broken",
            "blocked_on": "BEAN-42",
        }
    }

    finding = local_test_failure_finding(1, status)

    assert finding is not None
    assert "Blocked, not fixable in code: test failure environment (blocked): env broken (blocked_on: BEAN-42)." in finding.summary
    assert "Report this failure as blocked" in finding.recommendation


def test_parked_record_from_ci_status_normalizes_watcher_fields():
    ci_status = {
        "test_failure": {
            "status": "exhausted",
            "classification": "introduced",
            "blocked_on": "PR#99",
            "parked_record": {
                "blocked_on": "PR#99",
                "status": "exhausted",
                "classification": "introduced",
                "failing_ids": ["tests/test_a.py::test_alpha"],
                "signature": "sig-123",
                "evidence": "test failure introduced (exhausted): ladder exhausted",
                "rows": [{"action_id": "ACT-0", "model_tier": "haiku-x"}],
                "checkpoint_path": "/tmp/cp-orig.json",
                "blocked_reason": "ladder exhausted",
                "final_tier": "opus-x",
            },
        }
    }

    record = parked_record_from_ci_status(ci_status)
    assert record == {
        "blocked_on": "PR#99",
        "status": "exhausted",
        "classification": "introduced",
        "failing_ids": ["tests/test_a.py::test_alpha"],
        "signature": "sig-123",
        "evidence": "test failure introduced (exhausted): ladder exhausted",
        "rows": [{"action_id": "ACT-0", "model_tier": "haiku-x"}],
        "checkpoint_path": "/tmp/cp-orig.json",
        "blocked_reason": "ladder exhausted",
        "final_tier": "opus-x",
    }

    overridden = parked_record_from_ci_status(ci_status, checkpoint_path="/tmp/cp-override.json")
    assert overridden is not None
    assert overridden["checkpoint_path"] == "/tmp/cp-override.json"


def test_parked_record_from_ci_status_defaults_unattributed_blocker_and_ignores_unparked():
    unattributed = {
        "test_failure": {
            "status": "exhausted",
            "classification": "introduced",
            "failing_ids": ["tests/test_a.py::test_alpha"],
            "signature": "sig-0",
            "rows": [{"action_id": "ACT-0"}],
        }
    }
    record = parked_record_from_ci_status(unattributed, checkpoint_path="/tmp/cp.json")
    assert record is not None
    assert record["blocked_on"] == "none"
    assert record["checkpoint_path"] == "/tmp/cp.json"
    assert record["rows"] == [{"action_id": "ACT-0"}]

    assert parked_record_from_ci_status({}) is None
    assert parked_record_from_ci_status({"test_failure": {"status": "passed", "failing_ids": []}}) is None
    assert parked_record_from_ci_status({"test_failure": {"status": "flaky_passed", "failing_ids": []}}) is None
