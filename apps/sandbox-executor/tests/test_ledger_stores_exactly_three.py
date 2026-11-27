from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.check_ledger_stores import LedgerStores, classify, find_ledger_stores, main, store_problems

REPO_ROOT = Path(__file__).resolve().parents[3]
CLEAN_LAYOUT = {
    "holon-knowledge/ledger/executions.jsonl": '{"id": "E-1"}\n',
    "holon-knowledge/ledger/intents.jsonl": '{"id": "I-1"}\n',
    "holon-knowledge/ledger/plans.jsonl": '{"id": "P-1"}\n',
    "holon-knowledge/ledger/pending/executions/I-1795000001-alpha.jsonl": '{"id": "E-2"}\n',
    "holon-knowledge/ledger/pending/intents/I-1795000001-alpha.jsonl": '{"id": "I-2"}\n',
    "holon-knowledge/ledger/pending/plans/_unscoped.jsonl": '{"id": "P-2"}\n',
    "holon-knowledge/kb/failure_modes/KB-failure-x.json": "{}\n",
    "holon-knowledge/wisdom/heuristics/WB-heuristic-x.json": "{}\n",
}


def _repo(tmp_path: Path, files: dict[str, str | None]) -> Path:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    for rel, text in {**CLEAN_LAYOUT, **files}.items():
        if text is not None:
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(text, encoding="utf-8")
    return repo


def test_the_clean_layout_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--repo", str(_repo(tmp_path, {}))]) == 0
    assert "ledger kinds: executions, intents, plans" in capsys.readouterr().out


@pytest.mark.parametrize(
    "path",
    [
        "holon-knowledge/ledger/events.jsonl",
        "apps/foo/audit.jsonl",
        "holon-knowledge/ledger/pending/events/I-1.jsonl",
        "holon-knowledge/ledger/pending/plans/I-1.json",
    ],
)
def test_a_fourth_store_fails_and_is_named(tmp_path: Path, capsys: pytest.CaptureFixture[str], path: str) -> None:
    assert main(["--repo", str(_repo(tmp_path, {path: "{}\n"}))]) == 1
    assert f"fourth ledger-like store: {path}" in capsys.readouterr().err


def test_jsonl_inside_the_excluded_knowledge_folders_passes(tmp_path: Path) -> None:
    files = {
        "holon-knowledge/kb/failure_modes/rows.jsonl": "{}\n",
        "holon-knowledge/wisdom/heuristics/rows.jsonl": "{}\n",
    }

    assert main(["--repo", str(_repo(tmp_path, files))]) == 0


def test_a_missing_canonical_ledger_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _repo(tmp_path, {"holon-knowledge/ledger/plans.jsonl": None})

    assert main(["--repo", str(repo)]) == 1
    assert "missing ledger: holon-knowledge/ledger/plans.jsonl" in capsys.readouterr().err


def test_a_gitignored_jsonl_is_not_a_store(tmp_path: Path) -> None:
    files = {".gitignore": "scratch/\n", "scratch/run.jsonl": "{}\n"}

    assert main(["--repo", str(_repo(tmp_path, files))]) == 0


def test_a_directory_outside_git_fails_closed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    assert main(["--repo", str(plain)]) == 2
    assert str(plain) in capsys.readouterr().err


def test_the_real_repository_passes() -> None:
    assert main(["--repo", str(REPO_ROOT)]) == 0


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("holon-knowledge/ledger/plans.jsonl", ("canonical", "plans")),
        ("holon-knowledge/ledger/pending/intents/I-1-a.jsonl", ("fragment", "intents")),
        (
            "holon-knowledge/ledger/pending/intents/deep/I-1.jsonl",
            ("foreign", "holon-knowledge/ledger/pending/intents/deep/I-1.jsonl"),
        ),
        ("holon-knowledge/ledger/pending/README.md", ("foreign", "holon-knowledge/ledger/pending/README.md")),
        ("holon-knowledge/ledger/reviews.jsonl", ("foreign", "holon-knowledge/ledger/reviews.jsonl")),
        ("holon-knowledge/kb/failure_modes/x.jsonl", None),
        ("holon-knowledge/ledger/README.md", None),
        ("scripts/check_ledger_stores.py", None),
    ],
)
def test_classify(path: str, expected: tuple[str, str] | None) -> None:
    assert classify(path) == expected


def test_fragments_alone_do_not_make_a_kind_present() -> None:
    stores = find_ledger_stores(["holon-knowledge/ledger/pending/plans/I-1.jsonl"])

    assert stores == LedgerStores(frozenset(), ())
    assert len(store_problems(stores)) == 3
