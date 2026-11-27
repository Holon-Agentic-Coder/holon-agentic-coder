from __future__ import annotations

import subprocess
from types import SimpleNamespace
from typing import Any

import pytest
from sandbox_executor import agent_defaults, calibration_diff, local_llm
from sandbox_executor.calibration import actual_evidence
from sandbox_executor.flow.context import FlowStage, StageResult, StageStatus
from sandbox_executor.flow.review import delta_review_git
from sandbox_executor.flow.stages import review as review_stage
from sandbox_executor.git import parse_numstat


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("pi-agent", "pi"),
        ("agent-pi", "pi"),
        ("claude-agent", "claude"),
        ("agent-codex", "codex"),
        ("Pi-Agent", "pi"),
        ("AGENT-CODEX", "codex"),
        ("  pi-agent  ", "pi"),
        ("\tagent-claude\n", "claude"),
        ("pi", "pi"),
        ("antigravity", "antigravity"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_agent_id_normalization_parity(raw: str, expected: str) -> None:
    assert agent_defaults.normalise_agent_id(raw) == expected
    assert local_llm.normalize_agent_id(raw) == expected


def test_local_llm_normalize_agent_id_delegates_to_agent_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_normalise(name: str) -> str:
        seen.append(name)
        return "delegated"

    monkeypatch.setattr(agent_defaults, "normalise_agent_id", fake_normalise)
    assert local_llm.normalize_agent_id("  Claude-Agent  ") == "delegated"
    assert seen == ["  Claude-Agent  "]


def test_parse_numstat_empty_and_blank_lines() -> None:
    assert parse_numstat("") == []
    assert parse_numstat("\n   \n\t\n") == []


def test_parse_numstat_standard_zero_binary_and_malformed_lines() -> None:
    sample = "\n".join(
        [
            "12\t3\tsrc/app.py",
            "0\t0\tsrc/empty.py",
            "-\t-\tassets/logo.png",
            "bad\tval\tsrc/weird.bin",
            "malformed line without tabs",
            "10\tonly_two_columns",
        ]
    )
    assert parse_numstat(sample) == [
        (12, 3, "src/app.py"),
        (0, 0, "src/empty.py"),
        (-1, -1, "assets/logo.png"),
        (-1, -1, "src/weird.bin"),
    ]


def test_parse_numstat_renames_and_paths_with_spaces_or_tabs() -> None:
    sample = "\n".join(
        [
            "4\t1\told.py => new.py",
            "7\t2\tsrc/{old.py => new.py}",
            "5\t0\tdir with spaces/file\tname.py",
        ]
    )
    assert parse_numstat(sample) == [
        (4, 1, "old.py => new.py"),
        (7, 2, "src/{old.py => new.py}"),
        (5, 0, "dir with spaces/file\tname.py"),
    ]


def test_calibration_numstat_excludes_metadata_and_handles_binary() -> None:
    sample = "\n".join(
        [
            "10\t2\tsrc/module.py",
            "-\t-\tassets/icon.png",
            "50\t0\tholon-knowledge/ledger/executions.jsonl",
            "20\t5\tplans/P-1.md",
            "15\t1\texecutions/E-1.md",
            "3\t1\t.holon/state.json",
            "4\t4\tsrc/{old.py => new.py}",
        ]
    )
    expected = (["src/module.py", "assets/icon.png", "src/{old.py => new.py}"], 3, 14, 6)
    assert calibration_diff.parse_patch_numstat(sample) == expected
    assert actual_evidence.parse_numstat(sample) == expected


def test_delta_repo_changed_line_count_sums_text_and_rejects_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = delta_review_git.DeltaRepo(repo_dir="/tmp/fake-repo")

    def fake_text_diff(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["git"],
            returncode=0,
            stdout="10\t4\tsrc/a.py\n0\t3\tsrc/b.py\n",
            stderr="",
        )

    monkeypatch.setattr(delta_review_git, "run_git", fake_text_diff)
    assert repo.changed_line_count("base", "head", ["src/a.py", "src/b.py"]) == 17

    def fake_binary_diff(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["git"],
            returncode=0,
            stdout="10\t4\tsrc/a.py\n-\t-\tassets/pic.png\n",
            stderr="",
        )

    monkeypatch.setattr(delta_review_git, "run_git", fake_binary_diff)
    assert repo.changed_line_count("base", "head", ["src/a.py", "assets/pic.png"]) is None


def test_review_stage_diff_summary_uses_parse_numstat(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    numstat_output = "\n".join(
        [
            "8\t2\tsrc/feature.py",
            "-\t-\tassets/diagram.png",
            "40\t0\tplans/P-123.md",
            "5\t1\tsrc/{a.py => b.py}",
            "12\t0\t./holon-knowledge/ledger/executions.jsonl",
        ]
    )
    captured: dict[str, Any] = {}

    def fake_run_git(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["diff", "--numstat"]:
            return subprocess.CompletedProcess(args=["git", *args], returncode=0, stdout=numstat_output, stderr="")
        return subprocess.CompletedProcess(args=["git", *args], returncode=0, stdout="true\n", stderr="")

    def fake_build_outcome(_ctx: Any, outcome_ctx: Any) -> StageResult:
        captured["diff_summary"] = outcome_ctx.review_package["diff_summary"]
        return StageResult(
            stage=FlowStage.REVIEW,
            status=StageStatus.APPROVED,
            start_time=outcome_ctx.start_time,
            end_time=outcome_ctx.end_time,
        )

    monkeypatch.setattr(review_stage, "_checkout_execution_branch", lambda _ctx: None)
    monkeypatch.setattr(review_stage, "_close_review_stage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(review_stage, "at_stage_boundary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(review_stage, "run_git", fake_run_git)
    monkeypatch.setattr(review_stage, "qualify_branch_revision", lambda _repo, branch, **_kw: f"refs/heads/{branch}")
    monkeypatch.setattr(review_stage, "GitHubAdapter", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        review_stage,
        "_run_engine_review_loop",
        lambda *_args, **_kwargs: {"consensus": {"approved": True}, "reviewers": [], "mergeability": {}},
    )
    monkeypatch.setattr(review_stage, "build_review_stage_outcome", fake_build_outcome)

    ctx = SimpleNamespace(
        intent_branch="I-1",
        plan_branch="P-1",
        execution_branch="E-1",
        target_worktree=str(tmp_path),
        dry_run=False,
        stage_results={},
        max_review_iterations=1,
        token_reduce=False,
        proxy_attach=False,
        review_state={},
        intent_data={"target_branch": "main"},
        log=lambda _msg: None,
    )
    res = review_stage.run_review_stage(ctx)
    assert res.status == StageStatus.APPROVED
    assert captured["diff_summary"] == {"files_changed": 3, "insertions": 13, "deletions": 3}
