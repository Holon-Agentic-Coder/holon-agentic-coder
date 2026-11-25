"""The containerised conflict resolver entrypoint with a fake runner, a fake turn and scripted lint and test results."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from sandbox_executor.agent_runner import CONFLICT_RESOLVER_ROLE, HOST_WORKTREE_ROLES
from sandbox_executor.entrypoint import conflict_resolver, conflict_resolver_verify
from sandbox_executor.entrypoint.conflict_resolver import DECISION_PATH, TEMPLATE_PATH, ResolverInput

REPO_ROOT = Path(__file__).resolve().parents[3]
DISPATCHER = Path(__file__).resolve().parent.parent / "entrypoint" / "role_dispatcher.sh"
GIT_WRITES = {"add", "commit", "merge", "checkout", "reset", "stash", "rebase", "push", "rm", "restore", "switch"}
MARKED = "<<<<<<< HEAD\nours = 1\n=======\ntheirs = 2\n>>>>>>> target\n"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository holding the shipped template, a conflicted ``a.py`` and an untouched ``other.py``."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    (root / TEMPLATE_PATH).parent.mkdir(parents=True)
    shutil.copy(REPO_ROOT / TEMPLATE_PATH, root / TEMPLATE_PATH)
    (root / "a.py").write_text("base = 0\n", encoding="utf-8")
    (root / "other.py").write_text("other = 0\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    (root / "a.py").write_text(MARKED, encoding="utf-8")
    return root


@pytest.fixture
def request_input(repo: Path) -> ResolverInput:
    base = _git(repo, "rev-parse", "HEAD").strip()
    return ResolverInput(("a.py",), base, "HEAD", "HEAD", "origin/main", {"intent_id": "I-1"}, "", "claude-haiku-4-5")


class Harness:
    """Scripted ``uv`` results, real read-only git, a fake runner and a fake turn that may edit the worktree."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
        self.repo = repo
        self.calls: list[list[str]] = []
        self.uv: dict[str, subprocess.CompletedProcess[str]] = {}
        self.turn_code = 0
        self.turn_edit = None
        self.prompts: list[str] = []
        monkeypatch.setattr(conflict_resolver_verify, "run_cmd", self.run_cmd)
        monkeypatch.setattr(conflict_resolver, "run_cmd", self.run_cmd)
        monkeypatch.setattr(conflict_resolver, "get_runner", lambda name: self)
        monkeypatch.setattr(conflict_resolver, "run_turn", self.run_turn)

    def run_cmd(self, args, cwd, env=None):
        self.calls.append(list(args))
        if args[0] == "uv":
            return self.uv.get(args[-1], subprocess.CompletedProcess(args, 0, "", ""))
        return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, check=False)

    def build_command(self, prompt, model):
        self.prompts.append(prompt)
        return ["fake-agent", model]

    def run_turn(self, cmd, **kwargs):
        if self.turn_edit:
            self.turn_edit(self.repo)
        return SimpleNamespace(returncode=self.turn_code, stdout="agent out", stderr="", breach=None)

    def uv_calls(self) -> list[str]:
        return [call[-1] for call in self.calls if call[0] == "uv"]


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, repo: Path) -> Harness:
    return Harness(monkeypatch, repo)


def _resolve_markers(repo: Path) -> None:
    (repo / "a.py").write_text("ours = 1\ntheirs = 2\n", encoding="utf-8")


def test_prompt_lists_only_the_conflict_paths_and_renders_every_placeholder(harness, repo, request_input):
    prompt = conflict_resolver.build_prompt(str(repo), request_input, "/tmp/decision.txt")
    assert "- `a.py`" in prompt and "### `a.py`" in prompt
    assert "other.py" not in prompt
    assert "ours = 1" in prompt and "/tmp/decision.txt" in prompt and '"intent_id": "I-1"' in prompt
    assert "{conflict_files}" not in prompt and "{ours_diff}" not in prompt and "{plan_content}" not in prompt


def test_cannot_decide_skips_lint_and_tests(harness, repo, request_input):
    harness.turn_edit = lambda root: (root / DECISION_PATH).write_text("CANNOT_DECIDE: both rename x", "utf-8")
    verdict = conflict_resolver.resolve(str(repo), request_input, "claude-agent", "claude-haiku-4-5")
    assert verdict.verdict == "cannot_decide"
    assert verdict.reason == "both rename x"
    assert harness.uv_calls() == []


def test_lint_failure_stops_before_tests(harness, repo, request_input):
    harness.turn_edit = _resolve_markers
    harness.uv["lint"] = subprocess.CompletedProcess(["uv"], 1, "E501 line too long", "")
    verdict = conflict_resolver.resolve(str(repo), request_input, "claude-agent", "claude-haiku-4-5")
    assert verdict.verdict == "lint_failed" and verdict.lint_exit == 1
    assert harness.uv_calls() == ["lint"]


def test_test_failure_records_the_failing_test_ids(harness, repo, request_input):
    harness.turn_edit = _resolve_markers
    output = "FAILED tests/test_a.py::test_one - assert 1 == 2\n1 failed, 3 passed in 0.5s"
    harness.uv["test"] = subprocess.CompletedProcess(["uv"], 1, output, "")
    verdict = conflict_resolver.resolve(str(repo), request_input, "claude-agent", "claude-haiku-4-5")
    assert verdict.verdict == "tests_failed"
    assert verdict.failing_tests == ["tests/test_a.py::test_one"]
    assert harness.uv_calls() == ["lint", "test"]


def test_success_writes_a_resolved_result_and_issues_no_git_write(harness, repo, request_input, tmp_path):
    harness.turn_edit = _resolve_markers
    result_file = tmp_path / "result.json"
    payload = {"paths": ["a.py"], "merge_base": request_input.merge_base, "tier": "claude-haiku-4-5"}
    result_file.write_text(json.dumps(payload), encoding="utf-8")
    argv = [str(result_file), "claude-agent", "claude-haiku-4-5", "--result-file", str(result_file)]
    assert conflict_resolver.main([*argv, "--workspace", str(repo)]) == 0
    result = json.loads(result_file.read_text(encoding="utf-8"))
    assert result["verdict"] == "resolved" and result["lint_exit"] == 0 and result["test_exit"] == 0
    git_subcommands = {call[3] for call in harness.calls if call[0] == "git"}
    assert git_subcommands and not git_subcommands & GIT_WRITES
    assert _git(repo, "status", "--porcelain", "--untracked-files=no").strip() == "M a.py"


def test_agent_failure_gives_agent_failed_without_verification(harness, repo, request_input):
    harness.turn_code = 2
    verdict = conflict_resolver.resolve(str(repo), request_input, "claude-agent", "claude-haiku-4-5")
    assert verdict.verdict == "agent_failed" and "exited with code 2" in verdict.reason
    assert verdict.output_tail == "agent out"
    assert harness.uv_calls() == []


def test_unreadable_input_writes_an_agent_failed_result(harness, tmp_path, repo):
    result_file = tmp_path / "result.json"
    result_file.write_text("{}", encoding="utf-8")
    argv = [str(result_file), "claude-agent", "m", "--result-file", str(result_file), "--workspace", str(repo)]
    assert conflict_resolver.main(argv) == 0
    assert json.loads(result_file.read_text(encoding="utf-8"))["verdict"] == "agent_failed"
    assert harness.prompts == []


def test_role_mounts_the_host_worktree_and_the_dispatcher_routes_it():
    assert CONFLICT_RESOLVER_ROLE == "conflict-resolver"
    assert CONFLICT_RESOLVER_ROLE in HOST_WORKTREE_ROLES
    script = DISPATCHER.read_text(encoding="utf-8")
    assert "conflict-resolver)\n        exec python3 -m sandbox_executor.entrypoint.conflict_resolver" in script
