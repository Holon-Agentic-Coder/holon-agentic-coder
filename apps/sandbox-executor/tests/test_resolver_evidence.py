"""Resolver rejections of important or critical findings need a specific reason and evidence naming checked code."""

from __future__ import annotations

from typing import Any

import pytest
from sandbox_executor.flow import run_review_loop
from sandbox_executor.resolver_evidence import names_checked_code
from sandbox_executor.review_report import (
    FindingRow,
    deserialize_finding_dispositions,
    serialize_finding_disposition,
)

from tests.review_fakes import FakeEnsemble, install_fake_reviewers, objecting_reviewer
from tests.test_review_dispatch import EXEC_TARGET
from tests.test_review_loop import _ensemble_ctx, _LoopStubs, _rows


def _row(severity: str) -> FindingRow:
    return FindingRow("R1-F1", severity, "src/app.py", 12, (1,), f"{severity} problem", "Fix it.")


def _settle(row_severity: str, **entry: Any):
    (disposition,) = deserialize_finding_dispositions([{"id": "R1-F1", **entry}], [_row(row_severity)])
    return disposition


def test_an_important_finding_rejected_as_bare_out_of_scope_stays_open():
    disp = _settle("important", disposition="rejected", rejection_reason="out of scope")

    assert disp.disposition == "open"
    assert disp.rejection_reason is None
    assert disp.evidence == "unevidenced rejection (out of scope)"


def test_a_critical_rejected_as_inaccurate_with_a_file_and_line_stays_rejected():
    disp = _settle("critical", disposition="rejected", rejection_reason="inaccurate", evidence="src/x.py:42")

    assert (disp.disposition, disp.rejection_reason, disp.evidence) == ("rejected", "inaccurate", "src/x.py:42")


def test_a_critical_rejected_with_a_reason_but_no_evidence_stays_open():
    disp = _settle("critical", disposition="rejected", rejection_reason="not diff-grounded")

    assert disp.disposition == "open"


def test_out_of_scope_is_not_enough_for_an_important_finding_even_with_evidence():
    disp = _settle("important", disposition="rejected", rejection_reason="out of scope", evidence="src/x.py:42")

    assert disp.disposition == "open"
    assert "src/x.py:42" in (disp.evidence or "")


def test_a_nit_rejected_as_out_of_scope_stays_rejected():
    disp = _settle("nit", disposition="rejected", rejection_reason="out of scope")

    assert (disp.disposition, disp.rejection_reason) == ("rejected", "out of scope")


def test_an_unknown_reason_is_unspecified_never_out_of_scope():
    assert _settle("nit", disposition="rejected", rejection_reason="meh").rejection_reason == "unspecified"
    assert _settle("nit", disposition="rejected").rejection_reason == "unspecified"


@pytest.mark.parametrize(("severity", "expected"), [("critical", "open"), ("important", "open"), ("nit", "rejected")])
def test_a_finding_the_resolver_skipped_is_open_for_important_and_higher(severity, expected):
    (disp,) = deserialize_finding_dispositions([], [_row(severity)])

    assert disp.disposition == expected


def test_the_resolver_cannot_downgrade_a_critical_to_escape_the_rule():
    disp = _settle("critical", severity="nit", disposition="rejected", rejection_reason="out of scope")

    assert (disp.severity, disp.disposition) == ("critical", "open")


@pytest.mark.parametrize(
    ("evidence", "named"),
    [
        ("src/sandbox/mounts.py:40", True),
        ("tests/test_mounts.py::test_socket_is_not_mounted", True),
        ("checked test_mounts.py", True),
        ("I looked at it", False),
        ("src/sandbox/mounts.py", False),
        (None, False),
    ],
)
def test_evidence_must_name_a_file_and_line_or_a_test(evidence, named):
    assert names_checked_code(evidence) is named


def test_evidence_survives_serialisation():
    disp = _settle("critical", disposition="rejected", rejection_reason="inaccurate", evidence="src/x.py:42")

    (again,) = deserialize_finding_dispositions([serialize_finding_disposition(disp)], [_row("critical")])

    assert again == disp


def test_an_open_finding_goes_to_the_resolver_again_and_is_recorded_open_at_the_end(tmp_path, monkeypatch):
    fake = FakeEnsemble([objecting_reviewer(1, "important")])
    install_fake_reviewers(monkeypatch, fake)
    calls: list[int] = []
    head = iter(["a" * 40, "b" * 40])

    def resolver(target, iteration, findings, action_ids, rejected_findings):
        calls.append(iteration)
        dispositions = [
            {"id": row.id, "disposition": "rejected", "rejection_reason": "out of scope"} for row in findings
        ]
        return {"dispositions": dispositions, "head_sha": next(head), "pushed": True}

    ctx = _ensemble_ctx(tmp_path)
    with _LoopStubs():
        res = run_review_loop(
            ctx, target=EXEC_TARGET, max_iterations=2, ensemble_size=1, run_ensemble_fn=fake, run_resolver_fn=resolver
        )

    assert calls == [1, 2]
    assert res["consensus"]["approved"] is False
    (row,) = _rows(tmp_path, "ACT-RES")
    assert row["review_disposition"] is None
    assert row["rejection_reason"] is None
    assert row["outcome"] == "failure"
    assert "left open" in row["action"]
    assert [d["disposition"] for d in res["dispositions"]] == ["open"]


def test_the_resolver_prompt_asks_for_evidence(tmp_path):
    from sandbox_executor.entrypoint.review_loop import _build_resolver_prompt

    prompt = _build_resolver_prompt(str(tmp_path), "feature", 1, [_row("critical")], "/tmp/d.json")

    assert "`evidence`" in prompt
    assert "`out of scope` alone is not accepted" in prompt
