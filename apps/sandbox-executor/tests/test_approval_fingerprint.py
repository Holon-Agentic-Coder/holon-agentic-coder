"""The PR-own change fingerprint: what it binds, what it excludes, and its invariance across merges of a moving main.

Every case builds real temporary git repositories (a bare ``origin`` and a clone); nothing touches the network or the
repository's own worktree. The Hypothesis properties are derandomized and bounded, because each example builds repos.
"""

from __future__ import annotations

import atexit
import json
import shutil
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st
from sandbox_executor.flow.review.approval_fingerprint import (
    FINGERPRINT_VERSION,
    approval_fingerprint_of,
    fingerprints_match,
    pr_change_fingerprint,
)
from sandbox_executor.flow.review.conflict_merge import _merge_target_ledger_only

from tests.flow.helpers import _git

BRANCH = "I-1-x/P-2-y/E-3-z/_"
LEDGER = "holon-knowledge/ledger/executions.jsonl"
TARGET = "refs/remotes/origin/main"
PR_PATHS = ("src/pr_a.py", "src/pr_b.py", "src/pkg/pr_c.py", "docs/pr_d.md")
MAIN_PATHS = ("src/main_a.py", "src/main_b.py", "lib/main_c.py")
CONTENT = st.text(alphabet="abcxyz =+\n", min_size=1, max_size=40)
PROPERTY_SETTINGS = settings(max_examples=25, deadline=None, derandomize=True, database=None)


def _write(work: Path, rel: str, text: str) -> None:
    path = work / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(work: Path, files: dict[str, str], message: str) -> str:
    for rel, text in files.items():
        _write(work, rel, text)
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "--allow-empty", "-m", message)
    return _git(work, "rev-parse", "HEAD")


def _ledger_with(work: Path, execution_id: str) -> str:
    path = work / LEDGER
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    row = {"execution_id": execution_id, "created_at": f"2026-11-{len(old.splitlines()) + 1:02d}T00:00:00Z"}
    return old + json.dumps(row, separators=(",", ":")) + "\n"


_TEMPLATE_ROOT: Path | None = None


def _template_repos() -> Path:
    """Build the base bare origin and clone once per worker so each case copies it instead of running git init/clone."""
    global _TEMPLATE_ROOT
    if _TEMPLATE_ROOT is None:
        tmp = tempfile.TemporaryDirectory(prefix="holon-fingerprint-template-")
        atexit.register(tmp.cleanup)
        root = Path(tmp.name)
        origin, work = root / "origin.git", root / "work"
        _git(root, "init", "-q", "--bare", "-b", "main", str(origin))
        _git(root, "clone", "-q", str(origin), str(work))
        _git(work, "remote", "set-url", "origin", "../origin.git")
        _commit(work, {"README.md": "base\n", LEDGER: _ledger_with(work, "E-base")}, "base")
        _git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
        _git(work, "update-ref", TARGET, "HEAD")
        _git(work, "checkout", "-q", "-b", BRANCH)
        _TEMPLATE_ROOT = root
    return _TEMPLATE_ROOT


def _repos(root: Path, pr_files: dict[str, str]) -> Path:
    """A bare origin and a clone on ``BRANCH`` whose own change is ``pr_files`` plus one ledger row."""
    template = _template_repos()
    shutil.copytree(template / "origin.git", root / "origin.git")
    work = root / "work"
    shutil.copytree(template / "work", work)
    _commit(work, {**pr_files, LEDGER: _ledger_with(work, "E-pr")}, "pr change")
    return work


def _advance_main(work: Path, files: dict[str, str], label: str) -> None:
    """Commit ``files`` and one ledger row on origin's main, leaving the clone on ``BRANCH``."""
    _git(work, "checkout", "-q", "main")
    _git(work, "merge", "-q", "--ff-only", TARGET)
    _commit(work, {**files, LEDGER: _ledger_with(work, f"E-main-{label}")}, f"main {label}")
    _git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    _git(work, "update-ref", TARGET, "HEAD")
    _git(work, "checkout", "-q", BRANCH)


def _merge_main(work: Path, label: str) -> None:
    """Append a branch ledger row, then merge origin/main the way the conflict gate does (ledger rows reconciled)."""
    _commit(work, {LEDGER: _ledger_with(work, f"E-pr-{label}")}, f"pr row {label}")
    outcome = _merge_target_ledger_only(str(work), TARGET, f"Merge main ({label})")
    assert outcome.merged, outcome


def _fingerprint(work: Path) -> str | None:
    return pr_change_fingerprint(str(work), "HEAD", TARGET)


def test_fingerprint_is_versioned_and_stable(tmp_path: Path) -> None:
    work = _repos(tmp_path, {"src/pr_a.py": "x = 1\n"})
    first = _fingerprint(work)
    assert first is not None and first.startswith(f"{FINGERPRINT_VERSION}:") and len(first) == 44
    assert _fingerprint(work) == first
    assert approval_fingerprint_of(str(work), _git(work, "rev-parse", "HEAD"), "main") == first


def test_excluded_paths_do_not_change_the_fingerprint(tmp_path: Path) -> None:
    work = _repos(tmp_path, {"src/pr_a.py": "x = 1\n"})
    before = _fingerprint(work)
    _commit(
        work,
        {
            "holon-knowledge/kb/note.md": "learnt\n",
            LEDGER: _ledger_with(work, "E-late"),
            "plans/I-1-x/P-2-y_calibration.md": "# calibration\n",
            "plans/P-2-y_calibration.md": "# flat calibration\n",
        },
        "bookkeeping",
    )
    assert _fingerprint(work) == before


def test_code_edit_changes_the_fingerprint(tmp_path: Path) -> None:
    work = _repos(tmp_path, {"src/pr_a.py": "x = 1\n"})
    before = _fingerprint(work)
    _commit(work, {"src/pr_a.py": "x = 2\n"}, "edit")
    assert _fingerprint(work) != before


def test_non_calibration_plan_edit_changes_the_fingerprint(tmp_path: Path) -> None:
    work = _repos(tmp_path, {"src/pr_a.py": "x = 1\n"})
    before = _fingerprint(work)
    _commit(work, {"plans/I-1-x/P-2-y.md": "# plan\n"}, "plan")
    assert _fingerprint(work) != before


def test_mode_only_change_changes_the_fingerprint(tmp_path: Path) -> None:
    work = _repos(tmp_path, {"scripts/run.sh": "echo hi\n"})
    before = _fingerprint(work)
    _git(work, "update-index", "--chmod=+x", "scripts/run.sh")
    _git(work, "commit", "-q", "-m", "chmod")
    assert _fingerprint(work) != before


def test_binary_change_changes_the_fingerprint(tmp_path: Path) -> None:
    work = _repos(tmp_path, {"src/pr_a.py": "x = 1\n"})
    (work / "assets").mkdir()
    (work / "assets/blob.bin").write_bytes(b"\x00\x01binary")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "binary")
    before = _fingerprint(work)
    (work / "assets/blob.bin").write_bytes(b"\x00\x02binary")
    _git(work, "commit", "-q", "-am", "binary edit")
    assert before is not None and _fingerprint(work) != before


def test_empty_pr_change_has_no_fingerprint(tmp_path: Path) -> None:
    work = _repos(tmp_path, {})
    assert _fingerprint(work) is None


def test_unknown_refs_have_no_fingerprint(tmp_path: Path) -> None:
    work = _repos(tmp_path, {"src/pr_a.py": "x = 1\n"})
    assert pr_change_fingerprint(str(work), "HEAD", "refs/remotes/origin/missing") is None
    assert pr_change_fingerprint(str(work), "0" * 40, TARGET) is None
    assert approval_fingerprint_of(str(work), "HEAD", "main") is None
    assert approval_fingerprint_of(str(work), None, "main") is None
    assert approval_fingerprint_of(str(work), "abc", None) is None
    assert approval_fingerprint_of(str(tmp_path / "no-repo"), "abc", "main") is None


def test_hand_resolved_code_conflict_changes_the_fingerprint(tmp_path: Path) -> None:
    work = _repos(tmp_path, {"src/pr_a.py": "x = 1\n"})
    before = _fingerprint(work)
    _advance_main(work, {"src/pr_a.py": "x = 3\n"}, "clash")
    outcome = _merge_target_ledger_only(str(work), TARGET, "Merge main (clash)")
    assert not outcome.merged and "src/pr_a.py" in outcome.conflicting_paths
    _git(work, "merge", "--no-ff", "-X", "theirs", TARGET, "-m", "hand merge")
    _commit(work, {"src/pr_a.py": "x = 1\nx = 3\n"}, "hand resolution")
    assert _fingerprint(work) != before


def test_clean_merge_of_disjoint_main_keeps_the_fingerprint(tmp_path: Path) -> None:
    work = _repos(tmp_path, {"src/pr_a.py": "x = 1\n"})
    before = _fingerprint(work)
    _advance_main(work, {"src/main_a.py": "y = 1\n"}, "one")
    _merge_main(work, "one")
    assert _fingerprint(work) == before


def test_none_never_matches() -> None:
    assert not fingerprints_match(None, None)
    assert not fingerprints_match("", "")
    assert not fingerprints_match("pf1:a", None)
    assert not fingerprints_match("pf1:a", "pf1:b")
    assert fingerprints_match("pf1:a", "pf1:a")


PR_CHANGES = st.dictionaries(st.sampled_from(PR_PATHS), CONTENT, min_size=1)
MAIN_MOVES = st.lists(st.dictionaries(st.sampled_from(MAIN_PATHS), CONTENT), min_size=0, max_size=3)


def _merged_repos(root: Path, pr_files: dict[str, str], main_moves: list[dict[str, str]]) -> tuple[Path, str | None]:
    """The clone after ``len(main_moves)`` merges of a moving main, and the fingerprint before the first merge."""
    work = _repos(root, pr_files)
    before = _fingerprint(work)
    for index, files in enumerate(main_moves):
        _advance_main(work, files, str(index))
        _merge_main(work, str(index))
        assert _fingerprint(work) == before, f"fingerprint changed after merge {index + 1}"
    return work, before


@PROPERTY_SETTINGS
@given(pr_files=PR_CHANGES, main_moves=MAIN_MOVES)
def test_property_fingerprint_survives_merges_of_a_disjoint_moving_main(
    pr_files: dict[str, str], main_moves: list[dict[str, str]]
) -> None:
    with tempfile.TemporaryDirectory(prefix="holon-fingerprint-") as root:
        work, before = _merged_repos(Path(root), pr_files, main_moves)
        assert before is not None
        assert _fingerprint(work) == before


@PROPERTY_SETTINGS
@given(pr_files=PR_CHANGES, main_moves=MAIN_MOVES, data=st.data())
def test_property_fingerprint_changes_when_a_pr_file_changes(
    pr_files: dict[str, str], main_moves: list[dict[str, str]], data: st.DataObject
) -> None:
    with tempfile.TemporaryDirectory(prefix="holon-fingerprint-") as root:
        work, before = _merged_repos(Path(root), pr_files, main_moves)
        path = data.draw(st.sampled_from(PR_PATHS), label="edited PR path")
        old = (work / path).read_text(encoding="utf-8") if (work / path).exists() else ""
        new = data.draw(CONTENT.filter(lambda text: text != old), label="new content")
        _commit(work, {path: new}, "post-approval push")
        assert _fingerprint(work) != before
