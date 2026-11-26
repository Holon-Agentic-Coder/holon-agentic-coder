from collections.abc import Mapping

import pytest
from sandbox_executor.entrypoint.test_failure_classify import (
    NOT_RUN,
    FailureClass,
    RerunResult,
    classify,
    diagnose,
    identity_suspects,
    scrubbed_environment,
)

IDS = ("tests/test_a.py::test_alpha",)
PASS = RerunResult(passed=True)
FAIL = RerunResult(passed=False, failing_ids=IDS, exit_code=1)


class ScriptedRerun:
    def __init__(self, *results: RerunResult) -> None:
        self.results = list(results)
        self.envs: list[Mapping[str, str]] = []

    def __call__(self, ids: tuple[str, ...], env: Mapping[str, str]) -> RerunResult:
        self.envs.append(dict(env))
        return self.results.pop(0)


class ScriptedBase:
    def __init__(self, result: RerunResult) -> None:
        self.result = result
        self.calls = 0

    def __call__(self, ids: tuple[str, ...]) -> RerunResult:
        self.calls += 1
        return self.result


@pytest.mark.parametrize(
    ("same_env", "scrubbed", "base", "expected"),
    [
        (PASS, NOT_RUN, NOT_RUN, FailureClass.FLAKY),
        (FAIL, PASS, NOT_RUN, FailureClass.ENVIRONMENT),
        (FAIL, FAIL, FAIL, FailureClass.PRE_EXISTING),
        (FAIL, FAIL, PASS, FailureClass.INTRODUCED),
        (FAIL, FAIL, RerunResult(passed=False, exit_code=4, output="ERROR: not found"), FailureClass.INTRODUCED),
        (FAIL, FAIL, NOT_RUN, FailureClass.INTRODUCED),
    ],
)
def test_decision_table(same_env, scrubbed, base, expected) -> None:
    assert classify(same_env, scrubbed, base, IDS) == expected


def test_base_failing_on_other_ids_is_not_pre_existing() -> None:
    base = RerunResult(passed=False, failing_ids=("tests/test_b.py::test_other",), exit_code=1)

    assert classify(FAIL, FAIL, base, IDS) == FailureClass.INTRODUCED


def test_leaked_git_identity_is_environment_in_one_diagnosis() -> None:
    def identity_sensitive(ids: tuple[str, ...], env: Mapping[str, str]) -> RerunResult:
        return FAIL if "GIT_AUTHOR_NAME" in env else PASS

    base = ScriptedBase(PASS)
    env = {"GIT_AUTHOR_NAME": "Reviewer", "GIT_COMMITTER_EMAIL": "r@example.com", "PATH": "/usr/bin"}

    diagnosis = diagnose(IDS, identity_sensitive, base, env)

    assert diagnosis.classification == FailureClass.ENVIRONMENT
    assert diagnosis.environment_suspects == ("GIT_AUTHOR_NAME", "GIT_COMMITTER_EMAIL")
    assert base.calls == 0


def test_flaky_stops_before_the_scrubbed_and_base_runs() -> None:
    rerun, base = ScriptedRerun(PASS), ScriptedBase(PASS)

    assert diagnose(IDS, rerun, base, {}).classification == FailureClass.FLAKY
    assert len(rerun.envs) == 1
    assert base.calls == 0


def test_scrubbed_run_gets_fresh_home_and_tmpdir() -> None:
    rerun = ScriptedRerun(FAIL, FAIL)

    diagnose(IDS, rerun, ScriptedBase(PASS), {"HOME": "/home/u", "TMPDIR": "/tmp/u", "GIT_AUTHOR_NAME": "x"})

    scrubbed = rerun.envs[1]
    assert scrubbed["HOME"] != "/home/u"
    assert scrubbed["TMPDIR"] != "/tmp/u"
    assert "GIT_AUTHOR_NAME" not in scrubbed


def test_base_ids_absent_is_introduced_with_absent_base_check() -> None:
    absent = RerunResult(passed=False, exit_code=4, output="ERROR: file or directory not found")

    diagnosis = diagnose(IDS, ScriptedRerun(FAIL, FAIL), ScriptedBase(absent), {})

    assert diagnosis.classification == FailureClass.INTRODUCED
    assert diagnosis.base_check == "absent"


def test_base_unavailable_is_introduced_with_unavailable_base_check() -> None:
    diagnosis = diagnose(IDS, ScriptedRerun(FAIL, FAIL), ScriptedBase(NOT_RUN), {})

    assert diagnosis.classification == FailureClass.INTRODUCED
    assert diagnosis.base_check == "unavailable"


def test_pre_existing_runs_all_three_reruns() -> None:
    rerun, base = ScriptedRerun(FAIL, FAIL), ScriptedBase(FAIL)

    diagnosis = diagnose(IDS, rerun, base, {})

    assert diagnosis.classification == FailureClass.PRE_EXISTING
    assert diagnosis.base_check == "failed"
    assert (len(rerun.envs), base.calls) == (2, 1)


def test_no_failing_ids_skips_every_rerun() -> None:
    rerun, base = ScriptedRerun(), ScriptedBase(PASS)

    diagnosis = diagnose((), rerun, base, {})

    assert diagnosis.classification == FailureClass.INTRODUCED
    assert diagnosis.base_check == "not_run"
    assert (len(rerun.envs), base.calls) == (0, 0)


def test_scrubbed_environment_keeps_everything_else_and_does_not_mutate_its_input() -> None:
    env = {
        "PATH": "/usr/bin",
        "HOME": "/home/u",
        "UV_PROJECT_ENVIRONMENT": "/w/.venv",
        "GIT_COMMITTER_NAME": "x",
        "GIT_DIR": "/w/.git",
    }
    original = dict(env)

    scrubbed = scrubbed_environment(env, "/fresh/home", "/fresh/tmp")

    assert env == original
    assert scrubbed["UV_CACHE_DIR"] == "/home/u/.cache/uv"
    assert scrubbed["UV_PROJECT_ENVIRONMENT"] == "/w/.venv"
    assert scrubbed["PATH"] == "/usr/bin"
    assert scrubbed["GIT_DIR"] == "/w/.git"
    assert "GIT_COMMITTER_NAME" not in scrubbed
    assert identity_suspects(env) == ("GIT_COMMITTER_NAME",)


def test_scrubbed_environment_keeps_an_explicit_uv_cache() -> None:
    assert scrubbed_environment({"UV_CACHE_DIR": "/cache"}, "/h", "/t")["UV_CACHE_DIR"] == "/cache"
