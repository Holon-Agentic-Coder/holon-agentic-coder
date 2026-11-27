"""The PR-own change fingerprint: what it binds, what it excludes, and its invariance across merges of a moving main.

Every case works in real temporary git repositories (a bare ``origin`` and a clone); nothing touches the network or
the repository's own worktree. The starting repositories are built once per session and copied
(``tests/repo_templates.py``). The Hypothesis property is derandomized and bounded, because each example copies the
template and then commits, pushes and merges in its copy. Its boundary cases are explicit examples, so they do not
depend on what is generated. Two of them are also the single-case forms of the two halves of the property: no main
move and an edit of the PR's one file (a code edit changes the fingerprint), and a clean merge of a main that moved in
another file (the fingerprint is kept).
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from sandbox_executor.flow.review.approval_fingerprint import (
    BOOKKEEPING_PATH_SUFFIXES,
    FINGERPRINT_VERSION,
    approval_fingerprint_of,
    fingerprints_match,
    is_bookkeeping_path,
    pr_change_fingerprint,
    reviewable_pathspecs,
)
from sandbox_executor.flow.review.conflict_merge import _merge_target_ledger_only

from tests.flow.helpers import _git
from tests.flow_repo_templates import GraphCommit, fast_import
from tests.repo_templates import copy_template, shared_template

BRANCH = "I-1-x/P-2-y/E-3-z/_"
LEDGER = "holon-knowledge/ledger/executions.jsonl"
TARGET = "refs/remotes/origin/main"
PR_PATHS = ("src/pr_a.py", "src/pr_b.py", "src/pkg/pr_c.py", "docs/pr_d.md")
MAIN_PATHS = ("src/main_a.py", "src/main_b.py", "lib/main_c.py")
CONTENT = st.text(alphabet="abcxyz =+\n", min_size=1, max_size=40)
PROPERTY_SETTINGS = settings(max_examples=12, deadline=None, derandomize=True, database=None)


def _write(work: Path, rel: str, text: str) -> None:
    path = work / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(work: Path, files: dict[str, str], message: str) -> None:
    for rel, text in files.items():
        _write(work, rel, text)
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "--allow-empty", "-m", message)


def _ledger_row(execution_id: str, day: int) -> str:
    row = {"execution_id": execution_id, "created_at": f"2026-11-{day:02d}T00:00:00Z"}
    return json.dumps(row, separators=(",", ":")) + "\n"


def _ledger_with(work: Path, execution_id: str) -> str:
    path = work / LEDGER
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    return old + _ledger_row(execution_id, len(old.splitlines()) + 1)


def _build_repos(root: Path) -> None:
    """A bare origin with the pushed base commit and a clone on ``BRANCH``."""
    origin, work = root / "origin.git", root / "work"
    _git(root, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(root, "clone", "-q", str(origin), str(work))
    _commit(work, {"README.md": "base\n", LEDGER: _ledger_with(work, "E-base")}, "base")
    _git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    _git(work, "checkout", "-q", "-b", BRANCH)
    _git(work, "fetch", "-q", "origin")


@pytest.fixture(scope="session")
def repos_template(tmp_path_factory: pytest.TempPathFactory, hermetic_git_config: str) -> Path:
    """The repositories of ``_build_repos``, built once and read-only; ``_repos`` copies them."""
    return shared_template(tmp_path_factory, "fingerprint-template", _build_repos)[0]


BOOKKEEPING_CASES: dict[str, dict[str, str | None]] = {
    "execution": {"executions/E-1.md": "# top-level execution record\n"},
    "plan": {"plans/I-1-x/P-2-y.md": "# plan\n", "plans/I-1-x/P-2-y_calibration.md": "# calibration\n"},
    "plan-json": {"plans/P-2-y_calibration.md": "# flat calibration\n", "plans/meta.json": "{}\n"},
    "knowledge-base": {"holon-knowledge/kb/note.md": "learnt\n", "holon-knowledge/kb/deep/rows.jsonl": "{}\n"},
    "ledger": {LEDGER: None},
}
REVIEWABLE_PATHS = (
    "plans/tool.py",
    "executions/run.sh",
    "holon-knowledge/kb/loader.py",
    "apps/sandbox-executor/src/x.py",
    "scripts/gate.py",
    "docs/guide.md",
    "holon-config/world/rules.md",
    "README.md",
    "planning/notes.md",
)


def _init_bare(root: Path) -> None:
    _git(root, "init", "-q", "--bare", "-b", "main", str(root / "repo.git"))


def _build_path_cases(root: Path) -> dict[str, str]:
    repo = root / "cases.git"
    _git(root, "init", "-q", "--bare", "-b", "main", str(repo))
    base_row, pr_row, late_row = _ledger_row("E-base", 1), _ledger_row("E-pr", 2), _ledger_row("E-late", 3)
    commits = [
        GraphCommit("main", {"README.md": "base\n", LEDGER: base_row}, "base"),
        GraphCommit(BRANCH, {"src/pr_a.py": "x = 1\n", LEDGER: base_row + pr_row}, "pr change", (0,)),
    ]
    labels = ["base", "pr"]
    for case_id, files in BOOKKEEPING_CASES.items():
        resolved = {rel: text or (base_row + pr_row + late_row) for rel, text in files.items()}
        commits.append(GraphCommit(BRANCH, resolved, f"bookkeeping {case_id}", (1,)))
        labels.append(f"bookkeeping:{case_id}")
    for rel in REVIEWABLE_PATHS:
        commits.append(GraphCommit(BRANCH, {rel: "changed\n"}, f"reviewable {rel}", (1,)))
        labels.append(f"reviewable:{rel}")
    commits.append(
        GraphCommit(
            BRANCH,
            {"executions/E-1.md": "# record\n", "plans/P-1.md": "# plan\n", LEDGER: base_row + pr_row},
            "bookkeeping only",
            (0,),
        )
    )
    labels.append("bookkeeping_only")
    commits.append(GraphCommit(BRANCH, {LEDGER: base_row + pr_row}, "empty pr", (0,)))
    labels.append("empty_pr")
    shas = fast_import(repo, commits)
    return dict(zip(labels, shas, strict=True))


@pytest.fixture(scope="session")
def bare_template(tmp_path_factory: pytest.TempPathFactory, hermetic_git_config: str) -> Path:
    return shared_template(tmp_path_factory, "fingerprint-bare-template", _init_bare)[0]


@pytest.fixture(scope="session")
def path_cases_repo(
    tmp_path_factory: pytest.TempPathFactory, hermetic_git_config: str
) -> tuple[Path, dict[str, str], str]:
    root, shas = shared_template(tmp_path_factory, "fingerprint-path-cases", _build_path_cases)
    repo = root / "cases.git"
    before = pr_change_fingerprint(str(repo), shas["pr"], shas["base"])
    assert before is not None
    return repo, shas, before


def _repos(template: Path, root: Path, pr_files: dict[str, str]) -> Path:
    """A bare origin and a clone on ``BRANCH`` whose own change is ``pr_files`` plus one ledger row."""
    work = copy_template(template, root) / "work"
    _commit(work, {**pr_files, LEDGER: _ledger_with(work, "E-pr")}, "pr change")
    return work


def _advance_main(work: Path, files: dict[str, str], label: str) -> None:
    """Commit ``files`` and one ledger row on origin's main, leaving the clone on ``BRANCH``."""
    # Local main is always behind or at origin/main here, so this is the checkout plus fast-forward in one process.
    _git(work, "checkout", "-q", "--no-track", "-B", "main", TARGET)
    _commit(work, {**files, LEDGER: _ledger_with(work, f"E-main-{label}")}, f"main {label}")
    # The push also moves refs/remotes/origin/main of the clone, so no fetch is needed afterwards.
    _git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    _git(work, "checkout", "-q", BRANCH)


def _merge_main(work: Path, label: str) -> None:
    """Append a branch ledger row, then merge origin/main the way the conflict gate does (ledger rows reconciled)."""
    _commit(work, {LEDGER: _ledger_with(work, f"E-pr-{label}")}, f"pr row {label}")
    outcome = _merge_target_ledger_only(str(work), TARGET, f"Merge main ({label})")
    assert outcome.merged, outcome


def _fingerprint(work: Path) -> str | None:
    return pr_change_fingerprint(str(work), "HEAD", TARGET)


def test_fingerprint_is_versioned_and_stable(repos_template: Path, tmp_path: Path) -> None:
    work = _repos(repos_template, tmp_path, {"src/pr_a.py": "x = 1\n"})
    first = _fingerprint(work)
    assert first is not None and first.startswith(f"{FINGERPRINT_VERSION}:") and len(first) == 44
    assert _fingerprint(work) == first
    assert approval_fingerprint_of(str(work), _git(work, "rev-parse", "HEAD"), "main") == first


@pytest.mark.parametrize("case_id", list(BOOKKEEPING_CASES))
def test_bookkeeping_paths_do_not_change_the_fingerprint(
    path_cases_repo: tuple[Path, dict[str, str], str], case_id: str
) -> None:
    repo, shas, before = path_cases_repo
    assert pr_change_fingerprint(str(repo), shas[f"bookkeeping:{case_id}"], shas["base"]) == before


@pytest.mark.parametrize("rel", REVIEWABLE_PATHS)
def test_every_reviewable_path_changes_the_fingerprint(
    path_cases_repo: tuple[Path, dict[str, str], str], rel: str
) -> None:
    repo, shas, before = path_cases_repo
    assert pr_change_fingerprint(str(repo), shas[f"reviewable:{rel}"], shas["base"]) not in (before, None)


def test_bookkeeping_only_pr_change_has_no_fingerprint(path_cases_repo: tuple[Path, dict[str, str], str]) -> None:
    repo, shas, _ = path_cases_repo
    assert pr_change_fingerprint(str(repo), shas["bookkeeping_only"], shas["base"]) is None


def test_version_is_pf2_and_never_matches_pf1() -> None:
    assert FINGERPRINT_VERSION == "pf2"
    assert not fingerprints_match("pf1:" + "a" * 40, "pf2:" + "a" * 40)


@pytest.mark.parametrize(
    ("path", "bookkeeping"),
    [
        ("executions/E-1.md", True),
        ("plans/I-1/P-2.md", True),
        ("holon-knowledge/ledger/executions.jsonl", True),
        ("holon-knowledge/kb/x.json", True),
        ("plans/tool.py", False),
        ("docs/x.md", False),
        ("planning/x.md", False),
        ("executions", False),
        ("plans/.json", False),
        ("plans/..json", False),
        ("plans/a..json", True),
        ("plans/.x.json", True),
    ],
)
def test_is_bookkeeping_path(path: str, bookkeeping: bool) -> None:
    assert is_bookkeeping_path(path) is bookkeeping


def test_bookkeeping_definition_matches_the_shared_ci_definition() -> None:
    from sandbox_executor.flow.github.ci_final_head import BOOKKEEPING_PATH_PREFIXES

    from scripts.ci_bookkeeping_gate import BOOKKEEPING_ROOTS, BOOKKEEPING_SUFFIXES
    from scripts.ci_bookkeeping_gate import is_bookkeeping_path as ci_is_bookkeeping_path

    assert tuple(prefix.rstrip("/") for prefix in BOOKKEEPING_PATH_PREFIXES) == BOOKKEEPING_ROOTS
    assert BOOKKEEPING_PATH_SUFFIXES == BOOKKEEPING_SUFFIXES
    expected = {
        f":(exclude,glob){root}/**/*[!.]*{suffix}" for root in BOOKKEEPING_ROOTS for suffix in BOOKKEEPING_SUFFIXES
    }
    assert set(reviewable_pathspecs()[2:]) == expected and reviewable_pathspecs()[:2] == ("--", ".")
    for path in ("executions/E-1.md", "plans/a/b.json", "plans/tool.py", "docs/x.md", "holon-knowledge/l.jsonl"):
        assert is_bookkeeping_path(path) is ci_is_bookkeeping_path(path)
    for path in ("plans/.json", "plans/..json", "plans/a/.md", "plans/a..json", "plans/.x.json"):
        assert is_bookkeeping_path(path) is ci_is_bookkeeping_path(path)


def test_excluded_pathspecs_select_exactly_the_bookkeeping_paths(tmp_path: Path) -> None:
    paths = ["plans/.json", "plans/..json", "plans/a.json", "plans/a..json", "plans/.x.json", "plans/x/.md"]
    paths.append("plans/t.py")
    for path in paths:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text("1\n")
    subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    listed = subprocess.run(
        ["git", "ls-files", *reviewable_pathspecs()], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.split()
    assert sorted(listed) == sorted(path for path in paths if not is_bookkeeping_path(path))


def test_mode_only_change_changes_the_fingerprint(repos_template: Path, tmp_path: Path) -> None:
    work = _repos(repos_template, tmp_path, {"scripts/run.sh": "echo hi\n"})
    before = _fingerprint(work)
    _git(work, "update-index", "--chmod=+x", "scripts/run.sh")
    _git(work, "commit", "-q", "-m", "chmod")
    assert _fingerprint(work) != before


def test_binary_change_changes_the_fingerprint(repos_template: Path, tmp_path: Path) -> None:
    work = _repos(repos_template, tmp_path, {"src/pr_a.py": "x = 1\n"})
    (work / "assets").mkdir()
    (work / "assets/blob.bin").write_bytes(b"\x00\x01binary")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "binary")
    before = _fingerprint(work)
    (work / "assets/blob.bin").write_bytes(b"\x00\x02binary")
    _git(work, "commit", "-q", "-am", "binary edit")
    assert before is not None and _fingerprint(work) != before


def test_empty_pr_change_has_no_fingerprint(path_cases_repo: tuple[Path, dict[str, str], str]) -> None:
    repo, shas, _ = path_cases_repo
    assert pr_change_fingerprint(str(repo), shas["empty_pr"], shas["base"]) is None


def test_unknown_refs_have_no_fingerprint(path_cases_repo: tuple[Path, dict[str, str], str], tmp_path: Path) -> None:
    repo, shas, _ = path_cases_repo
    assert pr_change_fingerprint(str(repo), shas["pr"], "refs/remotes/origin/missing") is None
    assert pr_change_fingerprint(str(repo), "0" * 40, shas["base"]) is None
    assert approval_fingerprint_of(str(repo), "HEAD", "main") is None
    assert approval_fingerprint_of(str(repo), None, "main") is None
    assert approval_fingerprint_of(str(repo), "abc", None) is None
    assert approval_fingerprint_of(str(tmp_path / "no-repo"), "abc", "main") is None


def test_clean_ledger_only_merge_preserves_fingerprint(repos_template: Path, tmp_path: Path) -> None:
    work = _repos(repos_template, tmp_path, {"src/pr_a.py": "x = 1\n"})
    before = _fingerprint(work)
    _advance_main(work, {"src/main_a.py": "y = 1\n"}, "0")
    _merge_main(work, "0")
    assert before is not None and _fingerprint(work) == before


def test_hand_resolved_code_conflict_changes_the_fingerprint(repos_template: Path, tmp_path: Path) -> None:
    work = _repos(repos_template, tmp_path, {"src/pr_a.py": "x = 1\n"})
    before = _fingerprint(work)
    _advance_main(work, {"src/pr_a.py": "x = 3\n"}, "clash")
    outcome = _merge_target_ledger_only(str(work), TARGET, "Merge main (clash)")
    assert not outcome.merged and "src/pr_a.py" in outcome.conflicting_paths
    _git(work, "merge", "--no-ff", "-X", "theirs", TARGET, "-m", "hand merge")
    _commit(work, {"src/pr_a.py": "x = 1\nx = 3\n"}, "hand resolution")
    assert _fingerprint(work) != before


def test_none_never_matches() -> None:
    assert not fingerprints_match(None, None)
    assert not fingerprints_match("", "")
    assert not fingerprints_match("pf1:a", None)
    assert not fingerprints_match("pf1:a", "pf1:b")
    assert fingerprints_match("pf1:a", "pf1:a")


PR_CHANGES = st.dictionaries(st.sampled_from(PR_PATHS), CONTENT, min_size=1)
MAIN_MOVES = st.lists(st.dictionaries(st.sampled_from(MAIN_PATHS), CONTENT), min_size=0, max_size=3)


def _property_dag(
    pr_files: dict[str, str], main_moves: list[dict[str, str]], edited_path: str, new_content: str
) -> tuple[list[GraphCommit], list[tuple[int, int]], int]:
    base_row, pr_row = _ledger_row("E-base", 1), _ledger_row("E-pr", 2)
    main_ledger, branch_ledger = [base_row], [base_row, pr_row]
    commits = [
        GraphCommit("main", {"README.md": "base\n", LEDGER: base_row}, "base"),
        GraphCommit(BRANCH, {**pr_files, LEDGER: "".join(branch_ledger)}, "pr change", (0,)),
    ]
    main_tip, branch_tip, next_day = 0, 1, 3
    main_tree: dict[str, str] = {}
    merge_pairs: list[tuple[int, int]] = []
    for index, files in enumerate(main_moves):
        main_tree.update(files)
        main_ledger.append(_ledger_row(f"E-main-{index}", next_day))
        commits.append(GraphCommit("main", {**files, LEDGER: "".join(main_ledger)}, f"main {index}", (main_tip,)))
        main_tip, next_day = len(commits) - 1, next_day + 1
        branch_ledger.append(_ledger_row(f"E-pr-{index}", next_day))
        commits.append(GraphCommit(BRANCH, {LEDGER: "".join(branch_ledger)}, f"pr row {index}", (branch_tip,)))
        branch_tip, next_day = len(commits) - 1, next_day + 1
        branch_ledger = sorted(set(branch_ledger) | set(main_ledger))
        # why: fast-import bases a merge commit on its first parent, so accumulated main files are included explicitly.
        merged_files = {**main_tree, LEDGER: "".join(branch_ledger)}
        commits.append(GraphCommit(BRANCH, merged_files, f"Merge main ({index})", (branch_tip, main_tip)))
        branch_tip = len(commits) - 1
        merge_pairs.append((branch_tip, main_tip))
    commits.append(GraphCommit(BRANCH, {edited_path: new_content}, "post-approval push", (branch_tip,)))
    return commits, merge_pairs, main_tip


class _ChosenDraws:
    """Stands in for ``st.data()`` in an explicit example: ``draw`` returns the value chosen for its label."""

    def __init__(self, path: str, content: str) -> None:
        self._chosen = {"edited PR path": path, "new content": content}

    def draw(self, _strategy: st.SearchStrategy[str], label: str) -> str:
        return self._chosen[label]

    def __repr__(self) -> str:
        return f"_ChosenDraws({self._chosen})"


# The boundaries of the property, as explicit examples: (PR change, main moves, edited PR path, new content).
ONE_PR_FILE = {"src/pr_a.py": "x = 1\n"}
NO_MAIN_MOVE = (ONE_PR_FILE, [], "src/pr_a.py", "x = 2\n")
THREE_MAIN_MOVES = (ONE_PR_FILE, [{path: "y = 1\n"} for path in MAIN_PATHS], "docs/pr_d.md", "new file\n")
ALL_PR_PATHS = ({path: f"{path}\n" for path in PR_PATHS}, [{"src/main_a.py": "y = 1\n"}], "src/pkg/pr_c.py", "c\n")
EMPTY_MAIN_MOVE = (ONE_PR_FILE, [{}], "src/pr_b.py", "z = 1\n")
SINGLE_NEWLINE = ({"src/pr_a.py": "\n"}, [{"src/main_a.py": "\n"}], "src/pr_a.py", "\n\n")


@PROPERTY_SETTINGS
@example(pr_files=NO_MAIN_MOVE[0], main_moves=NO_MAIN_MOVE[1], data=_ChosenDraws(*NO_MAIN_MOVE[2:]))
@example(pr_files=THREE_MAIN_MOVES[0], main_moves=THREE_MAIN_MOVES[1], data=_ChosenDraws(*THREE_MAIN_MOVES[2:]))
@example(pr_files=ALL_PR_PATHS[0], main_moves=ALL_PR_PATHS[1], data=_ChosenDraws(*ALL_PR_PATHS[2:]))
@example(pr_files=EMPTY_MAIN_MOVE[0], main_moves=EMPTY_MAIN_MOVE[1], data=_ChosenDraws(*EMPTY_MAIN_MOVE[2:]))
@example(pr_files=SINGLE_NEWLINE[0], main_moves=SINGLE_NEWLINE[1], data=_ChosenDraws(*SINGLE_NEWLINE[2:]))
@given(pr_files=PR_CHANGES, main_moves=MAIN_MOVES, data=st.data())
def test_property_fingerprint_survives_merges_of_a_disjoint_moving_main_and_changes_when_a_pr_file_changes(
    bare_template: Path, pr_files: dict[str, str], main_moves: list[dict[str, str]], data: st.DataObject
) -> None:
    path = data.draw(st.sampled_from(PR_PATHS), label="edited PR path")
    old = pr_files.get(path, "")
    new = data.draw(CONTENT.filter(lambda text: text != old), label="new content")
    commits, merge_pairs, last_main = _property_dag(pr_files, main_moves, path, new)
    with tempfile.TemporaryDirectory(prefix="holon-fingerprint-") as root:
        bare = copy_template(bare_template, Path(root)) / "repo.git"
        shas = fast_import(bare, commits)
        before = pr_change_fingerprint(str(bare), shas[1], shas[0])
        assert before is not None
        for step, (merge_idx, main_idx) in enumerate(merge_pairs, start=1):
            assert pr_change_fingerprint(str(bare), shas[merge_idx], shas[main_idx]) == before, (
                f"fingerprint changed after merge {step}"
            )
        assert pr_change_fingerprint(str(bare), shas[-1], shas[last_main]) != before
