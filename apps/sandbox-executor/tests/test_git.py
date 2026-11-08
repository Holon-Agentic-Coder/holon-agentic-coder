"""Unit tests for centralised Git repository abstraction."""

import ast
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.git import (
    GitRepository,
    branch_exists,
    changed_paths,
    empty_diff_summary,
    get_diff_numstat,
    get_head_commit,
    has_effective_diff,
    resolve_ref,
    run_git,
)


def test_run_git_success() -> None:
    """Verify run_git executes successfully and captures output."""
    res = run_git(["rev-parse", "--is-inside-work-tree"], check=True)
    assert res.returncode == 0
    assert res.stdout.strip() == "true"


def test_run_git_failure_raises() -> None:
    """Verify run_git raises RuntimeError with redacted output on failure when check=True."""
    with pytest.raises(RuntimeError) as exc_info:
        run_git(["rev-parse", "--verify", "refs/heads/nonexistent_test_branch_12345"], check=True)
    assert "Git command failed with exit code" in str(exc_info.value)
    assert "nonexistent_test_branch_12345" in str(exc_info.value)


def test_run_git_failure_no_check() -> None:
    """Verify run_git returns CompletedProcess without raising when check=False."""
    res = run_git(["rev-parse", "--verify", "refs/heads/nonexistent_test_branch_12345"], check=False)
    assert res.returncode != 0


def test_run_git_timeout() -> None:
    """Verify run_git raises TimeoutError on subprocess timeout."""
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["git", "status"], timeout=0.1)):
        with pytest.raises(TimeoutError) as exc_info:
            run_git(["status"], timeout=1)
        assert "Git command timed out" in str(exc_info.value)


def test_resolve_ref_and_get_head_commit() -> None:
    """Verify resolve_ref resolves valid references and get_head_commit returns 40-char SHA."""
    head_sha = get_head_commit(".")
    assert head_sha is not None
    assert len(head_sha) == 40
    assert all(c in "0123456789abcdefABCDEF" for c in head_sha)

    # Empty and invalid refs
    assert resolve_ref(".", "") is None
    assert resolve_ref(".", "invalid_ref_that_does_not_exist_98765") is None


def test_branch_exists() -> None:
    """Verify branch_exists correctly detects existing and non-existing branches."""
    # Non-existing branch
    assert not branch_exists(".", "branch_never_created_987654321")

    # Local HEAD branch or main
    has_main = branch_exists(".", "main") or branch_exists(".", "HEAD")
    assert has_main is True


def test_get_diff_numstat_parsing() -> None:
    """Verify get_diff_numstat parses textual and binary numstats correctly."""
    mock_stdout = "10\t5\tfile1.py\n-\t-\tbinary.png\n2\t0\tdocs/README.md\n"
    with patch("sandbox_executor.git.run_git") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=mock_stdout)
        numstats = get_diff_numstat(".", "base", "head")

        assert len(numstats) == 3
        assert numstats[0] == (10, 5, "file1.py")
        assert numstats[1] == (-1, -1, "binary.png")
        assert numstats[2] == (2, 0, "docs/README.md")


def test_get_diff_numstat_error() -> None:
    """Verify get_diff_numstat gracefully returns empty list on Git failure."""
    with patch("sandbox_executor.git.run_git") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="fatal: bad revision")
        numstats = get_diff_numstat(".", "bad_base", "bad_head")
        assert numstats == []


def _git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return res.stdout.strip()


def _write(repo: Path, rel: str, content: str) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


@pytest.fixture
def plan_repo(tmp_path: Path) -> Path:
    """A repository whose 'plan' branch holds a ledger, a plan and one source file; HEAD is on 'exec'."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "plan")
    _git(repo, "config", "user.email", "test@holon.com")
    _git(repo, "config", "user.name", "Holon Test")
    _write(repo, "holon-knowledge/ledger/executions.jsonl", "")
    _write(repo, "plans/P-1.md", "# Plan\n")
    _write(repo, "src/app.py", "print('hi')\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "plan")
    _git(repo, "checkout", "-q", "-b", "exec")
    return repo


def _commit_all(repo: Path, msg: str = "exec") -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)


def test_has_effective_diff_ledger_only_is_empty(plan_repo: Path) -> None:
    """A commit touching only the execution ledger is an empty execution."""
    _write(plan_repo, "holon-knowledge/ledger/executions.jsonl", '{"status": "success"}\n')
    _commit_all(plan_repo)
    assert changed_paths(str(plan_repo), "plan", "exec") == []
    assert has_effective_diff(str(plan_repo), "plan", "exec") is False


def test_has_effective_diff_plans_only_is_empty(plan_repo: Path) -> None:
    """Edits confined to plans/ are bookkeeping, not implementation."""
    _write(plan_repo, "plans/P-1.md", "# Plan\n\nEdited.\n")
    _write(plan_repo, "plans/P-1_calibration.md", "# Calibration\n")
    _commit_all(plan_repo)
    assert has_effective_diff(str(plan_repo), "plan", "exec") is False


def test_has_effective_diff_execution_notes_only_is_empty(plan_repo: Path) -> None:
    """An execution whose only output is its executions/E-*.md notes file is still empty."""
    _write(plan_repo, "executions/E-1-claude-agent-claude-opus-5-5.md", "# Execution notes\n")
    _write(plan_repo, "holon-knowledge/ledger/executions.jsonl", '{"status": "success"}\n')
    _commit_all(plan_repo)
    assert changed_paths(str(plan_repo), "plan", "exec") == []
    assert has_effective_diff(str(plan_repo), "plan", "exec") is False

    _write(plan_repo, "executions/E-2-claude-agent-claude-opus-5-5.md", "# Untracked notes\n")
    assert has_effective_diff(str(plan_repo), "plan") is False


def test_empty_diff_summary_names_every_exclusion() -> None:
    """The ledger summary lists all excluded bookkeeping prefixes."""
    assert empty_diff_summary("I-1/P-1/_") == (
        "Execution produced no changes outside holon-knowledge/ledger/, plans/ and executions/ relative to I-1/P-1/_"
    )


def test_has_effective_diff_mixed_changes(plan_repo: Path) -> None:
    """A real source change counts even alongside ledger and plan edits."""
    _write(plan_repo, "holon-knowledge/ledger/executions.jsonl", '{"status": "success"}\n')
    _write(plan_repo, "src/app.py", "print('changed')\n")
    _commit_all(plan_repo)
    assert changed_paths(str(plan_repo), "plan", "exec") == ["src/app.py"]
    assert has_effective_diff(str(plan_repo), "plan", "exec") is True


def test_has_effective_diff_rename_into_excluded_dir_counts(plan_repo: Path) -> None:
    """Moving a source file into plans/ still removes the source path, so it is not empty."""
    _git(plan_repo, "mv", "src/app.py", "plans/app.py")
    _commit_all(plan_repo)
    assert changed_paths(str(plan_repo), "plan", "exec") == ["src/app.py"]


def test_has_effective_diff_ignores_later_base_commits(plan_repo: Path) -> None:
    """Commits added to the plan branch after the execution forked are not execution work."""
    _git(plan_repo, "checkout", "-q", "plan")
    _write(plan_repo, "src/later.py", "x = 1\n")
    _commit_all(plan_repo, "later plan commit")
    assert has_effective_diff(str(plan_repo), "plan", "exec") is False


def test_has_effective_diff_working_tree_includes_untracked_and_commits(plan_repo: Path) -> None:
    """Without a head revision, commits on top of base, edits and untracked files all count."""
    base = _git(plan_repo, "rev-parse", "HEAD")
    assert has_effective_diff(str(plan_repo), base) is False

    _write(plan_repo, "holon-knowledge/ledger/executions.jsonl", '{"status": "success"}\n')
    assert has_effective_diff(str(plan_repo), base) is False

    _write(plan_repo, "src/new_module.py", "y = 2\n")
    assert changed_paths(str(plan_repo), base) == ["src/new_module.py"]

    _commit_all(plan_repo)
    assert changed_paths(str(plan_repo), base) == ["src/new_module.py"]


def test_has_effective_diff_fails_closed_on_bad_ref(plan_repo: Path) -> None:
    """An unknown base must raise, so callers never mistake a failed diff for real work."""
    with pytest.raises(RuntimeError, match="Git command failed"):
        has_effective_diff(str(plan_repo), "refs/heads/does-not-exist", "exec")


def test_has_effective_diff_ignores_target_branch_sync_merge(plan_repo: Path) -> None:
    """Content merged in from the target branch (executor pre-push sync) is not execution work."""
    _git(plan_repo, "checkout", "-q", "-b", "main", "plan")
    _write(plan_repo, "src/upstream.py", "z = 3\n")
    _commit_all(plan_repo, "main advances")
    _git(plan_repo, "checkout", "-q", "exec")
    _write(plan_repo, "holon-knowledge/ledger/executions.jsonl", '{"status": "success"}\n')
    _commit_all(plan_repo, "ledger only")
    _git(plan_repo, "merge", "-q", "--no-ff", "main", "-m", "chore(sync): sync target branch main before push")
    assert has_effective_diff(str(plan_repo), "plan", "exec") is False

    _write(plan_repo, "src/app.py", "print('real work')\n")
    _commit_all(plan_repo, "real work after sync")
    assert changed_paths(str(plan_repo), "plan", "exec") == ["src/app.py"]


def test_has_effective_diff_reverted_change_is_empty(plan_repo: Path) -> None:
    """A change that a later commit reverts leaves no net diff."""
    _write(plan_repo, "src/app.py", "print('temporary')\n")
    _commit_all(plan_repo, "change")
    _write(plan_repo, "src/app.py", "print('hi')\n")
    _commit_all(plan_repo, "revert")
    assert has_effective_diff(str(plan_repo), "plan", "exec") is False


def test_suite_git_config_is_the_hermetic_test_config(hermetic_git_config: str, tmp_path: Path) -> None:
    """Every git process in the suite reads the test-only global config, never the developer's or the system's.

    The queries run from ``tmp_path``, outside any repository, so a checkout's own ``.git/config`` (which takes
    precedence over the global file) cannot answer them: only the system and global scopes apply.
    """
    config_file = Path(hermetic_git_config)
    hooks = subprocess.run(
        ["git", "config", "--global", "--get", "core.hooksPath"],
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
    )
    assert hooks.stdout.strip() == (config_file.parent / "hooks").as_posix()
    assert list((config_file.parent / "hooks").iterdir()) == []
    origin = subprocess.run(
        ["git", "config", "--show-origin", "--get", "gc.auto"],
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
    )
    assert origin.stdout.strip() == f"file:{config_file.as_posix()}\t0"
    listing = subprocess.run(
        ["git", "config", "--list", "--show-origin"], capture_output=True, text=True, check=True, cwd=tmp_path
    )
    origins = {line.split("\t", 1)[0] for line in listing.stdout.splitlines()}
    assert origins == {f"file:{config_file.as_posix()}"}


def test_git_repository_init_and_custom_runner(tmp_path: Path) -> None:
    """Verify GitRepository initialisation with default runner and custom injected runner."""
    repo_default = GitRepository(tmp_path)
    assert repo_default.repo_dir == str(tmp_path)

    called_commands: list[list[str]] = []

    def custom_runner(
        args: list[str],
        cwd: str | None = None,
        check: bool = True,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        called_commands.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="custom_runner_output\n", stderr="")

    repo_custom = GitRepository(tmp_path, runner=custom_runner)
    res = repo_custom.run(["status", "--porcelain"])
    assert res.stdout == "custom_runner_output\n"
    assert called_commands == [["git", "status", "--porcelain"]]


def test_git_repository_rev_parse(plan_repo: Path) -> None:
    """Verify GitRepository.rev_parse with commit resolution and flag queries."""
    repo = GitRepository(plan_repo)
    head_res = repo.rev_parse("HEAD")
    assert head_res.returncode == 0
    assert len(head_res.stdout.strip()) == 40

    verify_res = repo.rev_parse("refs/heads/plan", verify=True)
    assert verify_res.returncode == 0
    assert len(verify_res.stdout.strip()) == 40

    missing_res = repo.rev_parse("refs/heads/nonexistent_ref_9999", verify=True, check=False)
    assert missing_res.returncode != 0

    worktree_res = repo.rev_parse("--is-inside-work-tree")
    assert worktree_res.returncode == 0
    assert worktree_res.stdout.strip() == "true"


def test_git_repository_current_branch(plan_repo: Path) -> None:
    """Verify GitRepository.current_branch detects current branch and detached HEAD states."""
    repo = GitRepository(plan_repo)
    assert repo.current_branch() == "exec"

    _git(plan_repo, "checkout", "-q", "--detach", "HEAD")
    assert repo.current_branch() is None


def test_git_repository_status_porcelain_and_is_clean(tmp_path: Path) -> None:
    """Verify GitRepository.is_clean and status_porcelain across filesystem lifecycle."""
    repo_dir = tmp_path / "lifecycle_repo"
    repo_dir.mkdir()
    _git(repo_dir, "init", "-q")
    _git(repo_dir, "config", "user.email", "test@holon.com")
    _git(repo_dir, "config", "user.name", "Holon Test")

    repo = GitRepository(repo_dir)
    assert repo.is_clean() is True
    assert repo.status_porcelain() == ""

    _write(repo_dir, "sample.txt", "content\n")
    assert repo.is_clean() is False
    assert "sample.txt" in repo.status_porcelain()

    repo.add("sample.txt")
    assert repo.is_clean() is False
    assert "sample.txt" in repo.status_porcelain()

    repo.commit("initial commit")
    assert repo.is_clean() is True
    assert repo.status_porcelain() == ""


def test_git_repository_operations(tmp_path: Path) -> None:
    """Verify GitRepository intention-revealing operations: config, add, commit, checkout, clean, reset."""
    repo_dir = tmp_path / "ops_repo"
    repo_dir.mkdir()
    _git(repo_dir, "init", "-q")

    repo = GitRepository(repo_dir)
    repo.config("user.email", "ops@holon.com")
    repo.config("user.name", "Ops Bot")

    _write(repo_dir, "file1.txt", "first\n")
    repo.add(all_files=True)
    repo.commit("first commit")

    repo.checkout("feature", create=True)
    assert repo.current_branch() == "feature"

    _write(repo_dir, "temp.junk", "discard\n")
    assert (repo_dir / "temp.junk").exists()
    repo.clean(force=True, directories=True)
    assert not (repo_dir / "temp.junk").exists()


def test_git_repository_run_failure_raises(plan_repo: Path) -> None:
    """Verify GitRepository.run raises RuntimeError with redacted output on failure when check=True."""
    repo = GitRepository(plan_repo)
    with pytest.raises(RuntimeError) as exc_info:
        repo.run(["checkout", "nonexistent_branch_that_fails_404"], check=True)
    assert "Git command failed with exit code" in str(exc_info.value)


def test_executor_contains_no_raw_git_literals() -> None:
    """Assert that entrypoint/executor.py contains no raw ['git', ...] literals outside allowed helpers."""
    executor_path = Path(__file__).resolve().parent.parent / "src" / "sandbox_executor" / "entrypoint" / "executor.py"
    source = executor_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(executor_path))

    allowed_functions = {"run_cmd", "_get_local_safe_directories"}
    violations: list[str] = []

    class ScopeVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.current_function: str | None = None

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            prev = self.current_function
            self.current_function = node.name
            self.generic_visit(node)
            self.current_function = prev

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            prev = self.current_function
            self.current_function = node.name
            self.generic_visit(node)
            self.current_function = prev

        def visit_List(self, node: ast.List) -> None:
            if (
                node.elts
                and isinstance(node.elts[0], ast.Constant)
                and node.elts[0].value == "git"
                and self.current_function not in allowed_functions
            ):
                func_name = self.current_function or "<module>"
                violations.append(f"Line {node.lineno} in {func_name}: raw ['git', ...] list literal")
            self.generic_visit(node)

    ScopeVisitor().visit(tree)
    assert not violations, "Raw git command literals found in executor.py:\n" + "\n".join(violations)
