"""Unit tests for fail-closed review target resolution and the empty-diff verdict."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from sandbox_executor import review_target as rt
from sandbox_executor.review_target import (
    PR_VIEW_FIELDS,
    DiffVerdict,
    ReviewTarget,
    ReviewTargetError,
    classify_review_diff,
    github_repo_slug,
    is_pr_reference,
    resolve_review_target,
    verify_review_diff,
)

from tests.leak_assertions import assert_no_fragment, make_gh_probe

HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"
PLAN = "I-1-guard/P-1-claude-agent-m/_"
EXEC = "I-1-guard/P-1-claude-agent-m/E-2-claude-agent-m/_"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _commit(repo: Path, path: str, content: str, message: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path, branch: str = "main") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", branch)
    _commit(repo, "app.py", "x = 1\n", "base")
    return repo


class FakeGh:
    """Records gh argv and answers with one canned result."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "", raises: Exception | None = None):
        self.calls: list[tuple[list[str], str | None]] = []
        self._result = subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)
        self._raises = raises

    def __call__(self, argv: list[str], cwd: str | None) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, cwd))
        if self._raises is not None:
            raise self._raises
        return self._result


def _pr_json(**overrides: Any) -> str:
    data = {
        "number": 90,
        "headRefName": "feature/x",
        "headRefOid": HEAD_SHA,
        "baseRefName": "release",
        "title": "T",
        "body": "B",
    }
    data.update(overrides)
    return json.dumps(data)


# --------------------------------------------------------------------------------------
# is_pr_reference and github_repo_slug
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("90", True),
        (" 91 ", True),
        ("https://github.com/acme/widgets/pull/90", True),
        ("https://github.com/acme/widgets/pull/90/files", True),
        ("https://github.com/acme/widgets/pull/90/", True),
        ("feature/x", False),
        (EXEC, False),
        ("", False),
        ("https://github.com/acme/widgets/issues/90", False),
        ("https://example.com/acme/widgets/pull/90", False),
    ],
)
def test_is_pr_reference(target, expected):
    assert is_pr_reference(target) is expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/acme/widgets.git", "acme/widgets"),
        ("https://x-access-token:secret@github.com/acme/widgets.git", "acme/widgets"),
        ("git@github.com:acme/widgets.git", "acme/widgets"),
        ("ssh://git@github.com/acme/widgets", "acme/widgets"),
        ("https://gitlab.com/acme/widgets.git", None),
        ("", None),
    ],
)
def test_github_repo_slug_drops_userinfo_and_dot_git(url, expected):
    assert github_repo_slug(url) == expected


# --------------------------------------------------------------------------------------
# resolve_review_target: PR references
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["90", "https://github.com/acme/widgets/pull/90"])
def test_pr_reference_resolves_to_head_branch_and_sha(tmp_path, target):
    gh = FakeGh(stdout=_pr_json())

    resolved = resolve_review_target(str(tmp_path), target, run_gh=gh)

    assert resolved == ReviewTarget(
        raw=target,
        head_branch="feature/x",
        head_sha=HEAD_SHA,
        pr_number=90,
        title="T",
        body="B",
        base_branch="release",
    )
    assert resolved.is_pr
    assert gh.calls == [
        (["gh", "pr", "view", target, "--json", "number,headRefName,headRefOid,baseRefName,title,body"], str(tmp_path))
    ]
    assert PR_VIEW_FIELDS == "number,headRefName,headRefOid,baseRefName,title,body"


def test_pr_without_a_base_ref_name_carries_no_base_branch(tmp_path):
    resolved = resolve_review_target(str(tmp_path), "90", run_gh=FakeGh(stdout=_pr_json(baseRefName="")))

    assert resolved.base_branch is None


@pytest.mark.parametrize(
    ("gh", "message"),
    [
        (FakeGh(returncode=1, stderr="no pull requests found"), "no pull requests found"),
        (FakeGh(raises=subprocess.TimeoutExpired(["gh"], 60)), "timed out"),
        (FakeGh(raises=FileNotFoundError("gh")), "could not run"),
        (FakeGh(stdout="not json"), "invalid JSON"),
        (FakeGh(stdout=_pr_json(headRefName="")), "no head branch or head SHA"),
        (FakeGh(stdout=_pr_json(headRefOid="")), "no head branch or head SHA"),
    ],
    ids=["non-zero-exit", "timeout", "missing-binary", "invalid-json", "empty-branch", "empty-sha"],
)
def test_gh_failures_raise_review_target_error(tmp_path, gh, message):
    with pytest.raises(ReviewTargetError, match=message):
        resolve_review_target(str(tmp_path), "90", run_gh=gh)


def test_gh_failure_message_is_redacted(tmp_path, monkeypatch):
    token = make_gh_probe()
    monkeypatch.setenv("GITHUB_TOKEN", token)
    gh = FakeGh(returncode=1, stderr=f"HTTP 401 using {token}")

    with pytest.raises(ReviewTargetError) as excinfo:
        resolve_review_target(str(tmp_path), "90", run_gh=gh)

    assert_no_fragment(str(excinfo.value), token)


def test_default_runner_raises_without_gh(tmp_path, monkeypatch):
    monkeypatch.setattr(rt.shutil, "which", lambda name: None)

    with pytest.raises(ReviewTargetError, match="gh is not available"):
        resolve_review_target(str(tmp_path), "90")


def test_empty_target_raises(tmp_path):
    with pytest.raises(ReviewTargetError, match="empty"):
        resolve_review_target(str(tmp_path), "  ")


# --------------------------------------------------------------------------------------
# resolve_review_target: branch targets
# --------------------------------------------------------------------------------------


def test_branch_target_resolves_to_its_full_tip_sha(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "feature")
    tip = _commit(repo, "app.py", "x = 2\n", "feature")
    _git(repo, "checkout", "-q", "main")

    resolved = resolve_review_target(str(repo), "feature")

    assert resolved == ReviewTarget(raw="feature", head_branch="feature", head_sha=tip)
    assert not resolved.is_pr


def test_unknown_branch_raises(tmp_path):
    repo = _repo(tmp_path)

    with pytest.raises(ReviewTargetError, match="could not be resolved"):
        resolve_review_target(str(repo), "does-not-exist")


# --------------------------------------------------------------------------------------
# verify_review_diff
# --------------------------------------------------------------------------------------


def _execution_repo(tmp_path: Path, change_source: bool) -> Path:
    repo = _repo(tmp_path, branch=PLAN)
    _git(repo, "checkout", "-q", "-b", EXEC)
    _commit(repo, "holon-knowledge/ledger/executions.jsonl", '{"status": "success"}\n', "ledger")
    if change_source:
        _commit(repo, "app.py", "x = 2\n", "implement")
    return repo


def test_execution_branch_identical_to_its_plan_is_empty(tmp_path):
    repo = _execution_repo(tmp_path, change_source=False)

    verdict, _detail = verify_review_diff(str(repo), EXEC)

    assert verdict is DiffVerdict.EMPTY


def test_execution_branch_that_changes_code_is_verified(tmp_path):
    repo = _execution_repo(tmp_path, change_source=True)

    verdict, detail = verify_review_diff(str(repo), EXEC)

    assert verdict is DiffVerdict.VERIFIED
    assert "1 changed path(s)" in detail


def test_unresolved_pr_reference_is_unverified(tmp_path):
    repo = _repo(tmp_path)

    verdict, detail = verify_review_diff(str(repo), "90")

    assert verdict is DiffVerdict.UNVERIFIED
    assert "not resolved" in detail


def test_pr_head_equal_to_its_base_is_empty(tmp_path):
    repo = _repo(tmp_path)
    base_sha = _git(repo, "rev-parse", "main")
    target = ReviewTarget(raw="90", head_branch="feature/x", head_sha=base_sha, pr_number=90)

    verdict, _detail = verify_review_diff(str(repo), target, base_branch="main")

    assert verdict is DiffVerdict.EMPTY


def _release_repo(tmp_path: Path) -> tuple[Path, str]:
    """A repository whose 'release' branch carries a code change that 'main' does not."""
    repo = _repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "release")
    release_sha = _commit(repo, "app.py", "x = 2\n", "release change")
    _git(repo, "checkout", "-q", "main")
    return repo, release_sha


def test_pr_head_equal_to_its_non_main_base_is_empty(tmp_path):
    repo, release_sha = _release_repo(tmp_path)
    target = ReviewTarget(raw="90", head_branch="feature/x", head_sha=release_sha, pr_number=90, base_branch="release")

    verdict, detail = verify_review_diff(str(repo), target, base_branch="main")

    assert verdict is DiffVerdict.EMPTY
    assert "release" in detail


def test_pr_base_is_honoured_over_the_target_branch(tmp_path):
    repo, release_sha = _release_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "feature/x", "release")
    head = _commit(repo, "lib.py", "y = 1\n", "feature change")
    _git(repo, "checkout", "-q", "main")
    on_release = ReviewTarget(
        raw="90", head_branch="feature/x", head_sha=release_sha, pr_number=90, base_branch="release"
    )
    on_main = ReviewTarget(raw="90", head_branch="feature/x", head_sha=release_sha, pr_number=90)

    assert verify_review_diff(str(repo), on_main, base_branch="main")[0] is DiffVerdict.VERIFIED
    assert verify_review_diff(str(repo), on_release, base_branch="main")[0] is DiffVerdict.EMPTY
    changed = ReviewTarget(raw="90", head_branch="feature/x", head_sha=head, pr_number=90, base_branch="release")
    verdict, detail = verify_review_diff(str(repo), changed, base_branch="main")
    assert verdict is DiffVerdict.VERIFIED
    assert detail.startswith("1 changed path(s) relative to")
    assert detail.endswith("release")


def test_pr_head_with_a_code_change_is_verified(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "feature/x")
    head = _commit(repo, "app.py", "x = 3\n", "change")
    target = ReviewTarget(raw="90", head_branch="feature/x", head_sha=head, pr_number=90)

    verdict, _detail = verify_review_diff(str(repo), target, base_branch="main")

    assert verdict is DiffVerdict.VERIFIED


def test_pr_head_missing_from_the_repository_is_unverified(tmp_path):
    repo = _repo(tmp_path)
    target = ReviewTarget(raw="90", head_branch="feature/x", head_sha=HEAD_SHA, pr_number=90)

    verdict, _detail = verify_review_diff(str(repo), target, base_branch="main")

    assert verdict is DiffVerdict.UNVERIFIED


def test_plain_branch_without_changes_is_empty_and_with_changes_verified(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "branch", "same")
    _git(repo, "checkout", "-q", "-b", "changed")
    _commit(repo, "lib.py", "y = 1\n", "add lib")

    assert verify_review_diff(str(repo), "same")[0] is DiffVerdict.EMPTY
    assert verify_review_diff(str(repo), "changed")[0] is DiffVerdict.VERIFIED


def test_missing_base_branch_is_unverified(tmp_path):
    repo = _repo(tmp_path)

    verdict, detail = verify_review_diff(str(repo), "main", base_branch="trunk")

    assert verdict is DiffVerdict.UNVERIFIED
    assert "trunk" in detail


class FakeOriginGit:
    """Wraps the real ``run_git`` and fakes an 'origin' remote plus the outcome of every fetch from it."""

    def __init__(self, real: Any, fetch_returncode: int, fetch_stderr: str = "", failing_branch: str | None = None):
        self.real = real
        self.fetch_returncode = fetch_returncode
        self.fetch_stderr = fetch_stderr
        self.failing_branch = failing_branch
        self.fetches: list[list[str]] = []

    def _fetch_returncode(self, args: list[str]) -> int:
        """Fail every fetch, or only the fetch of ``failing_branch`` when one is named."""
        if self.failing_branch is None or f"refs/heads/{self.failing_branch}:" in " ".join(args):
            return self.fetch_returncode
        return 0

    def __call__(self, args: list[str], *a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
        if args[:3] == ["remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(args, 0, stdout="https://github.com/acme/widgets.git\n", stderr="")
        if args[:1] == ["fetch"]:
            self.fetches.append(args)
            code = self._fetch_returncode(args)
            return subprocess.CompletedProcess(args, code, stdout="", stderr=self.fetch_stderr if code else "")
        return self.real(args, *a, **kw)


def _fake_origin(
    monkeypatch: pytest.MonkeyPatch, fetch_returncode: int, fetch_stderr: str = "", failing_branch: str | None = None
) -> FakeOriginGit:
    from sandbox_executor import flow

    fake = FakeOriginGit(flow.run_git, fetch_returncode, fetch_stderr, failing_branch)
    monkeypatch.setattr(flow, "run_git", fake)
    return fake


def test_pr_base_that_cannot_be_fetched_from_origin_is_unverified(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    base_sha = _git(repo, "rev-parse", "main")
    target = ReviewTarget(raw="90", head_branch="feature/x", head_sha=base_sha, pr_number=90, base_branch="main")
    fake = _fake_origin(monkeypatch, fetch_returncode=128, fetch_stderr="fatal: could not read from remote")

    verdict, detail = verify_review_diff(str(repo), target)

    assert verdict is DiffVerdict.UNVERIFIED
    assert detail == "base branch 'main' could not be fetched from origin: fatal: could not read from remote"
    assert fake.fetches == [["fetch", "--no-tags", "origin", "+refs/heads/main:refs/remotes/origin/main"]]


def test_pr_base_fetched_from_origin_keeps_the_empty_and_verified_verdicts(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    base_sha = _git(repo, "rev-parse", "main")
    _git(repo, "checkout", "-q", "-b", "feature/x")
    head = _commit(repo, "app.py", "x = 3\n", "change")
    fake = _fake_origin(monkeypatch, fetch_returncode=0)

    same = ReviewTarget(raw="90", head_branch="feature/x", head_sha=base_sha, pr_number=90, base_branch="main")
    changed = ReviewTarget(raw="90", head_branch="feature/x", head_sha=head, pr_number=90, base_branch="main")

    assert verify_review_diff(str(repo), same)[0] is DiffVerdict.EMPTY
    assert verify_review_diff(str(repo), changed)[0] is DiffVerdict.VERIFIED
    assert len(fake.fetches) == 2


def test_pr_base_without_origin_uses_the_local_branch(tmp_path, monkeypatch):
    """With no 'origin' remote there is nothing to fetch, so the local base branch is the base.

    A repository without origin is a local-only checkout: no PR is opened from a remote copy, so the
    local branch is the only base there is, and the guard must not refuse it for lacking a fetch.
    """
    from sandbox_executor import flow

    repo = _repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "feature/x")
    head = _commit(repo, "app.py", "x = 3\n", "change")
    calls: list[list[str]] = []
    real = flow.run_git

    def recording(args: list[str], *a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return real(args, *a, **kw)

    monkeypatch.setattr(flow, "run_git", recording)
    target = ReviewTarget(raw="90", head_branch="feature/x", head_sha=head, pr_number=90, base_branch="main")

    verdict, detail = verify_review_diff(str(repo), target)

    assert verdict is DiffVerdict.VERIFIED
    assert detail == "1 changed path(s) relative to refs/heads/main"
    assert not [call for call in calls if call[:1] == ["fetch"]]


def test_plain_branch_head_that_cannot_be_fetched_from_origin_is_unverified(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "feature/x")
    _commit(repo, "app.py", "x = 3\n", "unpushed change")
    fake = _fake_origin(monkeypatch, 128, "fatal: couldn't find remote ref feature/x", failing_branch="feature/x")

    verdict, detail = verify_review_diff(str(repo), "feature/x")

    assert verdict is DiffVerdict.UNVERIFIED
    assert detail == (
        "head branch 'feature/x' could not be fetched from origin: fatal: couldn't find remote ref feature/x"
    )
    assert fake.fetches == [
        ["fetch", "--no-tags", "origin", "+refs/heads/main:refs/remotes/origin/main"],
        ["fetch", "--no-tags", "origin", "+refs/heads/feature/x:refs/remotes/origin/feature/x"],
    ]


def test_plain_branch_head_fetched_from_origin_keeps_the_empty_and_verified_verdicts(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _git(repo, "branch", "same")
    _git(repo, "checkout", "-q", "-b", "changed")
    _commit(repo, "lib.py", "y = 1\n", "add lib")
    fake = _fake_origin(monkeypatch, fetch_returncode=0)

    assert verify_review_diff(str(repo), "same")[0] is DiffVerdict.EMPTY
    assert verify_review_diff(str(repo), "changed")[0] is DiffVerdict.VERIFIED
    head_fetches = [call[-1] for call in fake.fetches if "main:" not in call[-1]]
    assert head_fetches == [
        "+refs/heads/same:refs/remotes/origin/same",
        "+refs/heads/changed:refs/remotes/origin/changed",
    ]


def test_plain_branch_head_without_origin_uses_the_local_branch_and_never_fetches(tmp_path, monkeypatch):
    from sandbox_executor import flow

    repo = _repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "feature/x")
    _commit(repo, "app.py", "x = 3\n", "change")
    calls: list[list[str]] = []
    real = flow.run_git

    def recording(args: list[str], *a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return real(args, *a, **kw)

    monkeypatch.setattr(flow, "run_git", recording)

    verdict, detail = verify_review_diff(str(repo), "feature/x")

    assert verdict is DiffVerdict.VERIFIED
    assert detail == "1 changed path(s) relative to refs/heads/main"
    assert not [call for call in calls if call[:1] == ["fetch"]]


@pytest.mark.parametrize("raised", ["helper", "subprocess"])
def test_git_timeout_during_the_diff_check_is_unverified_and_redacted(tmp_path, monkeypatch, raised):
    from sandbox_executor import flow

    repo = _repo(tmp_path)
    secret = "ghp_" + "A" * 36

    def timing_out(args: list[str], *a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
        if args[:3] == ["remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if raised == "helper":
            raise TimeoutError(f"Git command timed out after 300s: git fetch https://x:{secret}@github.com/a/b")
        raise subprocess.TimeoutExpired(["git", "fetch", f"https://x:{secret}@github.com/a/b"], 300)

    monkeypatch.setattr(flow, "run_git", timing_out)

    verdict, detail = verify_review_diff(str(repo), "feature/x")

    assert verdict is DiffVerdict.UNVERIFIED
    assert detail.startswith("git timed out while checking the review diff: ")
    assert secret not in detail


def test_verify_review_diff_never_calls_gh(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(rt.shutil, "which", lambda name: None)

    assert verify_review_diff(str(repo), "main")[0] is DiffVerdict.EMPTY


# --------------------------------------------------------------------------------------
# classify_review_diff: resolve a raw PR reference, then run the guard
# --------------------------------------------------------------------------------------


def test_classify_review_diff_resolves_a_pr_and_diffs_against_its_base(tmp_path):
    repo, release_sha = _release_repo(tmp_path)
    gh = FakeGh(stdout=_pr_json(headRefOid=release_sha, baseRefName="release"))

    verdict, _detail = classify_review_diff(str(repo), "90", base_branch="main", run_gh=gh)

    assert verdict is DiffVerdict.EMPTY
    assert len(gh.calls) == 1


def test_classify_review_diff_is_unverified_when_the_pr_cannot_be_resolved(tmp_path):
    repo = _repo(tmp_path)

    verdict, detail = classify_review_diff(str(repo), "90", run_gh=FakeGh(returncode=1, stderr="not found"))

    assert verdict is DiffVerdict.UNVERIFIED
    assert "not found" in detail


def test_classify_review_diff_passes_a_branch_straight_to_the_guard(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "branch", "same")
    gh = FakeGh()

    assert classify_review_diff(str(repo), "same", run_gh=gh)[0] is DiffVerdict.EMPTY
    assert gh.calls == []
