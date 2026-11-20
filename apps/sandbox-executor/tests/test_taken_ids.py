import json
import subprocess
from pathlib import Path

from sandbox_executor.entrypoint.taken_ids import taken_child_ids, taken_plan_ids
from sandbox_executor.git import GitRepository


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def _repo(tmp_path: Path, *branches: str) -> GitRepository:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
    for branch in branches:
        _git(tmp_path, "branch", branch)
    return GitRepository(str(tmp_path))


def test_taken_child_ids_sees_execution_branches_ending_in_the_underscore_leaf(tmp_path):
    repo = _repo(tmp_path, "I-1-a/P-1-X/_", "I-1-a/P-1-X/E-2-CLA-CO5.5/_", "I-1-a/P-2-X/E-9-CLA-CO5.5/_")
    # An origin-tracking ref of a sibling execution counts as taken too.
    _git(tmp_path, "update-ref", "refs/remotes/origin/I-1-a/P-1-X/E-3-CLA-CO5.5/_", "HEAD")

    assert taken_child_ids(repo, "I-1-a/P-1-X", "E") == {"E-2-CLA-CO5.5", "E-3-CLA-CO5.5"}


def test_taken_child_ids_sees_sibling_plan_branches_of_the_intent_only(tmp_path):
    repo = _repo(tmp_path, "I-1-a/_", "I-1-a/P-5-CLA-CS5.5/_", "I-1-a/P-5-CLA-CS5.5/E-6-X/_", "I-1-b/P-7-CLA-CS5.5/_")
    _git(tmp_path, "update-ref", "refs/remotes/origin/I-1-a/P-6-CLA-CS5.5/_", "HEAD")

    assert taken_child_ids(repo, "I-1-a/_", "P") == {"P-5-CLA-CS5.5", "P-6-CLA-CS5.5"}


def test_taken_plan_ids_include_sibling_branches_intent_rows_and_plan_files_on_disk(tmp_path):
    """A same-second twin never reuses a plan name: sibling branches, this intent's rows and its files are taken."""
    repo = _repo(tmp_path, "I-1-a/P-3-CLA-CS5.5/_")
    ledger = tmp_path / "holon-knowledge" / "ledger" / "plans.jsonl"
    ledger.parent.mkdir(parents=True)
    rows = [
        {"plan_id": "P-1-CLA-CS5.5", "intent_branch": "I-1-a/_"},
        {"plan_id": "P-9-CLA-CS5.5", "intent_branch": "I-1-b/_"},
    ]
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    (tmp_path / "plans" / "I-1-a").mkdir(parents=True)
    (tmp_path / "plans" / "I-1-a" / "P-2-CLA-CS5.5.md").write_text("# Plan\n", encoding="utf-8")

    taken = taken_plan_ids(repo, str(tmp_path), "I-1-a/_")

    assert taken >= {"P-1-CLA-CS5.5", "P-2-CLA-CS5.5", "P-3-CLA-CS5.5"}
    assert "P-9-CLA-CS5.5" not in taken
