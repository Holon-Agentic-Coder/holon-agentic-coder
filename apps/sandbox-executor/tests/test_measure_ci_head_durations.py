from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from scripts.measure_ci_head_durations import (
    DurationStats,
    build_parser,
    classify_head_commit,
    collect_pr_head_timings,
    flatten_paged_items,
    load_reviewer_waits,
    main,
    parse_pr_spec,
    summarize_durations,
)

REPO = "acme/holon"
MUTATING_FLAGS = frozenset({"-X", "--method", "-f", "-F", "--field", "--raw-field", "--input"})


class FakeGhRunner:
    def __init__(self, responses: dict[str, Any], repo_name: str = REPO) -> None:
        self.responses = responses
        self.repo_name = repo_name
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, args: Sequence[str]) -> Any:
        cmd = tuple(args)
        assert cmd, "empty gh command"
        assert not (MUTATING_FLAGS & set(cmd)), f"non-read-only gh flags: {cmd}"
        self.calls.append(cmd)
        if cmd[:2] == ("repo", "view"):
            return self.repo_name
        assert cmd[0] == "api", f"unexpected gh subcommand: {cmd}"
        endpoint = cmd[-1]
        assert endpoint in self.responses, f"unrecorded gh api endpoint: {endpoint}"
        return self.responses[endpoint]


def commit_entry(sha: str, message: str, date: str = "2026-11-20T10:00:00Z") -> dict[str, Any]:
    return {"sha": sha, "commit": {"message": message, "committer": {"date": date}}}


def commit_detail(files: Sequence[dict[str, str]]) -> dict[str, Any]:
    return {"files": list(files)}


def run_entry(run_id: int, head_sha: str, name: str = "Test - Unit", status: str = "completed") -> dict[str, Any]:
    return {"id": run_id, "name": name, "status": status, "head_sha": head_sha, "run_attempt": 1}


def job_entry(
    name: str,
    started_at: str | None,
    completed_at: str | None,
    conclusion: str | None = "success",
    **overrides: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": name,
        "started_at": started_at,
        "completed_at": completed_at,
        "conclusion": conclusion,
        "run_attempt": 1,
        "labels": ["macos-latest"] if "macos" in name else ["ubuntu-latest"],
    }
    return {**base, **overrides}


def sample_pr_360_to_366_responses() -> dict[str, Any]:
    responses: dict[str, Any] = {}
    for pr in range(360, 367):
        code_sha = f"{pr:04x}" + "a" * 36
        book_sha = f"{pr:04x}" + "b" * 36
        responses[f"repos/{REPO}/pulls/{pr}/commits?per_page=100"] = [
            [commit_entry(code_sha, f"feat: update logic for PR {pr}")],
            [commit_entry(book_sha, f"chore(ledger): record execution for PR {pr}")],
        ]
        responses[f"repos/{REPO}/commits/{code_sha}"] = commit_detail(
            [{"filename": "scripts/measure_ci_head_durations.py"}]
        )
        responses[f"repos/{REPO}/commits/{book_sha}"] = commit_detail(
            [
                {"filename": f"plans/P-{pr}.md"},
                {"filename": f"executions/E-{pr}.json"},
                {"filename": "holon-knowledge/ledger/executions.jsonl"},
            ]
        )
        code_run_id = pr * 10 + 1
        book_run_id = pr * 10 + 2
        responses[f"repos/{REPO}/actions/runs?head_sha={code_sha}&per_page=100"] = [
            {
                "workflow_runs": [
                    run_entry(code_run_id, code_sha),
                    run_entry(pr * 10 + 9, code_sha, status="in_progress"),
                ]
            }
        ]
        responses[f"repos/{REPO}/actions/runs/{code_run_id}/jobs?filter=all&per_page=100"] = [
            {
                "jobs": [
                    job_entry("unit (ubuntu-latest)", "2026-11-20T10:00:00Z", "2026-11-20T10:02:00Z"),
                    job_entry(
                        "unit (macos-latest)",
                        "2026-11-20T10:00:00Z",
                        f"2026-11-20T10:{5 + (pr - 360):02d}:00Z",
                    ),
                    job_entry("lint", "2026-11-20T10:00:00Z", "2026-11-20T10:00:30Z", conclusion="skipped"),
                    job_entry("pending", "2026-11-20T10:00:00Z", None, conclusion=None),
                ]
            }
        ]
        responses[f"repos/{REPO}/actions/runs?head_sha={book_sha}&per_page=100"] = [
            {"workflow_runs": [run_entry(book_run_id, book_sha, name="Hygiene")]}
        ]
        responses[f"repos/{REPO}/actions/runs/{book_run_id}/jobs?filter=all&per_page=100"] = [
            {
                "jobs": [
                    job_entry("hygiene (ubuntu-latest)", "2026-11-20T10:10:00Z", "2026-11-20T10:10:20Z"),
                    job_entry(
                        "hygiene (ubuntu-latest)",
                        "2026-11-20T10:12:00Z",
                        "2026-11-20T10:12:40Z",
                        run_attempt=2,
                    ),
                ]
            }
        ]
    return responses


def test_parse_pr_spec_defaults_ranges_lists_and_deduplicates() -> None:
    assert parse_pr_spec(build_parser().parse_args([]).prs) == [360, 361, 362, 363, 364, 365, 366]
    assert parse_pr_spec("360") == [360]
    assert parse_pr_spec("362, 360-362, 365") == [360, 361, 362, 365]


@pytest.mark.parametrize("bad_spec", ["", "   ", "366-360", "0", "-5", "abc", "360,", "360-361-362", "0-3"])
def test_parse_pr_spec_rejects_invalid_specs(bad_spec: str) -> None:
    with pytest.raises(ValueError):
        parse_pr_spec(bad_spec)
    runner = FakeGhRunner({})
    assert main(["--repo", REPO, "--prs", bad_spec], runner=runner) == 2
    assert runner.calls == []


def test_flatten_paged_items_supports_slurped_lists_and_dicts() -> None:
    assert flatten_paged_items([[{"id": 1}], [{"id": 2}]]) == [{"id": 1}, {"id": 2}]
    assert flatten_paged_items([{"jobs": [{"id": 1}]}, {"jobs": [{"id": 2}]}], key="jobs") == [{"id": 1}, {"id": 2}]
    assert flatten_paged_items({"workflow_runs": [{"id": 3}]}, key="workflow_runs") == [{"id": 3}]
    assert flatten_paged_items({"jobs": None}, key="jobs") == []
    assert flatten_paged_items([{"jobs": [{"id": 1}]}, {"jobs": None}, {"other": 2}], key="jobs") == [{"id": 1}]
    assert flatten_paged_items("unexpected") == []


@pytest.mark.parametrize(
    ("files", "subject", "expected"),
    [
        (["plans/P-1.md", "executions/E-1.json", "holon-knowledge/ledger/executions.jsonl"], "feat: x", "bookkeeping"),
        (["plans/P-1.md", "scripts/foo.py"], "ledger: update", "code"),
        (["plans/helper.py"], "plan: helper", "code"),
        (["holon-knowledge/notes.md", "apps/sandbox-executor/src/a.py"], "rename", "code"),
        ([], "ledger: append execution", "bookkeeping"),
        ([], "calibration: record metrics", "bookkeeping"),
        ([], "review: address comments", "bookkeeping"),
        ([], "chore(bookkeeping): sync plan", "bookkeeping"),
        ([], "feat: implement parser", "code"),
    ],
)
def test_classify_head_commit_separates_code_and_bookkeeping(files: list[str], subject: str, expected: str) -> None:
    assert classify_head_commit(files, subject) == expected


def test_rename_from_non_bookkeeping_path_classifies_as_code() -> None:
    sha = "c" * 40
    runner = FakeGhRunner(
        {
            f"repos/{REPO}/pulls/360/commits?per_page=100": [commit_entry(sha, "bookkeeping: move doc")],
            f"repos/{REPO}/commits/{sha}": commit_detail(
                [{"filename": "plans/moved.md", "previous_filename": "docs/spec.md"}]
            ),
            f"repos/{REPO}/actions/runs?head_sha={sha}&per_page=100": {"workflow_runs": []},
        }
    )
    heads, timings = collect_pr_head_timings(REPO, [360], runner)
    assert len(heads) == 1
    assert heads[0].head_kind == "code"
    assert heads[0].changed_files == ("plans/moved.md", "docs/spec.md")
    assert timings == []


def test_collect_pr_head_timings_filters_skipped_and_incomplete_and_keeps_reruns() -> None:
    runner = FakeGhRunner(sample_pr_360_to_366_responses())
    heads, timings = collect_pr_head_timings(REPO, [360, 361], runner)
    assert len(heads) == 4
    assert [head.head_kind for head in heads] == ["code", "bookkeeping", "code", "bookkeeping"]
    assert len(timings) == 8
    assert all(t.conclusion != "skipped" for t in timings)
    assert any(t.run_attempt == 2 and t.duration_seconds == 40.0 for t in timings)
    macos_360 = next(t for t in timings if t.pr_number == 360 and t.runner_os == "macos-latest")
    macos_361 = next(t for t in timings if t.pr_number == 361 and t.runner_os == "macos-latest")
    assert macos_360.duration_seconds == 300.0
    assert macos_361.duration_seconds == 360.0


def test_load_reviewer_waits_parses_plain_text_and_jsonl_logs(tmp_path: Path) -> None:
    sha_code = "0168" + "a" * 36
    sha_book = "0168" + "b" * 36
    plain_log = tmp_path / "pr-360-flow.log"
    plain_log.write_text(
        "\n".join(
            [
                "[Review Loop] Starting review iteration 1/5 for target '360'.",
                "[Review Loop] CI wait settled after 125.5s: all checks passed.",
                f"[Review Loop] Resolver pushed new head '{sha_book[:12]}' at iteration 2; proceeding to re-review.",
                "[Review Loop] CI wait stopped on a failure after 45s: unit failed.",
                "[Review Loop] Starting review iteration 3/5 for target '360'.",
                "[Review Loop] CI wait budget of 1800s spent: pending macos.",
            ]
        ),
        encoding="utf-8",
    )
    jsonl_log = tmp_path / "executions.jsonl"
    jsonl_log.write_text(
        json.dumps(
            {
                "pr_number": 361,
                "review_iteration": 1,
                "head_sha": sha_code,
                "ci_wait": {"waited_seconds": 90.0, "state": "settled"},
            }
        )
        + "\nnot-json\n",
        encoding="utf-8",
    )
    waits = load_reviewer_waits([tmp_path])
    assert [(w.pr_number, w.iteration, w.waited_seconds, w.state) for w in waits] == [
        (361, 1, 90.0, "settled"),
        (360, 1, 125.5, "settled"),
        (360, 2, 45.0, "failure"),
        (360, 3, 1800.0, "budget_spent"),
    ]
    assert waits[2].head_sha == sha_book[:12]


def test_summarize_durations_handles_empty_and_populated_series() -> None:
    assert summarize_durations([]) == DurationStats(0, 0.0, 0.0)
    assert summarize_durations([10.0, 50.0, 20.0, 100.0]) == DurationStats(4, 35.0, 100.0)


def test_cli_renders_tables_for_prs_360_to_366_with_and_without_flow_logs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = FakeGhRunner(sample_pr_360_to_366_responses())
    log_file = tmp_path / "pr-360.log"
    log_file.write_text(
        "\n".join(
            [
                "[Review Loop] Starting review iteration 1/3 for target '360'.",
                "[Review Loop] CI wait settled after 210s: green.",
            ]
        ),
        encoding="utf-8",
    )
    rc = main(["--repo", REPO, "--prs", "360-366", "--flow-log", str(log_file)], runner=runner)
    assert rc == 0
    output = capsys.readouterr().out
    assert "## Overall Summary" in output
    assert "## Per-Job Durations" in output
    assert "## Per-Head Durations" in output
    assert "## Reviewer Iteration Waits" in output
    assert "macos-latest" in output
    assert "bookkeeping" in output
    assert "480.0s" in output
    assert "660.0s" in output
    assert "210.0s (iter 1, settled)" in output
    assert all(call[0] == "api" for call in runner.calls)

    missing_log = tmp_path / "missing.log"
    rc_no_log = main(["--prs", "360", "--flow-log", str(missing_log)], runner=runner)
    assert rc_no_log == 0
    out_no_log = capsys.readouterr().out
    assert "## Reviewer Iteration Waits" not in out_no_log
    assert "| #360" in out_no_log
    assert any(call[:2] == ("repo", "view") for call in runner.calls)


def test_direct_script_invocation_succeeds_without_pythonpath(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[3] / "scripts" / "measure_ci_head_durations.py"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--prs" in proc.stdout


def test_load_reviewer_waits_resets_head_and_iteration_across_prs_in_combined_log(tmp_path: Path) -> None:
    combined_log = tmp_path / "combined.log"
    combined_log.write_text(
        "\n".join(
            [
                "[Review Loop] Starting review iteration 1/5 for target '360'.",
                "[Review Loop] Resolver pushed new head '0168bbbbbbbb' at iteration 2; proceeding to re-review.",
                "[Review Loop] CI wait settled after 40s: green.",
                "Switching to PR #361",
                "[Review Loop] CI wait settled after 75s: green.",
            ]
        ),
        encoding="utf-8",
    )
    waits = load_reviewer_waits([combined_log])
    assert [(w.pr_number, w.iteration, w.head_sha, w.waited_seconds, w.state) for w in waits] == [
        (360, 2, "0168bbbbbbbb", 40.0, "settled"),
        (361, 1, "", 75.0, "settled"),
    ]


def test_cli_filters_unrelated_pr_waits_from_shared_flow_logs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = FakeGhRunner(sample_pr_360_to_366_responses())
    shared_jsonl = tmp_path / "executions.jsonl"
    shared_jsonl.write_text(
        "\n".join(
            [
                json.dumps({"pr_number": 360, "review_iteration": 1, "ci_wait": {"waited_seconds": 55.0}}),
                json.dumps({"pr_number": 361, "review_iteration": 1, "ci_wait": {"waited_seconds": 99.0}}),
                json.dumps({"review_iteration": 2, "ci_wait": {"waited_seconds": 33.0}}),
            ]
        ),
        encoding="utf-8",
    )
    rc = main(["--repo", REPO, "--prs", "360", "--flow-log", str(shared_jsonl)], runner=runner)
    assert rc == 0
    output = capsys.readouterr().out
    assert "## Reviewer Iteration Waits" in output
    assert "55.0s" in output
    assert "33.0s" in output
    assert "#361" not in output
    assert "99.0s" not in output
