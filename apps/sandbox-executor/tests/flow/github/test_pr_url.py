"""Tests for the stdout-only ``pr_url`` from ``create_or_update_pr`` and the non-interactive ``gh_env`` flags."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest
from sandbox_executor.flow.github.adapter import GitHubAdapter
from sandbox_executor.flow.github.adapter_runner import gh_env
from sandbox_executor.flow.github.pr import create_or_update_pr

PR_URL = "https://github.com/o/r/pull/7"
PROXY = {"HTTPS_PROXY": "http://proxy:1"}
HINT_BLOCK = (
    "hint: if you need to wait for this pull request to be checked, run\r\n"
    "hint:   gh event listen --pull-request 7\r\n"
    "https://github.com/o/r/actions\n"
)


class ScriptedGh:
    """A ``subprocess.run`` stand-in that scripts gh results by subcommand and records argv and env."""

    def __init__(
        self,
        *,
        pr_exists: bool,
        edit_stdout: str = "",
        edit_stderr: str = HINT_BLOCK,
        view_error: BaseException | None = None,
    ) -> None:
        self.pr_exists = pr_exists
        self.edit_stdout = edit_stdout
        self.edit_stderr = edit_stderr
        self.view_error = view_error
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert argv[0] == "gh"
        self.calls.append((argv, kwargs["env"]))
        subcommand = argv[2]
        if subcommand == "view":
            if self.view_error is not None:
                raise self.view_error
            if self.pr_exists:
                return subprocess.CompletedProcess(argv, 0, json.dumps({"number": 7}), "")
            return subprocess.CompletedProcess(argv, 1, "", "no pull requests found for branch")
        if subcommand == "create":
            return subprocess.CompletedProcess(argv, 0, f"{PR_URL}\r\n", HINT_BLOCK)
        return subprocess.CompletedProcess(argv, 0, self.edit_stdout, self.edit_stderr)


def _create_or_update(run: ScriptedGh, which: Any = lambda _: "/usr/bin/gh") -> dict[str, Any]:
    github = GitHubAdapter("/tmp", PROXY, run=run, which=which)
    return create_or_update_pr("/tmp", "feature", "main", "Title", "Body", PROXY, github=github)


def test_create_path_pr_url_comes_from_stdout_only() -> None:
    result = _create_or_update(ScriptedGh(pr_exists=False))

    assert result["action"] == "create"
    assert result["pr_number"] == 7
    assert result["pr_url"] == PR_URL
    for fragment in ("\n", "\r", "hint:", "gh event listen"):
        assert fragment not in result["pr_url"]
    assert "gh event listen" in result["output"]


def test_edit_path_without_stdout_url_has_no_pr_url() -> None:
    result = _create_or_update(ScriptedGh(pr_exists=True, edit_stdout="updated\n"))

    assert result["action"] == "edit"
    assert result["pr_url"] is None


def test_edit_path_ignores_url_led_stderr_when_stdout_is_empty() -> None:
    stderr = "https://github.com/o/r/actions\nhint:   gh event listen --pull-request 7\n"

    result = _create_or_update(ScriptedGh(pr_exists=True, edit_stderr=stderr))

    assert result["output"].startswith("https://")
    assert result["pr_url"] is None


def test_edit_path_with_stdout_url_parses_it() -> None:
    result = _create_or_update(ScriptedGh(pr_exists=True, edit_stdout=f"{PR_URL}\n"))

    assert result["action"] == "edit"
    assert result["pr_url"] == PR_URL


def test_not_attempted_result_has_no_pr_url() -> None:
    result = _create_or_update(ScriptedGh(pr_exists=False), which=lambda _: None)

    assert result["attempted"] is False
    assert result["pr_url"] is None


def test_error_result_has_no_pr_url() -> None:
    run = ScriptedGh(pr_exists=False, view_error=subprocess.TimeoutExpired(["gh"], 60))

    result = _create_or_update(run)

    assert result["action"] == "error"
    assert result["pr_url"] is None


def test_gh_env_disables_prompts_notifier_and_hints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_PROMPT_DISABLED", "0")
    monkeypatch.setenv("GH_NO_UPDATE_NOTIFIER", "0")

    env = gh_env({**PROXY, "GH_PROMPT_DISABLED": "0"})

    assert env["GH_PROMPT_DISABLED"] == "1"
    assert env["GH_NO_UPDATE_NOTIFIER"] == "1"
    assert env["DOCKER_CLI_HINTS"] == "false"
    assert env["HTTPS_PROXY"] == PROXY["HTTPS_PROXY"]


def test_every_gh_spawn_carries_the_non_interactive_flags() -> None:
    run = ScriptedGh(pr_exists=False)

    _create_or_update(run)

    assert len(run.calls) == 2
    for _, env in run.calls:
        assert env["GH_PROMPT_DISABLED"] == "1"
        assert env["GH_NO_UPDATE_NOTIFIER"] == "1"
        assert env["DOCKER_CLI_HINTS"] == "false"
        assert env["HTTPS_PROXY"] == PROXY["HTTPS_PROXY"]
