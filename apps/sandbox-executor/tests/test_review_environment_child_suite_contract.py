from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.entrypoint import review_loop
from sandbox_executor.flow.review.git_identity import REVIEW_GIT_ENV

from tests.hermetic_fixtures import (
    CHILD_SUITE_SENTINEL,
    REPO_ROOT,
    TESTS_DIR,
    project_environment,
    pytest_suite_command,
    uv_available,
)
from tests.test_review_loop import FakeGitCommands, _run_entrypoint

CANARY_TESTS = (
    f"{TESTS_DIR}/test_executor_run_salvage.py::TestExecutorPartialWorkSalvage"
    "::test_agent_commits_with_clean_tree_record_the_agent_head",
    f"{TESTS_DIR}/test_flow_repo_templates.py::test_the_history_matches_build_flow_repos_and_commit_on",
)

PRE_PUSH_TEST_COMMAND = ["uv", "run", "task", "test"]

PARENT_XDIST_PREFIX = "PYTEST_XDIST_"

CHILD_TIMEOUT_SECONDS = 120

pytestmark = [
    pytest.mark.skipif(os.environ.get(CHILD_SUITE_SENTINEL) == "1", reason="child suite must not recurse"),
    pytest.mark.skipif(not uv_available(), reason="uv is not available to run the child suite"),
]


class PrePushEnvironmentRecordingGit(FakeGitCommands):
    def __init__(self, repo: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self.repo = repo
        self.verification_started = False
        self.pre_push_env: dict[str, str] | None = None

    def __call__(self, args, cwd=None, env=None, check=True):
        if not self.verification_started:
            self.verification_started = True
            # why: _run_entrypoint fakes the loop, so main()'s real process environment is observed from in here
            review_loop._verify_before_push(str(self.repo))
        if list(args) == PRE_PUSH_TEST_COMMAND:
            self.pre_push_env = dict(env)
        return super().__call__(args, cwd=cwd, env=env, check=check)


@pytest.fixture
def review_loop_child_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    for key in list(os.environ):
        if key.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_")):
            monkeypatch.delenv(key)
    fake_git = PrePushEnvironmentRecordingGit(tmp_path, branch="feature/y")

    code, _loop, _post, _gh = _run_entrypoint(tmp_path, "feature/y", fake_git)

    assert code == 0
    assert fake_git.pre_push_env is not None, "the review loop never ran its pre-push suite"
    return fake_git.pre_push_env


def run_canaries_under(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    child_env = dict(env)
    # why: the parent runner's variables would make the child mistake itself for an xdist worker
    for name in list(child_env):
        if name.startswith(PARENT_XDIST_PREFIX):
            del child_env[name]
    child_env.pop("PYTEST_CURRENT_TEST", None)
    child_env[CHILD_SUITE_SENTINEL] = "1"
    # why: the child must reuse the parent's uv environment and never reach the network or download a Python
    child_env["UV_PROJECT_ENVIRONMENT"] = project_environment()
    child_env["UV_OFFLINE"] = "1"
    child_env["UV_PYTHON_DOWNLOADS"] = "never"
    return subprocess.run(
        pytest_suite_command("-n", "0", targets=CANARY_TESTS),
        cwd=REPO_ROOT,
        env=child_env,
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT_SECONDS,
        check=False,
    )


def child_output_tail(child: subprocess.CompletedProcess[str]) -> str:
    return f"{child.stdout[-3000:]}\n{child.stderr[-2000:]}"


def test_canary_tests_pass_under_the_environment_the_review_loop_hands_its_pre_push_suite(
    review_loop_child_environment: dict[str, str],
) -> None:
    child = run_canaries_under(review_loop_child_environment)

    assert child.returncode == 0, child_output_tail(child)
    assert "2 passed" in child.stdout, child_output_tail(child)


def test_canary_tests_fail_when_the_review_identity_leaks_into_that_environment(
    review_loop_child_environment: dict[str, str],
) -> None:
    child = run_canaries_under({**review_loop_child_environment, **REVIEW_GIT_ENV})

    assert child.returncode == 1, child_output_tail(child)
    failed_lines = [line for line in child.stdout.splitlines() if line.startswith("FAILED")]
    for canary in CANARY_TESTS:
        assert any(canary in line for line in failed_lines), child_output_tail(child)
