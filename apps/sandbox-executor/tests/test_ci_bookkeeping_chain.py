"""Hermetic tests for the bookkeeping gate's API reads and its walk through earlier runs.

Every API answer is served by ``tests.ci_bookkeeping_fakes.FakeGitHub``; nothing here reaches the network.
"""

from __future__ import annotations

import pytest

from scripts.ci_bookkeeping_api import COMPARE_FILE_CAP, GateClient, RunRef, compare, list_earlier_runs, list_jobs
from scripts.ci_bookkeeping_chain import (
    CHAIN_RULES,
    FULL_RUN_STEP,
    GATE_SKIPPED,
    MAX_CHAIN_HOPS,
    NOT_SUCCESSFUL,
    REAL_SUCCESS,
    RULE_CHAIN_TOO_LONG,
    RULE_PREVIOUS_RUN_MISSING,
    RULE_PREVIOUS_RUN_NOT_FINISHED,
    RULE_PREVIOUS_RUN_NOT_SUCCESSFUL,
    RULE_PREVIOUS_RUN_SUCCEEDED,
    RULE_PREVIOUS_RUN_UNVERIFIED,
    RULE_SHARED_HEAD_BRANCH,
    UNVERIFIED,
    JobQuery,
    classify_job,
    find_real_success,
    match_job,
)
from tests.ci_bookkeeping_fakes import (
    BRANCH,
    MACOS_JOB,
    REPO,
    TOKEN,
    UNIT_JOB,
    WORKFLOW,
    FakeGitHub,
    comparison,
    gate_skipped_job,
    job,
    legacy_success_job,
    real_success_job,
)

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
# No workflow of this repository shows a job with two matrix values; ``match_job`` accepts the shape all the same.
TWO_VALUE_JOB = "unit (ubuntu-latest, 120)"
CURRENT_RUN = 50


def client_for(fake: FakeGitHub) -> GateClient:
    return GateClient(REPO, TOKEN, fake)


def query(target_sha: str = SHA_B, **overrides: object) -> JobQuery:
    values: dict[str, object] = {
        "workflow_file": WORKFLOW,
        "branch": BRANCH,
        "job_name": "unit",
        "os_name": "ubuntu-latest",
        "run_number": CURRENT_RUN,
        "target_sha": target_sha,
    }
    return JobQuery(**{**values, **overrides})


def names(*job_names: str) -> list[dict[str, object]]:
    return [job(name) for name in job_names]


# --- match_job -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("job_names", "job_name", "os_name", "expected"),
    [
        pytest.param(["build"], "build", "", "build", id="exact name without a label"),
        pytest.param(["build"], "build", "ubuntu-latest", "build", id="exact name with a label"),
        pytest.param([UNIT_JOB, MACOS_JOB], "unit", "ubuntu-latest", UNIT_JOB, id="matrix"),
        pytest.param([UNIT_JOB, MACOS_JOB], "unit", "macos-latest", MACOS_JOB, id="the other runner"),
        pytest.param([TWO_VALUE_JOB, MACOS_JOB], "unit", "ubuntu-latest", TWO_VALUE_JOB, id="two matrix values"),
        pytest.param(
            ["Workspace survival (ubuntu-latest)"],
            "Workspace survival",
            "ubuntu-latest",
            "Workspace survival (ubuntu-latest)",
            id="a job name with a space",
        ),
        pytest.param(["build (ubuntu-latest)"], "build", "", "build (ubuntu-latest)", id="matrix job, no label asked"),
        pytest.param([MACOS_JOB], "unit", "ubuntu-latest", None, id="only the macOS job"),
        pytest.param(["unit-extra (ubuntu-latest)"], "unit", "ubuntu-latest", None, id="a longer job name"),
        pytest.param(["unit (ubuntu-latest-xl)"], "unit", "ubuntu-latest", None, id="a longer runner label"),
        pytest.param(["unit (ubuntu-latest) extra"], "unit", "ubuntu-latest", None, id="text after the bracket"),
        pytest.param([TWO_VALUE_JOB, UNIT_JOB], "unit", "ubuntu-latest", None, id="two matching jobs"),
        pytest.param([UNIT_JOB, MACOS_JOB], "unit", "", None, id="two matrix jobs and no label"),
        pytest.param([], "unit", "ubuntu-latest", None, id="no jobs"),
    ],
)
def test_match_job_finds_exactly_one_job_by_name(job_names, job_name, os_name, expected) -> None:
    matched = match_job(names(*job_names), job_name, os_name)

    assert (matched["name"] if matched else None) == expected


# --- classify_job ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        pytest.param(real_success_job(), REAL_SUCCESS, id="marker step succeeded"),
        pytest.param(gate_skipped_job(), GATE_SKIPPED, id="marker step skipped"),
        pytest.param(legacy_success_job(), UNVERIFIED, id="no marker step"),
        pytest.param(job(steps=None), UNVERIFIED, id="no step list"),
        pytest.param(job(marker="success", conclusion="failure"), NOT_SUCCESSFUL, id="job failed"),
        pytest.param(job(marker="success", conclusion="cancelled"), NOT_SUCCESSFUL, id="job cancelled"),
        pytest.param(job(marker="success", status="in_progress", conclusion=None), NOT_SUCCESSFUL, id="in progress"),
        pytest.param(job(marker="failure"), NOT_SUCCESSFUL, id="marker step failed"),
        pytest.param(job(marker=None, steps=[{"name": FULL_RUN_STEP}]), NOT_SUCCESSFUL, id="no conclusion"),
    ],
)
def test_classify_job_tells_a_real_success_from_a_skipped_one(entry, expected) -> None:
    assert classify_job(entry) == expected


def test_classify_job_does_not_trust_two_marker_steps_that_disagree() -> None:
    steps = [{"name": FULL_RUN_STEP, "conclusion": "success"}, {"name": FULL_RUN_STEP, "conclusion": "skipped"}]

    assert classify_job(job(steps=steps)) == NOT_SUCCESSFUL


# --- find_real_success -----------------------------------------------------------------------------------------------


def test_a_real_success_on_the_target_commit_is_found_with_two_requests() -> None:
    fake = FakeGitHub().add_run(49, SHA_B, real_success_job(), real_success_job(MACOS_JOB))

    result = find_real_success(client_for(fake), query())

    assert (result.real_sha, result.rule) == (SHA_B, RULE_PREVIOUS_RUN_SUCCEEDED)
    assert len(fake.urls) == 2


def test_no_earlier_run_is_previous_run_missing() -> None:
    fake = FakeGitHub()

    result = find_real_success(client_for(fake), query())

    assert (result.real_sha, result.rule) == (None, RULE_PREVIOUS_RUN_MISSING)


def test_the_newest_earlier_run_on_another_commit_is_previous_run_missing() -> None:
    fake = FakeGitHub().add_run(48, SHA_B, real_success_job()).add_run(49, SHA_C, real_success_job())

    result = find_real_success(client_for(fake), query(SHA_B))

    assert (result.real_sha, result.rule) == (None, RULE_PREVIOUS_RUN_MISSING)
    assert fake.calls("/jobs") == []


def test_a_run_that_is_not_finished_is_not_trusted() -> None:
    fake = FakeGitHub().add_run(49, SHA_B, real_success_job(), status="in_progress")

    result = find_real_success(client_for(fake), query())

    assert (result.real_sha, result.rule) == (None, RULE_PREVIOUS_RUN_NOT_FINISHED)
    assert fake.calls("/jobs") == []


@pytest.mark.parametrize("status", ["completed", "in_progress"])
def test_a_run_that_names_several_pull_requests_is_not_trusted(status) -> None:
    both = [{"number": 7}, {"number": 8}]
    fake = FakeGitHub().add_run(49, SHA_B, real_success_job(), status=status, pull_requests=both)

    result = find_real_success(client_for(fake), query())

    assert (result.real_sha, result.rule) == (None, RULE_SHARED_HEAD_BRANCH)
    assert result.detail == "run 49 names 2 pull requests"
    assert fake.calls("/jobs") == []


def test_an_older_run_that_names_several_pull_requests_ends_the_walk() -> None:
    fake = FakeGitHub().add_run(48, SHA_A, real_success_job(), pull_requests=[{"number": 7}, {"number": 8}])
    fake.add_run(49, SHA_B, gate_skipped_job())

    result = find_real_success(client_for(fake), query(SHA_B))

    assert (result.real_sha, result.rule) == (None, RULE_SHARED_HEAD_BRANCH)
    assert fake.calls("/jobs") == [f"https://api.github.com/repos/{REPO}/actions/runs/1049/jobs?per_page=100"]


@pytest.mark.parametrize("pull_requests", [[], None], ids=["an empty list", "null"])
def test_a_run_that_names_no_pull_request_is_still_walked(pull_requests) -> None:
    fake = FakeGitHub().add_run(49, SHA_B, real_success_job(), pull_requests=pull_requests)

    result = find_real_success(client_for(fake), query())

    assert (result.real_sha, result.rule) == (SHA_B, RULE_PREVIOUS_RUN_SUCCEEDED)


def test_a_run_without_the_job_is_previous_run_missing() -> None:
    fake = FakeGitHub().add_run(49, SHA_B, real_success_job(MACOS_JOB))

    result = find_real_success(client_for(fake), query())

    assert (result.real_sha, result.rule) == (None, RULE_PREVIOUS_RUN_MISSING)


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "skipped"])
def test_a_job_that_did_not_succeed_ends_the_walk_with_its_conclusion(conclusion) -> None:
    fake = FakeGitHub().add_run(49, SHA_B, job(marker="skipped", conclusion=conclusion))

    result = find_real_success(client_for(fake), query())

    assert (result.real_sha, result.rule) == (None, RULE_PREVIOUS_RUN_NOT_SUCCESSFUL)
    assert conclusion in result.detail


def test_a_success_without_the_marker_step_is_unverified() -> None:
    fake = FakeGitHub().add_run(49, SHA_B, legacy_success_job())

    result = find_real_success(client_for(fake), query())

    assert (result.real_sha, result.rule) == (None, RULE_PREVIOUS_RUN_UNVERIFIED)


def test_two_skipped_runs_lead_to_the_commit_of_the_real_success() -> None:
    fake = FakeGitHub()
    fake.add_run(47, SHA_A, real_success_job()).add_run(48, SHA_C, gate_skipped_job()).add_run(
        49, SHA_B, gate_skipped_job()
    )

    result = find_real_success(client_for(fake), query(SHA_B))

    assert (result.real_sha, result.rule) == (SHA_A, RULE_PREVIOUS_RUN_SUCCEEDED)
    assert [url.split("/runs/")[1].split("/")[0] for url in fake.calls("/jobs")] == ["1049", "1048", "1047"]


def test_two_skipped_runs_that_end_in_a_failure_find_no_success() -> None:
    fake = FakeGitHub()
    fake.add_run(47, SHA_A, job(conclusion="failure"))
    fake.add_run(48, SHA_C, gate_skipped_job()).add_run(49, SHA_B, gate_skipped_job())

    result = find_real_success(client_for(fake), query(SHA_B))

    assert (result.real_sha, result.rule) == (None, RULE_PREVIOUS_RUN_NOT_SUCCESSFUL)
    assert "failure" in result.detail


def test_a_chain_of_skipped_runs_that_is_too_long_stops_the_walk() -> None:
    fake = FakeGitHub().add_run(1, SHA_A, real_success_job())
    for number in range(2, 2 + MAX_CHAIN_HOPS + 1):
        fake.add_run(number, SHA_B, gate_skipped_job())

    result = find_real_success(client_for(fake), query(SHA_B))

    assert (result.real_sha, result.rule) == (None, RULE_CHAIN_TOO_LONG)
    assert len(fake.calls("/jobs")) == MAX_CHAIN_HOPS


def test_a_real_success_at_the_last_allowed_hop_is_still_found() -> None:
    fake = FakeGitHub().add_run(1, SHA_A, real_success_job())
    for number in range(2, 2 + MAX_CHAIN_HOPS - 1):
        fake.add_run(number, SHA_B, gate_skipped_job())

    result = find_real_success(client_for(fake), query(SHA_B))

    assert (result.real_sha, result.rule) == (SHA_A, RULE_PREVIOUS_RUN_SUCCEEDED)
    assert len(fake.calls("/jobs")) == MAX_CHAIN_HOPS


def test_a_list_that_ends_after_skipped_runs_is_previous_run_missing() -> None:
    fake = FakeGitHub().add_run(48, SHA_A, gate_skipped_job()).add_run(49, SHA_B, gate_skipped_job())

    result = find_real_success(client_for(fake), query(SHA_B))

    assert (result.real_sha, result.rule) == (None, RULE_PREVIOUS_RUN_MISSING)


def test_runs_at_or_above_the_current_run_number_are_ignored() -> None:
    fake = FakeGitHub().add_run(49, SHA_B, real_success_job())
    fake.add_run(CURRENT_RUN, SHA_C, job(conclusion="failure")).add_run(51, SHA_C, job(conclusion="failure"))

    result = find_real_success(client_for(fake), query(SHA_B))

    assert (result.real_sha, result.rule) == (SHA_B, RULE_PREVIOUS_RUN_SUCCEEDED)


def test_runs_of_another_branch_or_workflow_are_not_asked_for() -> None:
    fake = FakeGitHub().add_run(48, SHA_B, real_success_job(), head_branch="another/branch")
    fake.add_run(49, SHA_B, real_success_job(), path=".github/workflows/make.yml")

    result = find_real_success(client_for(fake), query(SHA_B))

    assert (result.real_sha, result.rule) == (None, RULE_PREVIOUS_RUN_MISSING)


def test_a_branch_with_slashes_is_quoted_in_the_requested_url() -> None:
    fake = FakeGitHub().add_run(49, SHA_B, real_success_job())

    list_earlier_runs(client_for(fake), WORKFLOW, BRANCH, CURRENT_RUN)

    assert "/" in BRANCH
    expected = f"actions/workflows/{WORKFLOW}/runs?event=pull_request&branch={BRANCH.replace('/', '%2F')}&per_page=100"
    assert fake.urls == [f"https://api.github.com/repos/{REPO}/{expected}"]


def test_earlier_runs_are_sorted_by_run_number_highest_first() -> None:
    fake = FakeGitHub().add_run(7, SHA_A).add_run(49, SHA_B).add_run(12, SHA_C)

    runs = list_earlier_runs(client_for(fake), WORKFLOW, BRANCH, CURRENT_RUN)

    assert runs == [
        RunRef(1049, 49, SHA_B, "completed", 1, ((7, ""),)),
        RunRef(1012, 12, SHA_C, "completed", 1, ((7, ""),)),
        RunRef(1007, 7, SHA_A, "completed", 1, ((7, ""),)),
    ]


def test_a_run_entry_counts_the_pull_requests_it_names() -> None:
    fake = FakeGitHub().add_run(47, SHA_A).add_run(48, SHA_B, pull_requests=[])
    fake.add_run(49, SHA_C, pull_requests=[{"number": 7}, {"number": 8}])
    del fake.runs[0]["pull_requests"]

    runs = list_earlier_runs(client_for(fake), WORKFLOW, BRANCH, CURRENT_RUN)

    assert [(ref.run_number, ref.pull_request_count) for ref in runs] == [(49, 2), (48, 0), (47, 0)]


def test_the_chain_rules_are_listed_once_each() -> None:
    assert len(set(CHAIN_RULES)) == len(CHAIN_RULES) == 7


# --- API reads -------------------------------------------------------------------------------------------------------


def test_compare_counts_both_names_of_a_rename() -> None:
    fake = FakeGitHub({(SHA_A, SHA_B): comparison("plans/x.md", ("scripts/a.py", "plans/a.py"))})

    result = compare(client_for(fake), SHA_A, SHA_B)

    assert result.status == "ahead"
    assert result.paths == ("plans/x.md", "plans/a.py", "scripts/a.py")
    assert not result.truncated
    assert fake.urls == [f"https://api.github.com/repos/{REPO}/compare/{SHA_A}...{SHA_B}"]


@pytest.mark.parametrize(("count", "truncated"), [(COMPARE_FILE_CAP - 1, False), (COMPARE_FILE_CAP, True)])
def test_compare_marks_a_full_file_list_as_truncated(count, truncated) -> None:
    fake = FakeGitHub({(SHA_A, SHA_B): comparison(*[f"plans/{index}.md" for index in range(count)])})

    assert compare(client_for(fake), SHA_A, SHA_B).truncated is truncated


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param({"status": "ahead"}, id="no file list"),
        pytest.param({"status": "ahead", "files": {"filename": "plans/x.md"}}, id="files is not a list"),
        pytest.param({"status": "ahead", "files": [{"status": "modified"}]}, id="an entry without a filename"),
        pytest.param({"status": "ahead", "files": [{"filename": 7}]}, id="a filename that is not text"),
        pytest.param({"status": "ahead", "files": ["plans/x.md"]}, id="an entry that is not an object"),
        pytest.param(
            {"status": "ahead", "files": [{"filename": "plans/x.md", "previous_filename": 7}]},
            id="a previous filename that is not text",
        ),
        pytest.param([{"status": "ahead", "files": []}], id="the answer is not an object"),
    ],
)
def test_compare_raises_on_an_answer_of_the_wrong_shape(answer) -> None:
    fake = FakeGitHub({(SHA_A, SHA_B): answer})

    with pytest.raises(ValueError):
        compare(client_for(fake), SHA_A, SHA_B)


def test_runs_and_jobs_raise_on_an_answer_of_the_wrong_shape() -> None:
    fake = FakeGitHub().add_run(49, SHA_B)
    fake.bodies = {"/runs?": b'{"workflow_runs": null}', "/jobs?": b'{"jobs": ["unit"]}'}

    with pytest.raises(ValueError):
        list_earlier_runs(client_for(fake), WORKFLOW, BRANCH, CURRENT_RUN)
    with pytest.raises(ValueError):
        list_jobs(client_for(fake), 1049)


def test_a_run_entry_whose_pull_requests_are_not_a_list_raises() -> None:
    fake = FakeGitHub().add_run(49, SHA_B, real_success_job(), pull_requests={"number": 7})

    with pytest.raises(ValueError, match="pull request list"):
        list_earlier_runs(client_for(fake), WORKFLOW, BRANCH, CURRENT_RUN)


def test_a_run_entry_without_its_head_commit_raises() -> None:
    fake = FakeGitHub()
    fake.bodies = {"/runs?": b'{"workflow_runs": [{"id": 1, "run_number": 2, "status": "completed"}]}'}

    with pytest.raises(KeyError):
        list_earlier_runs(client_for(fake), WORKFLOW, BRANCH, CURRENT_RUN)


def test_the_token_is_sent_as_a_header_and_never_in_a_url_or_the_repr() -> None:
    fake = FakeGitHub({(SHA_A, SHA_B): comparison()}).add_run(49, SHA_B, real_success_job())
    client = client_for(fake)

    compare(client, SHA_A, SHA_B)
    find_real_success(client, query())

    assert len(fake.urls) == 3
    assert all(TOKEN not in url for url in fake.urls)
    assert fake.auth == [f"Bearer {TOKEN}"] * 3
    assert TOKEN not in repr(client)
