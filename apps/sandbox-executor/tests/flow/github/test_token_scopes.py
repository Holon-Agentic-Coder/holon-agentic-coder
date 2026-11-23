"""Tests for the GitHub token scope report: no network (every request goes to a fake fetcher) and no token leaks."""

from __future__ import annotations

import logging
import subprocess
from urllib.error import URLError

import pytest
from sandbox_executor.flow.github.token_scopes import (
    LOGIN_COMMAND,
    REFRESH_COMMAND,
    USER_API_URL,
    TokenScopeReport,
    resolve_github_token,
    scope_report,
    token_type,
    touches_workflows,
    workflow_scope_error,
)

SENTINEL = "ghp_TESTSENTINEL"
FINE_GRAINED = "github_pat_TESTSENTINEL"
WORKFLOW_PATHS = [".github/workflows/ci.yml"]


class FakeFetcher:
    """Records each GET and answers with a fixed status and headers (or raises)."""

    def __init__(self, headers=None, status=200, error=None):
        self.headers = headers or {}
        self.status = status
        self.error = error
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        if self.error is not None:
            raise self.error
        return self.status, self.headers


def _report(token=SENTINEL, **fetcher_kwargs):
    fetcher = FakeFetcher(**fetcher_kwargs)
    return scope_report(environ={"GITHUB_TOKEN": token}, fetch=fetcher), fetcher


def test_a_token_without_the_workflow_scope_fails_a_workflow_touch_set_with_the_refresh_command():
    report, fetcher = _report(headers={"X-OAuth-Scopes": "repo, read:org"})
    assert report.scopes == ("repo", "read:org")
    error = workflow_scope_error(report, touches_workflows(WORKFLOW_PATHS))
    assert error is not None and REFRESH_COMMAND in error and ".github/workflows/ci.yml" in error
    assert fetcher.calls[0][0] == USER_API_URL
    assert fetcher.calls[0][1]["Authorization"] == f"Bearer {SENTINEL}"


def test_the_same_token_passes_a_touch_set_without_workflow_files():
    report, _ = _report(headers={"X-OAuth-Scopes": "repo, read:org"})
    assert workflow_scope_error(report, touches_workflows(["apps/x.py"])) is None


def test_a_token_with_the_workflow_scope_passes_a_workflow_touch_set():
    report, _ = _report(headers={"x-oauth-scopes": "repo, workflow"})
    assert report.has_workflow
    assert workflow_scope_error(report, touches_workflows(WORKFLOW_PATHS)) is None


def test_a_fine_grained_token_is_unverifiable_and_makes_no_request():
    report, fetcher = _report(token=FINE_GRAINED, headers={"X-OAuth-Scopes": "repo"})
    assert fetcher.calls == []
    assert report.token_type == "fine-grained" and not report.verifiable
    assert workflow_scope_error(report, WORKFLOW_PATHS) is None
    assert "scopes=unverifiable" in report.format_line()


def test_a_classic_token_without_a_scopes_header_is_unverifiable():
    report, _ = _report(headers={"Content-Type": "application/json"})
    assert not report.verifiable and "X-OAuth-Scopes" in report.detail
    assert workflow_scope_error(report, WORKFLOW_PATHS) is None


def test_an_empty_scopes_header_is_a_classic_token_with_no_scopes():
    report, _ = _report(headers={"X-OAuth-Scopes": ""})
    assert report.scopes == ()
    assert "scopes=none" in report.format_line()
    assert workflow_scope_error(report, WORKFLOW_PATHS) is not None


@pytest.mark.parametrize(
    "error", [URLError(f"no route for {SENTINEL}"), OSError("network blocked")], ids=["url-error", "os-error"]
)
def test_a_failed_request_is_unverifiable_and_its_reason_carries_no_token(error):
    report, _ = _report(error=error)
    assert not report.verifiable and "failed" in report.detail
    assert SENTINEL not in report.detail


def test_a_non_ok_status_is_unverifiable():
    report, _ = _report(status=401, headers={"X-OAuth-Scopes": "repo"})
    assert not report.verifiable and "401" in report.detail


def test_no_token_against_workflow_paths_names_the_login_command():
    report = scope_report(environ={}, run=lambda argv: subprocess.CompletedProcess(argv, 1, "", "not logged in"))
    assert report.token_type == "none"
    error = workflow_scope_error(report, WORKFLOW_PATHS)
    assert error is not None and LOGIN_COMMAND in error
    assert workflow_scope_error(report, []) is None


@pytest.mark.parametrize(
    ("entry", "touches"),
    [
        (".github/workflows/ci.yml", True),
        ("./.github/workflows/release.yaml", True),
        (".github/workflows/", True),
        (".github/**", True),
        (".github/workflows/*.yml", True),
        ("**", True),
        (".github/", True),
        (".github/CODEOWNERS", False),
        ("apps/**", False),
        ("docs/workflows/x.md", False),
    ],
)
def test_touches_workflows_truth_table(entry, touches):
    assert (touches_workflows([entry]) != []) is touches


def test_touches_workflows_dedupes_and_names_only_the_workflow_entries():
    paths = [".github/workflows/ci.yml", "apps/x.py", ".github/workflows/ci.yml", ".github/**"]
    assert touches_workflows(paths) == [".github/workflows/ci.yml", ".github/**"]


@pytest.mark.parametrize(
    ("token", "label"),
    [
        ("ghp_x", "classic"),
        ("gho_x", "oauth"),
        ("github_pat_x", "fine-grained"),
        ("ghs_x", "app"),
        ("ghu_x", "user-to-server"),
        ("xyz", "unknown"),
    ],
)
def test_token_type_comes_from_the_prefix(token, label):
    assert token_type(token) == label


def test_the_token_value_never_appears_in_reports_errors_or_logs(caplog):
    caplog.set_level(logging.DEBUG)
    report, _ = _report(headers={"X-OAuth-Scopes": "repo"})
    error = workflow_scope_error(report, WORKFLOW_PATHS) or ""
    for text in (report.format_line(), repr(report), str(report), error, report.detail, caplog.text):
        assert SENTINEL not in text


def test_resolve_github_token_prefers_github_token_then_gh_token_then_gh():
    calls = []

    def gh(argv):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "gho_FROMGH\n", "")

    assert resolve_github_token({"GITHUB_TOKEN": "a", "GH_TOKEN": "b"}, gh) == ("a", "GITHUB_TOKEN")
    assert resolve_github_token({"GH_TOKEN": "b"}, gh) == ("b", "GH_TOKEN")
    assert calls == []
    assert resolve_github_token({}, gh) == ("gho_FROMGH", "gh")
    assert calls == [["gh", "auth", "token"]]


def test_resolve_github_token_survives_a_failing_gh():
    def broken(argv):
        raise OSError("gh vanished")

    assert resolve_github_token({}, broken) == (None, "none")


def test_report_format_line_names_type_source_and_scopes():
    line = TokenScopeReport("classic", "gh", ("repo", "workflow")).format_line()
    assert line == "github_token=classic source=gh scopes=repo,workflow"


def test_a_user_to_server_token_is_unverifiable_without_a_request():
    report, fetcher = _report(token="ghu_TESTSENTINEL", headers={"X-OAuth-Scopes": ""})
    assert not report.verifiable and fetcher.calls == []
    assert workflow_scope_error(report, WORKFLOW_PATHS) is None


def test_an_empty_scopes_header_from_an_unknown_token_is_unverifiable():
    report, _ = _report(token="xyz_TESTSENTINEL", headers={"X-OAuth-Scopes": ""})
    assert not report.verifiable and "empty scopes header" in report.detail
    assert workflow_scope_error(report, WORKFLOW_PATHS) is None


def test_an_empty_scopes_header_from_a_classic_token_means_no_scopes():
    report, _ = _report(headers={"X-OAuth-Scopes": ""})
    assert report.scopes == ()
    assert REFRESH_COMMAND in (workflow_scope_error(report, WORKFLOW_PATHS) or "")
