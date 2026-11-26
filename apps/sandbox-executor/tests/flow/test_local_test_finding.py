"""The resolver finding built from the Stage 4 test failure loop's evidence."""

from __future__ import annotations

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
