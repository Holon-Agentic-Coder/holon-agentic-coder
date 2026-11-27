import argparse
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox_executor.entrypoint.review_loop import _run_resolver_mode

from tests.review_fakes import install_fake_reviewers
from tests.test_review_loop import (
    _DESTRUCTIVE_GIT_VERBS,
    ENTRYPOINT,
    PR_HEAD_SHA,
    FakeGitCommands,
    _real_git,
    _run_entrypoint,
)


# why: mirrors the sandbox setup in tests/test_review_loop.py so both suites exercise the same entry conditions
@pytest.fixture(autouse=True)
def _sandboxed_clean_reviewers(monkeypatch):
    install_fake_reviewers(monkeypatch)


def test_mounted_head_detached_away_from_the_branch_tip_is_refused_for_a_branch_target(tmp_path, capsys):
    head_detached_away_from_branch_tip = "e" * 40
    fake_git = FakeGitCommands(heads=[head_detached_away_from_branch_tip])

    code, mock_loop, _post, _gh = _run_entrypoint(tmp_path, "feature/y", fake_git)

    assert code == 1
    mock_loop.assert_not_called()
    err = " ".join(capsys.readouterr().err.split())
    assert "mounted workspace is on a detached HEAD" in err
    assert (
        f"detached at {head_detached_away_from_branch_tip[:12]}, not at the tip {PR_HEAD_SHA[:12]} of 'feature/y'"
        in err
    )
    assert "Check out 'feature/y' by hand" in err
    assert not any(c[1] == "symbolic-ref" and c[-1].startswith("refs/heads/") for c in fake_git.calls)


def test_mounted_head_detached_at_the_branch_tip_is_reattached_and_reviewed(tmp_path, capsys):
    head_detached_at_branch_tip = PR_HEAD_SHA
    fake_git = FakeGitCommands(heads=[head_detached_at_branch_tip])

    code, mock_loop, _post, _gh = _run_entrypoint(tmp_path, "feature/y", fake_git)

    assert code == 0
    mock_loop.assert_called_once()
    assert mock_loop.call_args.kwargs["context"].execution_branch == "feature/y"
    assert ["git", "symbolic-ref", "-m", "holon: re-attach feature/y at its tip", "HEAD", "refs/heads/feature/y"] in (
        fake_git.calls
    )
    assert "re-attached 'feature/y'" in capsys.readouterr().err
    assert not any(c[1] in _DESTRUCTIVE_GIT_VERBS for c in fake_git.calls), fake_git.calls


def _two_commit_feature_repo_with_ledger(tmp_path: Path) -> Path:
    repo = tmp_path / "flow"
    subprocess.run(["git", "init", "-q", "-b", "feature/r", str(repo)], capture_output=True, check=True)
    ledger = repo / "holon-knowledge" / "ledger"
    ledger.mkdir(parents=True)
    (ledger / "executions.jsonl").write_text('{"execution_id": "E-1"}\n', encoding="utf-8")
    (repo / "app.py").write_text("print('bug')\n", encoding="utf-8")
    _real_git(repo, "add", "-A")
    _real_git(repo, "commit", "-qm", "first")
    (repo / "app.py").write_text("print('still a bug')\n", encoding="utf-8")
    _real_git(repo, "commit", "-qam", "second")
    return repo


def _run_detaching_resolver(tmp_path: Path, detach_at: str) -> tuple[Path, dict, str]:
    repo = _two_commit_feature_repo_with_ledger(tmp_path)
    tip = _real_git(repo, "rev-parse", "HEAD")
    result_file = tmp_path / "result.json"
    findings = [{"id": "R1-F1", "severity": "nit", "path": "app.py", "line": 1, "summary": "prints bug"}]
    result_file.write_text(json.dumps({"findings": findings}), encoding="utf-8")
    args = argparse.Namespace(
        findings_file=str(result_file),
        result_file=str(result_file),
        iteration=2,
        dry_run=False,
        target="feature/r",
        json=False,
    )

    def detaching_agent(repo_dir, *_args):
        _real_git(repo_dir, "checkout", "-q", "--detach", detach_at)
        Path(repo_dir, "app.py").write_text("print('fixed')\n", encoding="utf-8")
        return [{"id": "R1-F1", "disposition": "applied", "rejection_reason": None}]

    with (
        patch(f"{ENTRYPOINT}._run_resolver_agent", side_effect=detaching_agent),
        patch(f"{ENTRYPOINT}._verify_before_push", return_value=(True, True, None)),
        pytest.raises(SystemExit),
    ):
        _run_resolver_mode(args, str(repo), "feature/r", "fake-agent", "fake-model")
    return repo, json.loads(result_file.read_text(encoding="utf-8")), tip


def test_resolver_reattaches_a_head_the_turn_detached_at_the_tip_and_commits_on_the_branch(tmp_path, capsys):
    repo, outcome, tip = _run_detaching_resolver(tmp_path, "HEAD")

    assert outcome["error"] is None
    assert _real_git(repo, "symbolic-ref", "--short", "HEAD") == "feature/r"
    head = _real_git(repo, "rev-parse", "refs/heads/feature/r")
    assert head == outcome["head_sha"] != tip
    assert _real_git(repo, "rev-parse", "HEAD~1") == tip
    assert "resolver turn (iteration 2) left the worktree HEAD detached at" in capsys.readouterr().err


def test_resolver_refuses_to_commit_when_the_turn_detached_head_elsewhere(tmp_path, capsys):
    repo, outcome, tip = _run_detaching_resolver(tmp_path, "HEAD~1")

    assert outcome["exit_code"] == 1
    assert "Resolver turn moved HEAD off 'feature/r'" in outcome["error"]
    assert "refusing to re-attach" in outcome["error"]
    assert _real_git(repo, "rev-parse", "refs/heads/feature/r") == tip
    assert outcome["head_sha"] == tip
