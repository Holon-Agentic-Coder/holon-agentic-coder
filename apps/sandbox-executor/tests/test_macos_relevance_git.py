"""Tests for the local git reads of the macOS relevance gate, on real temporary repositories (never the worktree).

``FixtureRepo`` builds what a ``pull_request`` checkout holds: a two-parent merge commit whose first parent is the base
branch and whose second parent is the pull request's head. ``decide`` then judges it exactly as CI does.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from scripts.ci_bookkeeping_chain import Decision
from scripts.macos_relevance import RelevanceFacts, decide, judge_commit
from scripts.macos_relevance_git import (
    ChangedFile,
    blob_text,
    changed_files,
    commit_parents,
    first_parent_commits,
    parse_name_status,
)

NEUTRAL_MODULE = "def add(a, b):\n    return a + b\n" + "".join(f"VALUE_{n} = {n}\n" for n in range(20))
SUBPROCESS_MODULE = "import subprocess\n" + NEUTRAL_MODULE
PLATFORM_MODULE = NEUTRAL_MODULE + "IS_MAC = sys.platform == 'darwin'\n"
BASE_FILES = {
    "README.md": "# Fixture\n",
    "pkg/calc.py": NEUTRAL_MODULE,
    "pkg/runner.py": SUBPROCESS_MODULE,
    "pkg/checks.py": PLATFORM_MODULE,
    "tools/build.sh": "#!/bin/sh\necho build\n",
}


class FixtureRepo:
    """A temporary repository on ``main`` with ``BASE_FILES`` committed; ``merge_pull_request`` builds a merge."""

    def __init__(self, root: Path) -> None:
        self.root = root
        subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True, capture_output=True)
        self.write(BASE_FILES)
        self.commit("base")

    def run(self, args: Sequence[str]) -> bytes:
        """The ``GitRunner`` of this repository."""
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True).stdout

    def git(self, *args: str) -> str:
        return self.run(args).decode("utf-8").strip()

    def write(self, files: dict[str, str | bytes | None]) -> None:
        """Write each file (``None`` deletes it) and stage everything."""
        for name, content in files.items():
            path = self.root / name
            if content is None:
                path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")
        self.git("add", "-A")

    def commit(self, message: str) -> str:
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def merge_pull_request(self, files: dict[str, str | bytes | None], renames: dict[str, str] | None = None) -> str:
        """Commit ``renames`` and ``files`` on a branch, merge it into ``main`` with a merge commit and check it out."""
        self.git("checkout", "-q", "-b", "pr", "main")
        for old, new in (renames or {}).items():
            (self.root / new).parent.mkdir(parents=True, exist_ok=True)
            self.git("mv", old, new)
        self.write(files)
        self.commit("pull request")
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "-m", "Merge pr into main", "pr")
        sha = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "--detach", sha)
        return sha


@pytest.fixture
def repo(tmp_path: Path) -> FixtureRepo:
    return FixtureRepo(tmp_path / "repo")


def facts_for(sha: str) -> RelevanceFacts:
    return RelevanceFacts(event_name="pull_request", run_attempt=1, labels=(), sha=sha)


def decide_merge(repo: FixtureRepo, files: dict[str, str | bytes | None], renames: dict[str, str] | None = None):
    sha = repo.merge_pull_request(files, renames)
    return decide(facts_for(sha), repo.run)


def test_a_neutral_only_python_change_skips(repo: FixtureRepo) -> None:
    judgement = decide_merge(repo, {"pkg/calc.py": NEUTRAL_MODULE + "EXTRA = 1\n", "docs/new.md": "x\n"})

    assert judgement.decision == Decision(True, "all-neutral", "2 changed files")
    assert [verdict.sensitive for verdict in judgement.verdicts] == [False, False]


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ("import subprocess\n" + NEUTRAL_MODULE, "process `import subprocess`"),
        (NEUTRAL_MODULE + "MAC = sys.platform\n", "platform-check `sys.platform`"),
    ],
)
def test_adding_a_sensitive_use_to_a_neutral_file_runs(repo: FixtureRepo, content: str, reason: str) -> None:
    judgement = decide_merge(repo, {"pkg/calc.py": content})

    assert judgement.decision == Decision(False, "sensitive-content", f"pkg/calc.py (new: {reason})")


@pytest.mark.parametrize(
    ("path", "reason"),
    [("pkg/runner.py", "process `import subprocess`"), ("pkg/checks.py", "platform-check `sys.platform`")],
)
def test_removing_a_sensitive_use_runs_on_the_old_content(repo: FixtureRepo, path: str, reason: str) -> None:
    judgement = decide_merge(repo, {path: NEUTRAL_MODULE})

    assert judgement.decision == Decision(False, "sensitive-content", f"{path} (old: {reason})")


@pytest.mark.parametrize(
    "path",
    [
        "pyproject.toml",
        "uv.lock",
        "apps/sandbox-executor/uv.lock",
        ".github/workflows/test-unit.yml",
        ".github/actions/macos-relevance-gate/action.yml",
        ".github/workflows/README.md",
        "tools/deploy.sh",
        "apps/sandbox-executor/Dockerfile",
    ],
)
def test_a_sensitive_path_runs(repo: FixtureRepo, path: str) -> None:
    judgement = decide_merge(repo, {"docs/new.md": "x\n", path: "content\n"})

    assert judgement.decision.skip is False
    assert judgement.decision.rule == "sensitive-path"
    assert judgement.decision.detail.startswith(f"{path} (new: ")


def test_a_deleted_sensitive_file_runs_on_its_old_path(repo: FixtureRepo) -> None:
    judgement = decide_merge(repo, {"tools/build.sh": None})

    assert judgement.decision == Decision(False, "sensitive-path", "tools/build.sh (old: shell-or-container)")


def test_a_deleted_file_with_sensitive_content_runs_on_its_old_content(repo: FixtureRepo) -> None:
    judgement = decide_merge(repo, {"pkg/runner.py": None})

    detail = "pkg/runner.py (old: process `import subprocess`)"
    assert judgement.decision == Decision(False, "sensitive-content", detail)


def test_a_sensitive_file_renamed_to_a_neutral_name_runs_on_its_old_path(repo: FixtureRepo) -> None:
    judgement = decide_merge(repo, {}, renames={"tools/build.sh": "docs/build.txt"})

    assert judgement.verdicts[0].change == ChangedFile("R", "docs/build.txt", "tools/build.sh")
    assert judgement.decision == Decision(False, "sensitive-path", "docs/build.txt (old: shell-or-container)")


def test_a_renamed_file_whose_sensitive_use_was_removed_runs_on_its_old_content(repo: FixtureRepo) -> None:
    judgement = decide_merge(repo, {"pkg/plain.py": NEUTRAL_MODULE}, renames={"pkg/runner.py": "pkg/plain.py"})

    assert judgement.verdicts[0].change == ChangedFile("R", "pkg/plain.py", "pkg/runner.py")
    assert judgement.decision == Decision(False, "sensitive-content", "pkg/plain.py (old: process `import subprocess`)")


def test_an_empty_diff_runs(repo: FixtureRepo) -> None:
    assert decide_merge(repo, {}).decision == Decision(False, "no-changed-files", "")


def test_undecodable_python_content_runs(repo: FixtureRepo) -> None:
    judgement = decide_merge(repo, {"pkg/blob.py": b"\xff\xfe\x00binary"})

    assert judgement.decision == Decision(False, "sensitive-content", "pkg/blob.py (new: unreadable-content)")


def test_a_head_that_is_not_github_sha_runs(repo: FixtureRepo) -> None:
    repo.merge_pull_request({"docs/new.md": "x\n"})

    judgement = decide(facts_for("0" * 40), repo.run)

    assert judgement.decision == Decision(False, "changed-files-unreadable", "HEAD is not GITHUB_SHA")


def test_a_head_without_two_parents_runs(repo: FixtureRepo) -> None:
    sha = repo.git("rev-parse", "HEAD")

    judgement = decide(facts_for(sha), repo.run)

    assert judgement.decision == Decision(False, "changed-files-unreadable", "HEAD has 0 parents, not 2")


def test_a_git_failure_runs(repo: FixtureRepo) -> None:
    def failing(args: Sequence[str]) -> bytes:
        raise subprocess.CalledProcessError(128, ["git", *args], b"", b"fatal: secret text")

    judgement = decide(facts_for("a" * 40), failing)

    assert judgement.decision == Decision(False, "changed-files-unreadable", "CalledProcessError")


def test_a_missing_first_parent_runs(repo: FixtureRepo) -> None:
    assert judge_commit(repo.run, "0" * 40, "HEAD").decision == Decision(
        False, "changed-files-unreadable", "CalledProcessError"
    )


def test_odd_paths_are_parsed_and_printed_safely(repo: FixtureRepo) -> None:
    odd = "docs/a file ü.md"
    newline = "docs/new\nline.md"

    judgement = decide_merge(repo, {odd: "x\n", newline: "y\n"})

    assert {verdict.change.path for verdict in judgement.verdicts} == {odd, newline}
    assert f"  neutral   A {odd}" in judgement.lines()
    assert "  neutral   A docs/new?line.md" in judgement.lines()


def test_parse_name_status_reads_renames_by_field() -> None:
    output = b"M\0a.py\0R087\0old name.sh\0new\nname.txt\0D\0gone.py\0"

    assert parse_name_status(output) == [
        ChangedFile("M", "a.py"),
        ChangedFile("R", "new\nname.txt", "old name.sh"),
        ChangedFile("D", "gone.py"),
    ]


@pytest.mark.parametrize("output", [b"M\0", b"R100\0old.py\0", b"\0a.py\0"])
def test_parse_name_status_rejects_a_truncated_record(output: bytes) -> None:
    with pytest.raises(ValueError, match="truncated"):
        parse_name_status(output)


def test_blob_text_caps_the_size_and_reads_both_sides(repo: FixtureRepo) -> None:
    sha = repo.merge_pull_request({"pkg/calc.py": "x = 1\n"})

    assert blob_text(repo.run, sha, "pkg/calc.py", 100) == "x = 1\n"
    assert blob_text(repo.run, f"{sha}^1", "pkg/calc.py", 1000) == NEUTRAL_MODULE
    assert blob_text(repo.run, f"{sha}^1", "pkg/calc.py", 10) is None
    assert blob_text(repo.run, sha, "pkg/absent.py", 100) is None


def test_changed_files_and_parents_of_a_merge(repo: FixtureRepo) -> None:
    sha = repo.merge_pull_request({"pkg/calc.py": "x = 1\n"})
    head, parents = commit_parents(repo.run, "HEAD")

    assert head == sha
    assert len(parents) == 2
    assert changed_files(repo.run, parents[0], head) == [ChangedFile("M", "pkg/calc.py")]


def test_first_parent_commits_lists_the_subjects_oldest_first(repo: FixtureRepo) -> None:
    repo.git("checkout", "-q", "main")
    first = repo.commit("feat: one (#7)")
    second = repo.commit("feat: two (#8)")

    assert first_parent_commits(repo.run, f"{first}^..main") == [(first, "feat: one (#7)"), (second, "feat: two (#8)")]
