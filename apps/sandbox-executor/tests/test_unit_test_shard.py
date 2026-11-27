from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.unit_test_shard import collected_test_files, files_in_shard, main, shard_of
from tests.hermetic_fixtures import REPO_ROOT, UNIT_MARKER_EXPRESSION

SHARD_SCRIPT = Path(REPO_ROOT) / "scripts" / "unit_test_shard.py"
WORKFLOW = Path(REPO_ROOT) / ".github" / "workflows" / "test-unit.yml"
TESTS_ROOT = Path(REPO_ROOT) / "apps" / "sandbox-executor" / "tests"
SKIPPED_DIRECTORIES = {"e2e", "__pycache__"}
SHARDS = 2


def real_unit_test_files() -> list[str]:
    # why: a nested pytest collection would add ~20 s to every suite run while yielding the same file set.
    return sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in TESTS_ROOT.rglob("test_*.py")
        if not SKIPPED_DIRECTORIES.intersection(path.relative_to(TESTS_ROOT).parts[:-1])
    )


def collection_output(files: list[str]) -> str:
    test_lines = [f"{path}::test_placeholder" for path in files]
    return "\n".join([*test_lines, "", f"{len(files)} tests collected in 1.00s", ""])


def shard_printed_by_the_script(collection: str, shard: int) -> list[str]:
    completed = subprocess.run(
        [sys.executable, str(SHARD_SCRIPT), "--shard", str(shard), "--shards", str(SHARDS)],
        cwd=REPO_ROOT,
        input=collection,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.splitlines()


def test_real_file_list_holds_this_test_file() -> None:
    assert "apps/sandbox-executor/tests/test_unit_test_shard.py" in real_unit_test_files()


def test_script_splits_the_real_file_list_into_two_balanced_disjoint_shards() -> None:
    files = real_unit_test_files()
    collection = collection_output(files)
    first, second = (shard_printed_by_the_script(collection, shard) for shard in (1, 2))

    assert len(first) == len(set(first))
    assert len(second) == len(set(second))
    assert not set(first) & set(second)
    assert set(first) | set(second) == set(files)
    for shard_files in (first, second):
        assert 0.4 <= len(shard_files) / len(files) <= 0.6


def test_shard_of_a_file_does_not_change_when_unrelated_files_are_added() -> None:
    files = real_unit_test_files()
    synthetic = [f"apps/sandbox-executor/tests/test_synthetic_{index}.py" for index in range(200)]
    shard_before = {path: shard_of(path, SHARDS) for path in files}

    for shard in (1, 2):
        original_members = set(files_in_shard(files, shard, SHARDS))
        grown_members = set(files_in_shard([*files, *synthetic], shard, SHARDS))
        assert grown_members & set(files) == original_members
    assert {path: shard_of(path, SHARDS) for path in files} == shard_before


@pytest.mark.parametrize("shards", [1, 2, 3])
def test_shard_of_is_numbered_from_one(shards: int) -> None:
    assert {shard_of(path, shards) for path in real_unit_test_files()} <= set(range(1, shards + 1))


def test_shard_of_ignores_a_leading_dot_slash() -> None:
    assert shard_of("./a/test_x.py", SHARDS) == shard_of("a/test_x.py", SHARDS)


def test_shard_of_refuses_zero_shards() -> None:
    with pytest.raises(ValueError):
        shard_of("a/test_x.py", 0)


def test_collection_parser_keeps_unique_files_only() -> None:
    lines = [
        "a/test_x.py::test_one",
        "a/test_x.py::test_two[a::b]",
        "b/test_y.py::TestCase::test_three",
        "",
        "PytestWarning: something::odd",
        "9531/9552 tests collected (21 deselected) in 19.01s",
    ]

    assert collected_test_files(lines) == ["a/test_x.py", "b/test_y.py"]


def test_main_refuses_an_empty_collection() -> None:
    stdout = io.StringIO()

    assert main(["--shard", "1", "--shards", "2"], io.StringIO("no tests ran in 0.01s\n"), stdout) == 2
    assert stdout.getvalue() == ""


def test_main_refuses_a_shard_out_of_range() -> None:
    collection = io.StringIO(collection_output(real_unit_test_files()))

    assert main(["--shard", "3", "--shards", "2"], collection, io.StringIO()) == 2


def test_workflow_runs_two_macos_shards_and_the_whole_suite_on_ubuntu() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    pytest_flags = f'uv run pytest -n auto --dist worksteal -v -l --tb=short -m "{UNIT_MARKER_EXPRESSION}"'

    assert "        os: [ubuntu-latest, macos-latest]\n        shard: [1, 2]\n" in text
    assert "        exclude:\n          - os: ubuntu-latest\n            shard: 2\n" in text
    assert "          - os: ubuntu-latest\n            timing-min-delta: 120\n            shards: 1\n" in text
    assert "          - os: macos-latest\n            timing-min-delta: 150\n            shards: 2\n" in text
    for shard in (1, 2):
        assert (
            f"          - os: macos-latest\n            shard: {shard}\n"
            f"            timing-job: unit-shard-{shard}\n            gate-leg: shard {shard} of 2\n"
        ) in text
    assert f'uv run pytest --collect-only -q -m "{UNIT_MARKER_EXPRESSION}" > unit-collection.txt' in text
    assert (
        'python3 scripts/unit_test_shard.py --shard "${{ matrix.shard }}" --shards "${{ matrix.shards }}"'
        " < unit-collection.txt > unit-shard-files.txt"
    ) in text
    assert f"{pytest_flags} --junitxml=unit-report.xml @unit-shard-files.txt\n" in text
    assert f"          else\n            {pytest_flags} --junitxml=unit-report.xml\n          fi\n" in text


def test_workflow_keeps_timing_records_and_baselines_per_shard() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert '--workflow test-unit --job "${{ matrix.timing-job }}" --os "${{ matrix.os }}"' in text
    assert "name: ci-timing-test-unit-${{ matrix.timing-job }}-${{ matrix.os }}" in text
    assert "            timing-job: unit\n" in text
