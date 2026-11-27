from __future__ import annotations

import sandbox_executor.flow as flow
from sandbox_executor.flow.review.local_test_finding import (
    MAX_IDS_NAMED,
    local_test_failure_finding,
    parked_record_from_ci_status,
)


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


def test_parked_record_from_ci_status_normalizes_attached_and_legacy_records():
    attached_status = {
        "test_failure": {
            "classification": "environment",
            "status": "blocked",
            "failing_ids": ["tests/test_a.py::test_alpha"],
            "signature": "sig-env",
            "blocked_on": "BEAN-42",
            "parked_record": {
                "status": "parked",
                "blocked_on": "BEAN-42",
                "classification": "environment",
                "failing_ids": ["tests/test_a.py::test_alpha"],
                "signature": "sig-env",
                "evidence": "env evidence",
                "rows": [{"action_id": "ACT-0"}],
                "checkpoint_path": None,
            },
        }
    }

    attached = parked_record_from_ci_status(attached_status, checkpoint_path="/tmp/cp.json")

    assert attached is not None
    assert attached["blocked_on"] == "BEAN-42"
    assert attached["classification"] == "environment"
    assert attached["failing_ids"] == ["tests/test_a.py::test_alpha"]
    assert attached["signature"] == "sig-env"
    assert attached["evidence"] == "env evidence"
    assert attached["rows"] == [{"action_id": "ACT-0"}]
    assert attached["checkpoint_path"] == "/tmp/cp.json"

    legacy_status = {
        "test_failure": {
            "classification": "pre-existing",
            "failing_ids": ["tests/test_a.py::test_beta"],
            "signature": "sig-pre",
            "rows": ["ACT-1"],
            "blocked_evidence": "pre-existing failure evidence",
        }
    }

    legacy = parked_record_from_ci_status(legacy_status, checkpoint_path="/tmp/legacy.json")

    assert legacy is not None
    assert legacy["blocked_on"] == "none"
    assert legacy["classification"] == "pre-existing"
    assert legacy["failing_ids"] == ["tests/test_a.py::test_beta"]
    assert legacy["signature"] == "sig-pre"
    assert legacy["evidence"] == "pre-existing failure evidence"
    assert legacy["rows"] == ["ACT-1"]
    assert legacy["checkpoint_path"] == "/tmp/legacy.json"


def test_parked_record_from_ci_status_returns_none_for_unparked_or_missing():
    assert parked_record_from_ci_status({}) is None
    assert parked_record_from_ci_status(_status(["tests/test_a.py::test_alpha"], {})) is None
    assert parked_record_from_ci_status({"test_failure": {"status": "passed", "failing_ids": []}}) is None


def test_local_test_failure_finding_surfaces_blocked_on():
    status = {
        "test_failure": {
            "classification": "environment",
            "failing_ids": ["tests/test_a.py::test_alpha"],
            "blocked_evidence": "test failure environment (blocked): leaked GIT_AUTHOR_NAME",
            "blocked_on": "BEAN-77",
        }
    }

    finding = local_test_failure_finding(1, status)

    assert finding is not None
    assert "Blocked, not fixable in code: test failure environment (blocked)" in finding.summary
    assert "Blocked on: BEAN-77." in finding.summary
    assert "Report this failure as blocked" in finding.recommendation


def test_parked_record_from_ci_status_is_lazily_exported_from_flow():
    assert flow.parked_record_from_ci_status is parked_record_from_ci_status
