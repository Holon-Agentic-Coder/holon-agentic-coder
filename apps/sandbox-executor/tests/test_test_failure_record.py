import dataclasses
import json
import string

from sandbox_executor.entrypoint.test_failure_record import (
    EXCERPT_CHARS_PER_TEST,
    MAX_RECORDED_IDS,
    STAGE_3,
    STAGE_4,
    TestFailureEvidence,
    environment_summary,
    excerpts_by_test,
    failing_ids_from_output,
    failure_signature,
    record_test_failure,
)

from tests import seeded_secrets

BRANCH = "I-17-record-demo/P-1-CLA-CO5.5/E-2-CLA-CO5.5/_"


def _fake_token() -> str:
    rng = seeded_secrets.generator("test-failure-record:token")
    return "ghp_" + "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(36))


def _output(message: str = "assert 1 == 2", line: int = 12, tmp: str = "/tmp/pytest-of-u/pytest-3/x") -> str:
    return "\n".join(
        [
            "============================= FAILURES =============================",
            "___________________________ test_alpha ___________________________",
            "",
            "    def test_alpha():",
            f">       {message}",
            f"E       {message} at {tmp}",
            f"tests/test_a.py:{line}: AssertionError",
            "___________________________ test_beta ___________________________",
            "E       ValueError: boom",
            "=========================== short test summary info ===========================",
            f"[gw0] FAILED tests/test_a.py::test_alpha - {message}",
            "FAILED tests/test_a.py::test_beta - ValueError: boom",
            "========================= 2 failed, 3 passed in 0.52s =========================",
        ]
    )


def test_failing_ids_are_sorted_unique_and_read_through_xdist_prefixes() -> None:
    ids = failing_ids_from_output(_output() + "\nFAILED tests/test_a.py::test_beta - again")

    assert ids == ("tests/test_a.py::test_alpha", "tests/test_a.py::test_beta")


def test_failing_ids_are_capped() -> None:
    output = "\n".join(f"FAILED tests/test_many.py::test_{n:03d}" for n in range(MAX_RECORDED_IDS + 10))

    assert len(failing_ids_from_output(output)) == MAX_RECORDED_IDS


def test_excerpts_join_the_summary_line_and_the_traceback_section() -> None:
    excerpts = excerpts_by_test(_output(), ("tests/test_a.py::test_alpha", "tests/test_a.py::test_beta"))

    assert excerpts["tests/test_a.py::test_alpha"].startswith("[gw0] FAILED tests/test_a.py::test_alpha")
    assert "E       assert 1 == 2" in excerpts["tests/test_a.py::test_alpha"]
    assert "ValueError: boom" in excerpts["tests/test_a.py::test_beta"]
    assert "test_beta" not in excerpts["tests/test_a.py::test_alpha"].split("\n", 1)[1]


def test_excerpts_are_bounded_and_redacted() -> None:
    token = _fake_token()
    output = _output(message=f"token was {token} " + "x" * 2000)

    excerpt = excerpts_by_test(output, ("tests/test_a.py::test_alpha",))["tests/test_a.py::test_alpha"]

    assert len(excerpt) <= EXCERPT_CHARS_PER_TEST
    assert token not in excerpt


def test_environment_summary_names_identity_overrides_without_values() -> None:
    env = {
        "GIT_AUTHOR_NAME": "Leaky Reviewer",
        "GIT_COMMITTER_EMAIL": "leak@example.com",
        "PYTEST_ADDOPTS": "-q",
        "HOME": "/home/u",
        "PATH": "/usr/bin",
    }

    summary = environment_summary(env)

    assert summary["identity_overrides"] == ["GIT_AUTHOR_NAME", "GIT_COMMITTER_EMAIL"]
    assert summary["values"] == {"PYTEST_ADDOPTS": "-q"}
    assert summary["HOME_set"] is True
    assert summary["TMPDIR_set"] is False
    assert "PATH" not in summary["variables"]
    rendered = json.dumps(summary)
    assert "Leaky Reviewer" not in rendered
    assert "leak@example.com" not in rendered


def test_signature_ignores_line_numbers_and_temp_paths() -> None:
    ids = ("tests/test_a.py::test_alpha",)
    first = excerpts_by_test(_output(line=12, tmp="/tmp/pytest-of-u/pytest-3/x"), ids)
    second = excerpts_by_test(_output(line=40, tmp="/tmp/pytest-of-u/pytest-9/y"), ids)

    assert failure_signature(ids, first) == failure_signature(ids, second)


def test_signature_differs_for_other_ids_or_assertions() -> None:
    ids = ("tests/test_a.py::test_alpha",)
    base = failure_signature(ids, excerpts_by_test(_output(), ids))

    assert failure_signature(ids, excerpts_by_test(_output(message="assert 'a' == 'b'"), ids)) != base
    other = ("tests/test_a.py::test_beta",)
    assert failure_signature(other, excerpts_by_test(_output(), other)) != base


def _evidence(**overrides: object) -> TestFailureEvidence:
    ids = ("tests/test_a.py::test_alpha",)
    evidence = TestFailureEvidence(
        run_id="run-1",
        stage=STAGE_3,
        head_sha="abc123",
        failing_ids=ids,
        excerpts=excerpts_by_test(_output(), ids),
        environment=environment_summary({"GIT_AUTHOR_NAME": "x"}),
        classification="introduced",
    )
    return dataclasses.replace(evidence, **overrides)


def test_record_lands_in_the_flow_fragment(tmp_path) -> None:
    ledger = tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"
    ledger.parent.mkdir(parents=True)

    row = record_test_failure(str(ledger), _evidence(), branch=BRANCH, execution_id="E-2-CLA-CO5.5")

    fragment = ledger.parent / "pending" / "executions" / "I-17-record-demo.jsonl"
    assert row is not None
    written = [json.loads(line) for line in fragment.read_text().splitlines()]
    assert written[0]["action"] == "test_failure"
    assert written[0]["failing_test_ids"] == ["tests/test_a.py::test_alpha"]
    assert written[0]["classification"] == "introduced"
    assert written[0]["action_origin"] == "initial_implementation"
    assert written[0]["outcome"] == "failure"
    assert written[0]["failure_signature"] == row["failure_signature"]


def test_environment_and_pre_existing_rows_are_blocked_and_stage_4_is_a_ci_fix() -> None:
    captured: list[dict] = []

    def append(_path: str, row: dict, **_kwargs: object) -> dict:
        captured.append(row)
        return row

    for classification in ("environment", "pre-existing"):
        record_test_failure(
            "unused",
            _evidence(classification=classification, stage=STAGE_4),
            branch=BRANCH,
            execution_id="E-2",
            append=append,
        )

    assert [row["outcome"] for row in captured] == ["blocked", "blocked"]
    assert {row["action_origin"] for row in captured} == {"ci_fix"}


def test_append_failure_returns_none() -> None:
    def broken_append(*_args: object, **_kwargs: object) -> dict:
        raise OSError("disk full")

    assert record_test_failure("unused", _evidence(), branch=BRANCH, execution_id="E-2", append=broken_append) is None
