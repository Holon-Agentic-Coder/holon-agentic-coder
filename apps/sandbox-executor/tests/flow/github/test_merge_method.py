"""Tests for the child PR merge-method choice: merge commit if allowed, else squash, never rebase; fail closed."""

from __future__ import annotations

import subprocess

import pytest
from sandbox_executor.flow.github.merge_method import MERGE_FLAG, SQUASH_FLAG, choose_merge_flag, lookup_merge_flag

NEITHER = "allows neither merge commits nor squash merges"


@pytest.mark.parametrize(
    ("settings", "flag"),
    [
        ({"allow_merge_commit": True, "allow_squash_merge": True}, MERGE_FLAG),
        ({"allow_merge_commit": True, "allow_squash_merge": False}, MERGE_FLAG),
        ({"allow_merge_commit": False, "allow_squash_merge": True}, SQUASH_FLAG),
        ({"allow_merge_commit": None, "allow_squash_merge": True}, SQUASH_FLAG),
        ({"allow_squash_merge": True, "allow_rebase_merge": True}, SQUASH_FLAG),
    ],
    ids=["both", "merge-only", "squash-only", "merge-null", "merge-missing"],
)
def test_choose_merge_flag_prefers_a_merge_commit_then_a_squash(settings, flag):
    assert choose_merge_flag(settings) == (flag, "")


@pytest.mark.parametrize(
    "settings",
    [
        {"allow_merge_commit": False, "allow_squash_merge": False},
        {"allow_merge_commit": False, "allow_squash_merge": False, "allow_rebase_merge": True},
        {"allow_merge_commit": "true", "allow_squash_merge": 1},
        {},
    ],
    ids=["nothing", "rebase-only", "non-boolean", "missing-keys"],
)
def test_choose_merge_flag_refuses_when_neither_method_is_strictly_allowed(settings):
    flag, reason = choose_merge_flag(settings)
    assert flag is None and NEITHER in reason


@pytest.mark.parametrize("settings", [None, [], "settings", [{"allow_merge_commit": True}]])
def test_choose_merge_flag_refuses_unreadable_settings(settings):
    assert choose_merge_flag(settings) == (None, "the repository merge settings could not be read")


class StubRunner:
    """Answers every ``gh`` call with one canned process and records the calls."""

    def __init__(self, code: int, stdout: str | None, stderr: str | None = ""):
        self.code, self.stdout, self.stderr = code, stdout, stderr
        self.calls: list[tuple[list[str], str]] = []

    def __call__(self, args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(args), cwd))
        return subprocess.CompletedProcess(["gh", *args], self.code, self.stdout, self.stderr)


def test_lookup_merge_flag_reads_the_repository_settings_once_with_gh_api(tmp_path):
    runner = StubRunner(0, '{"allow_merge_commit": false, "allow_squash_merge": true}')

    assert lookup_merge_flag(str(tmp_path), runner) == (SQUASH_FLAG, "")
    assert runner.calls == [(["api", "repos/{owner}/{repo}"], str(tmp_path))]


@pytest.mark.parametrize(
    ("code", "stdout", "stderr", "fragment"),
    [
        (1, "", "HTTP 404: Not Found", "failed: HTTP 404: Not Found"),
        (1, '{"allow_merge_commit": true}', "HTTP 502", "failed: HTTP 502"),
        (0, "", "", "failed: exit code 0"),
        (0, "  \n", None, "failed: exit code 0"),
        (0, None, None, "failed: exit code 0"),
        (127, "", "", "failed: exit code 127"),
        (0, "{not json", "", "did not return JSON"),
        (0, "[]", "", "could not be read"),
    ],
    ids=["http-404", "error-with-output", "empty", "blank", "no-stdout", "missing-binary", "invalid-json", "list"],
)
def test_lookup_merge_flag_fails_closed(tmp_path, code, stdout, stderr, fragment):
    flag, reason = lookup_merge_flag(str(tmp_path), StubRunner(code, stdout, stderr))
    assert flag is None and fragment in reason


def test_lookup_merge_flag_redacts_secrets_from_the_refusal(tmp_path):
    token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    flag, reason = lookup_merge_flag(str(tmp_path), StubRunner(1, "", f"bad credentials for {token}"))
    assert flag is None and "bad credentials" in reason
    assert token not in reason
