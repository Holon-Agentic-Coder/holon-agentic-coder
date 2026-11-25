"""`holon doctor` reports the GitHub token type and scopes, never its value, and warns without the workflow scope."""

from __future__ import annotations

import argparse
import io
import json
from unittest.mock import patch

import pytest
from sandbox_executor import cli
from sandbox_executor.flow.github import token_scopes
from sandbox_executor.flow.github.token_scopes import REFRESH_COMMAND, TokenScopeReport

SENTINEL = "ghp_TESTSENTINEL"
WORKFLOW_WARNING = "WARNING: the GitHub token lacks the workflow scope"


def _fetcher(scopes: str | None):
    def fetch(url, headers, timeout):
        return 200, ({} if scopes is None else {"X-OAuth-Scopes": scopes})

    return fetch


def _report_environment(monkeypatch, fetch) -> tuple[int, str]:
    real_scope_report = token_scopes.scope_report
    monkeypatch.setattr(token_scopes, "scope_report", lambda: real_scope_report(fetch=fetch))
    out = io.StringIO()
    code = cli.report_budgets_and_envelope(out=out)
    return code, out.getvalue()


@pytest.fixture(autouse=True)
def _sentinel_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", SENTINEL)


def test_a_classic_token_without_workflow_is_reported_and_warned(monkeypatch):
    code, output = _report_environment(monkeypatch, _fetcher("repo, read:org"))
    assert "github_token=classic source=GITHUB_TOKEN scopes=repo,read:org" in output
    assert WORKFLOW_WARNING in output and REFRESH_COMMAND in output
    assert code == 0 and SENTINEL not in output


def test_a_classic_token_with_workflow_has_no_warning(monkeypatch):
    code, output = _report_environment(monkeypatch, _fetcher("repo, workflow"))
    assert "scopes=repo,workflow" in output and "WARNING: the GitHub token" not in output
    assert code == 0 and SENTINEL not in output


def test_a_fine_grained_token_is_unverifiable_with_its_warning(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github_pat_TESTSENTINEL")
    _, output = _report_environment(monkeypatch, _fetcher("repo"))
    assert "github_token=fine-grained source=GITHUB_TOKEN scopes=unverifiable" in output
    assert "workflow scope cannot be verified" in output and "Workflows read/write" in output
    assert "github_pat_TESTSENTINEL" not in output


def test_no_token_is_reported_without_a_warning(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN")
    _, output = _report_environment(monkeypatch, _fetcher("repo"))
    assert "github_token=none (set GITHUB_TOKEN or run gh auth login)" in output
    assert "WARNING: the GitHub token" not in output


def test_the_default_report_never_reaches_the_api_and_leaks_nothing():
    """Without a patched report, the suite's API stub makes the scopes unverifiable; the doctor still succeeds."""
    out = io.StringIO()
    assert cli.report_budgets_and_envelope(out=out) == 0
    assert "scopes=unverifiable" in out.getvalue() and SENTINEL not in out.getvalue()


def test_a_failing_report_is_isolated_and_value_free(monkeypatch):
    def broken():
        raise RuntimeError(f"boom {SENTINEL}")

    monkeypatch.setattr(token_scopes, "scope_report", broken)
    out = io.StringIO()
    assert cli.report_budgets_and_envelope(out=out) == 1
    assert "WARNING: doctor github token check failed unexpectedly (RuntimeError)" in out.getvalue()
    assert SENTINEL not in out.getvalue()


def test_json_doctor_lists_the_workflow_warning(monkeypatch, capsys):
    from sandbox_executor.preflight import PreflightResult, PreflightStatus

    monkeypatch.setattr(token_scopes, "scope_report", lambda: TokenScopeReport("oauth", "gh", ("repo",)))
    probe = PreflightResult(
        status=PreflightStatus.OK,
        agent="antigravity-agent",
        model="m",
        image="holon/agent-antigravity",
        exit_code=0,
        output_excerpt="",
        hint="",
    )
    args = argparse.Namespace(agent="antigravity-agent", model="gemini-3.8-flash-medium", json=True, timeout=None)
    with patch("sandbox_executor.cli.doctor.run_preflight_probe", return_value=probe):
        cli.run_doctor(args, fetch=lambda url, timeout: b"{}")
    environment = json.loads(capsys.readouterr().out)["environment"]
    assert "github_token=oauth source=gh scopes=repo" in environment["lines"]
    assert any(line.startswith(WORKFLOW_WARNING) for line in environment["warnings"])
