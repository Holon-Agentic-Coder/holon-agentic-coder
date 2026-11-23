"""Tests for the repository-scoped ``GitHubAdapter``: argv, cwd, env, timeouts, redaction and the write guard."""

from __future__ import annotations

import ast
import inspect
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from sandbox_executor.flow.github import adapter as adapter_module
from sandbox_executor.flow.github import adapter_runner
from sandbox_executor.flow.github.adapter import GitHubAdapter, merge_argv

from tests.gh_write_guard import GhWriteBlockedError

TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
PROXY = {"HTTP_PROXY": "http://127.0.0.1:8080"}


class RecordingRun:
    """A ``subprocess.run`` stand-in that records each call and replays scripted results in order."""

    def __init__(self, *results: Any) -> None:
        self.results = list(results)
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        result = self.results.pop(0) if self.results else (0, "", "")
        if isinstance(result, BaseException):
            raise result
        returncode, stdout, stderr = result
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def _adapter(run: RecordingRun, which: Any = lambda _: "/usr/bin/gh", proxy: Any = PROXY) -> GitHubAdapter:
    return GitHubAdapter("/repo", proxy, run=run, which=which)


def _assert_pr_call(call: tuple[list[str], dict[str, Any]], argv: list[str], timeout: float = 60) -> None:
    got_argv, kwargs = call
    assert got_argv == argv
    assert kwargs["cwd"] == "/repo"
    assert kwargs["timeout"] == timeout
    assert kwargs["capture_output"] is True and kwargs["text"] is True and kwargs["check"] is False
    assert kwargs["env"]["DOCKER_CLI_HINTS"] == "false"
    assert kwargs["env"]["HTTP_PROXY"] == PROXY["HTTP_PROXY"]


@pytest.mark.parametrize(
    ("call", "argv", "timeout"),
    [
        (
            lambda a: a.ci_checks("42", 12.5),
            ["gh", "pr", "checks", "42", "--json", "bucket,name,state,event,workflow,link"],
            12.5,
        ),
        (
            lambda a: a.mergeability("feat"),
            ["gh", "pr", "view", "feat", "--json", "number,mergeable,mergeStateStatus,statusCheckRollup"],
            60,
        ),
        (lambda a: a.post_review("42", "--approve", "R"), ["gh", "pr", "review", "42", "--approve", "--body", "R"], 60),
        (lambda a: a.post_comment("42", "R"), ["gh", "pr", "comment", "42", "--body", "R"], 60),
        (lambda a: a.pr_view_number("feat"), ["gh", "pr", "view", "feat", "--json", "number,title"], 60),
    ],
)
def test_pr_methods_pin_argv_cwd_env_and_timeout(call, argv, timeout):
    run = RecordingRun()
    call(_adapter(run))
    assert len(run.calls) == 1
    _assert_pr_call(run.calls[0], argv, timeout)


def test_open_or_update_edits_an_existing_pr_with_only_the_given_fields():
    run = RecordingRun((0, json.dumps({"number": 99, "title": "Old"}), ""), (0, "ok", ""))
    action, number, result = _adapter(run).open_or_update("feat", "main", "New title", "")
    assert (action, number, result.ok) == ("edit", 99, True)
    _assert_pr_call(run.calls[1], ["gh", "pr", "edit", "feat", "--title", "New title"])


def test_open_or_update_creates_with_defaults_and_parses_the_pr_number():
    run = RecordingRun((1, "", "no pull requests found"), (0, "https://github.com/o/r/pull/123\n", ""))
    action, number, result = _adapter(run).open_or_update("feat", "plan", "", "")
    assert (action, number, result.ok) == ("create", 123, True)
    _assert_pr_call(
        run.calls[1],
        [
            "gh",
            "pr",
            "create",
            "--base",
            "plan",
            "--head",
            "feat",
            "--title",
            "feat",
            "--body",
            "Automated PR for branch feat",
        ],
    )


def test_open_or_update_reports_an_unrunnable_lookup_as_error_without_creating():
    run = RecordingRun(subprocess.TimeoutExpired(["gh"], 60))
    action, number, result = _adapter(run).open_or_update("feat", "main", "t", "b")
    assert (action, number, result.returncode) == ("error", None, 127)
    assert len(run.calls) == 1


def test_missing_gh_is_unavailable_and_never_spawns():
    run = RecordingRun()
    github = _adapter(run, which=lambda _: None)
    assert github.available is False
    for result in (
        github.ci_checks("1", 5),
        github.mergeability("b"),
        github.post_review("1", "--comment", "r"),
        github.post_comment("1", "r"),
        github.open_or_update("b", "main", "t", "b")[2],
    ):
        assert (result.available, result.ok, result.returncode) == (False, False, 127)
    assert run.calls == []


@pytest.mark.parametrize("exc", [OSError(f"exec failed {TOKEN}"), subprocess.TimeoutExpired(["gh", TOKEN], 60)])
def test_spawn_failures_become_redacted_127_results(exc):
    result = _adapter(RecordingRun(exc)).post_review("1", "--comment", "r")
    assert (result.returncode, result.ok) == (127, False)
    assert result.error and TOKEN not in result.error and TOKEN not in result.stderr


def test_stderr_and_output_are_redacted_while_json_stdout_is_byte_identical():
    payload = json.dumps([{"name": "ci", "link": f"https://x/{TOKEN}"}])
    result = _adapter(RecordingRun((1, payload, f"bad token {TOKEN}"))).ci_checks("1", 5)
    assert result.stdout == payload
    assert TOKEN not in result.stderr
    assert TOKEN not in result.output


def test_merge_runner_has_no_proxy_env_the_merge_timeout_and_defaults_cwd(monkeypatch):
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    run = RecordingRun((0, "out", "err"))
    proc = _adapter(run, which=lambda _: None).gh(["repo", "view"])
    argv, kwargs = run.calls[0]
    assert argv == ["gh", "repo", "view"]
    assert (kwargs["cwd"], kwargs["timeout"]) == ("/repo", 120)
    assert "HTTP_PROXY" not in kwargs["env"] and kwargs["env"]["DOCKER_CLI_HINTS"] == "false"
    assert (proc.args, proc.returncode, proc.stdout, proc.stderr) == (["gh", "repo", "view"], 0, "out", "err")
    _adapter(run).gh(["pr", "list"], "/elsewhere")
    assert run.calls[1][1]["cwd"] == "/elsewhere"


def test_merge_runner_turns_a_missing_binary_into_a_127_process():
    proc = _adapter(RecordingRun(FileNotFoundError(f"no gh {TOKEN}"))).gh(["repo", "view"])
    assert proc.returncode == 127 and TOKEN not in proc.stderr


def test_pr_head_sha_reads_head_ref_oid_or_none():
    run = RecordingRun((0, json.dumps({"headRefOid": "abc"}), ""), (1, "", "boom"), (0, "not json", ""))
    github = _adapter(run)
    assert github.pr_head_sha("7", 10) == "abc"
    assert run.calls[0][0] == ["gh", "pr", "view", "7", "--json", "headRefOid"]
    assert run.calls[0][1]["timeout"] == 10 and run.calls[0][1]["env"]["HTTP_PROXY"] == PROXY["HTTP_PROXY"]
    assert github.pr_head_sha("7") is None
    assert github.pr_head_sha("7") is None
    assert github.pr_head_sha("") is None and len(run.calls) == 3
    assert _adapter(RecordingRun(), which=lambda _: None).pr_head_sha("7") is None


def test_rerun_failed_pins_argv_cwd_env_and_timeout_and_reads_the_exit_code():
    run = RecordingRun((0, "", ""), (1, "", "run is not failed"))
    github = _adapter(run)
    assert github.rerun_failed("4242", 10) is True
    _assert_pr_call(run.calls[0], ["gh", "run", "rerun", "4242", "--failed"], 10)
    assert github.rerun_failed("4242") is False
    assert run.calls[1][1]["timeout"] == 60


def test_rerun_failed_is_false_when_gh_is_missing_or_cannot_be_spawned():
    run = RecordingRun()
    assert _adapter(run, which=lambda _: None).rerun_failed("1", 10) is False
    assert run.calls == []
    assert _adapter(RecordingRun(OSError("exec failed"))).rerun_failed("1", 10) is False
    assert _adapter(RecordingRun(subprocess.TimeoutExpired(["gh"], 10))).rerun_failed("1", 10) is False


@pytest.mark.parametrize("flag", ["--merge", "--squash"])
def test_merge_argv_builds_the_match_head_commit_argv(flag):
    assert merge_argv(5, flag, "abc") == ["pr", "merge", "5", flag, "--match-head-commit", "abc"]


@pytest.mark.parametrize(("flag", "sha"), [("--rebase", "abc"), ("--auto", "abc"), ("--admin", "abc"), ("--merge", "")])
def test_merge_argv_refuses_other_flags(flag, sha):
    with pytest.raises(ValueError):
        merge_argv(5, flag, sha)


@pytest.mark.parametrize(
    "write",
    [
        lambda a: a.post_review("1", "--approve", "report"),
        lambda a: a.post_comment("1", "report"),
        lambda a: a.gh(merge_argv(1, "--merge", "abc")),
    ],
)
def test_adapter_writes_still_reach_the_suite_write_guard(write, gh_write_guard: list[str]):
    # No injected run: the real subprocess.run constructs Popen, which the suite-wide guard refuses for gh writes.
    github = GitHubAdapter("/nonexistent-repo", which=lambda _: "/usr/bin/gh")
    with pytest.raises(GhWriteBlockedError):
        write(github)
    assert len(gh_write_guard) == 1
    gh_write_guard.clear()


def test_open_or_update_create_still_reaches_the_write_guard(gh_write_guard: list[str]):
    real_run = subprocess.run

    def view_misses_then_real(argv: list[str], **kwargs: Any) -> Any:
        if argv[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(argv, 1, "", "no pull requests found")
        return real_run(argv, **kwargs)

    with pytest.raises(GhWriteBlockedError):
        GitHubAdapter("/nonexistent-repo", run=view_misses_then_real, which=lambda _: "/x/gh").open_or_update(
            "feat", "main", "t", "b"
        )
    assert gh_write_guard[0].startswith("gh pr create")
    gh_write_guard.clear()


def test_global_subprocess_and_which_patches_still_intercept(monkeypatch):
    run = RecordingRun((0, "[]", ""))
    monkeypatch.setattr("subprocess.run", run)
    monkeypatch.setattr("shutil.which", lambda _: "/patched/gh")
    github = GitHubAdapter("/repo")
    assert github.available is True
    github.ci_checks("1", 5)
    assert run.calls[0][0][:3] == ["gh", "pr", "checks"]


def test_adapter_modules_spawn_only_through_subprocess_run():
    for module in (adapter_module, adapter_runner):
        source = Path(inspect.getfile(module)).read_text(encoding="utf-8")
        assert "Popen(" not in source and "shell=True" not in source and "os.exec" not in source
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.arg | ast.keyword) and node.arg in ("run", "which"):
                default_bound = isinstance(node, ast.keyword) and isinstance(node.value, ast.Attribute)
                assert not default_bound, "run/which must be looked up at call time"
