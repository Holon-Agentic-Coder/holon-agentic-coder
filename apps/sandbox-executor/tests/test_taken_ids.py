import subprocess
from pathlib import Path

from sandbox_executor.entrypoint.taken_ids import taken_child_ids
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
    _git(tmp_path, "update-ref", "refs/remotes/origin/I-1-a/P-1-X/E-3-CLA-CO5.5/_", "HEAD")

    assert taken_child_ids(repo, "I-1-a/P-1-X", "E") == {"E-2-CLA-CO5.5", "E-3-CLA-CO5.5"}


def test_taken_child_ids_sees_sibling_plan_branches_of_the_intent_only(tmp_path):
    repo = _repo(tmp_path, "I-1-a/_", "I-1-a/P-5-CLA-CS5.5/_", "I-1-a/P-5-CLA-CS5.5/E-6-X/_", "I-1-b/P-7-CLA-CS5.5/_")
    _git(tmp_path, "update-ref", "refs/remotes/origin/I-1-a/P-6-CLA-CS5.5/_", "HEAD")

    assert taken_child_ids(repo, "I-1-a/_", "P") == {"P-5-CLA-CS5.5", "P-6-CLA-CS5.5"}
