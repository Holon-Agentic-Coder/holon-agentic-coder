from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.check_ledger_rows_append_only import check_append_only, compare_rows, main, rows

LEDGER = "holon-knowledge/ledger"
FRAGMENT = f"{LEDGER}/pending/plans/I-1795000001-alpha.jsonl"
BASE_FILES = {
    f"{LEDGER}/executions.jsonl": '{"id": "E-1"}\n{"id": "E-2"}\n',
    f"{LEDGER}/intents.jsonl": '{"id": "I-1"}\n{"id": "I-2"}\n',
    f"{LEDGER}/plans.jsonl": '{"id": "P-1"}\n{"id": "P-2"}\n{"id": "P-3"}\n',
    FRAGMENT: '{"id": "P-4"}\n{"id": "P-5"}\n',
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def _commit(repo: Path, files: dict[str, str | None]) -> str:
    for rel, text in files.items():
        if text is None:
            (repo / rel).unlink()
        else:
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "change")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def base(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    _git(tmp_path, "init", "-q", str(repo))
    return repo, _commit(repo, BASE_FILES)


def _problems(base: tuple[Path, str], files: dict[str, str | None]) -> list[str]:
    repo, base_sha = base
    return check_append_only(repo, base_sha, _commit(repo, files))[0]


PASSING_EDITS = {
    "appended rows": {
        f"{LEDGER}/plans.jsonl": BASE_FILES[f"{LEDGER}/plans.jsonl"] + '{"id": "P-9"}\n',
        FRAGMENT: BASE_FILES[FRAGMENT] + '{"id": "P-6"}\n',
    },
    "new fragments": {
        f"{LEDGER}/pending/intents/I-1795000002-beta.jsonl": '{"id": "I-3"}\n',
        f"{LEDGER}/pending/executions/_unscoped.jsonl": '{"id": "E-3"}\n',
    },
    "promotion": {
        f"{LEDGER}/plans.jsonl": BASE_FILES[f"{LEDGER}/plans.jsonl"] + BASE_FILES[FRAGMENT],
        FRAGMENT: None,
    },
}


@pytest.mark.parametrize("edit", sorted(PASSING_EDITS))
def test_additions_new_fragments_and_promotion_pass(base: tuple[Path, str], edit: str) -> None:
    assert _problems(base, PASSING_EDITS[edit]) == []


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (
            {f"{LEDGER}/intents.jsonl": '{"id": "I-1"}\n{"id": "I-X"}\n'},
            [f"{LEDGER}/intents.jsonl:2: changed"],
        ),
        (
            {f"{LEDGER}/plans.jsonl": '{"id": "P-1"}\n{"id": "P-2"}\n'},
            [f"{LEDGER}/plans.jsonl:3: deleted"],
        ),
        (
            {f"{LEDGER}/executions.jsonl": '{"id": "E-2"}\n{"id": "E-1"}\n'},
            [f"{LEDGER}/executions.jsonl:1: moved", f"{LEDGER}/executions.jsonl:2: moved"],
        ),
        ({f"{LEDGER}/intents.jsonl": None}, [f"{LEDGER}/intents.jsonl: deleted"]),
        ({FRAGMENT: None}, [f"{FRAGMENT}: fragment removed"]),
        (
            {FRAGMENT: None, f"{LEDGER}/plans.jsonl": BASE_FILES[f"{LEDGER}/plans.jsonl"] + '{"id": "P-4"}\n'},
            [f"{FRAGMENT}: fragment removed"],
        ),
        (
            {f"{LEDGER}/executions.jsonl": '{"id": "E-1"}\n{"id": "E-2"} \n{"id": "E-3"}\n'},
            [f"{LEDGER}/executions.jsonl:2: changed"],
        ),
    ],
    ids=["edited", "deleted", "swapped", "ledger-removed", "fragment-removed", "half-promoted", "trailing-space"],
)
def test_a_base_row_edit_fails(base: tuple[Path, str], files: dict[str, str | None], expected: list[str]) -> None:
    assert _problems(base, files) == expected


def test_main_reports_a_violation_with_exit_1(base: tuple[Path, str], capsys: pytest.CaptureFixture[str]) -> None:
    repo, base_sha = base
    head = _commit(repo, {f"{LEDGER}/intents.jsonl": '{"id": "I-1"}\n'})

    assert main(["--repo", str(repo), "--base", base_sha, "--head", head]) == 1
    assert f"{LEDGER}/intents.jsonl:2: deleted" in capsys.readouterr().err


def test_main_passes_and_counts_the_files(base: tuple[Path, str], capsys: pytest.CaptureFixture[str]) -> None:
    repo, base_sha = base

    assert main(["--repo", str(repo), "--base", base_sha, "--head", "HEAD"]) == 0
    assert "ledger rows append-only: 4 files checked" in capsys.readouterr().out


def test_rows_added_on_the_base_after_the_branch_point_are_not_edits(base: tuple[Path, str]) -> None:
    repo, branch_point = base
    _git(repo, "checkout", "-q", "-b", "flow")
    head = _commit(repo, {f"{LEDGER}/pending/intents/I-1795000003-gamma.jsonl": '{"id": "I-5"}\n'})
    _git(repo, "checkout", "-q", branch_point)
    later_base = _commit(repo, {f"{LEDGER}/intents.jsonl": BASE_FILES[f"{LEDGER}/intents.jsonl"] + '{"id": "I-4"}\n'})

    assert check_append_only(repo, later_base, head) == ([], 4)


def test_an_unknown_base_fails_closed(base: tuple[Path, str], capsys: pytest.CaptureFixture[str]) -> None:
    repo, _ = base

    assert main(["--repo", str(repo), "--base", "0" * 40, "--head", "HEAD"]) == 2
    assert "cannot read the base ledgers" in capsys.readouterr().err


def test_a_base_without_common_history_fails_closed(base: tuple[Path, str]) -> None:
    repo, base_sha = base
    _git(repo, "checkout", "-q", "--orphan", "unrelated")
    orphan = _commit(repo, {"README.md": "unrelated\n"})

    assert main(["--repo", str(repo), "--base", orphan, "--head", base_sha]) == 2


def test_an_unterminated_last_row_equals_its_terminated_form() -> None:
    assert rows(b'{"a": 1}\n{"b": 2}') == rows(b'{"a": 1}\n{"b": 2}\n') == [b'{"a": 1}', b'{"b": 2}']
    assert rows(b"") == []


def test_compare_rows_caps_the_report_of_a_mass_rewrite() -> None:
    problems = compare_rows("x.jsonl", [str(n).encode() for n in range(30)], [])

    assert problems[:2] == ["x.jsonl:1: deleted", "x.jsonl:2: deleted"]
    assert problems[-1] == "x.jsonl: ... and 10 more"
    assert len(problems) == 21
