from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from sandbox_executor import cli
from sandbox_executor.cli.commands import discover
from sandbox_executor.harness_reference_patterns import sample_reference
from sandbox_executor.intent_assumption_rules import default_assumptions_fields

_FILES = {
    "src/pkg/__init__.py": "",
    "src/pkg/core.py": "VALUE = 1\n",
    "src/pkg/lonely.py": "OTHER = 2\n",
    "tests/test_core.py": "from pkg.core import VALUE\n",
}


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-b", "main", "-q")
    for rel, text in _FILES.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text, encoding="utf-8")
        _git(path, "add", rel)
        _git(path, "commit", "-q", "-m", f"add {rel}")
    return path


def _intent_file(tmp_path: Path, scope: list[str], goal: str = "") -> str:
    intent_dir = tmp_path / "intents"
    intent_dir.mkdir(exist_ok=True)
    path = intent_dir / "intent.json"
    payload = {
        **default_assumptions_fields(),
        "goal": goal,
        "scope": scope,
        "acceptance_criteria": ["core.py and lonely.py"],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _run(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(argv)
    return int(exc_info.value.code or 0)


def _snapshot(repo: Path) -> tuple[bytes, int, str, str, list[str]]:
    index = repo / ".git" / "index"
    index_bytes, index_mtime = index.read_bytes(), index.stat().st_mtime_ns
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    status = _git(repo, "status", "--porcelain", "--untracked-files=all", env=env)
    head = _git(repo, "rev-parse", "HEAD", env=env)
    refs = sorted(path.relative_to(repo).as_posix() for path in (repo / ".git" / "refs").rglob("*"))
    return index_bytes, index_mtime, status, head, refs


def test_intent_check_is_discovered_next_to_an_unchanged_intent_command() -> None:
    names = [module.__name__.rpartition(".")[2] for module in discover()]
    assert names.index("intent") + 1 == names.index("intent_check")
    parser = cli.build_parser()
    intent_args = parser.parse_args(["intent", "intents/x.json", "--target-worktree", "/w"])
    assert (intent_args.intent_file, intent_args.target_worktree) == ("intents/x.json", "/w")
    assert intent_args._run.__module__.endswith("commands.intent")
    check_args = parser.parse_args(["intent-check", "intents/x.json", "--json"])
    assert check_args._run.__module__.endswith("commands.intent_check")
    assert (check_args.json, check_args.target_worktree) == (True, None)


def test_exit_code_is_zero_without_proposals(repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    intent = _intent_file(tmp_path, ["src/pkg/lonely.py"])
    assert _run(["intent-check", intent, "--target-worktree", str(repo)]) == 0
    assert "0 proposal(s)." in capsys.readouterr().out


def test_exit_code_is_one_with_proposals(repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    intent = _intent_file(tmp_path, ["src/pkg/core.py"])
    assert _run(["intent-check", intent, "--target-worktree", str(repo)]) == 1
    assert "tests/test_core.py (test imports listed module; from src/pkg/core.py)" in capsys.readouterr().out


def test_json_output_has_exactly_the_report_keys(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    intent = _intent_file(tmp_path, ["src/pkg/core.py", "src/pkg/gone.py"])
    assert _run(["intent-check", intent, "--target-worktree", str(repo), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"missing", "proposed", "warnings"}
    assert payload["missing"] == ["src/pkg/gone.py"]
    assert payload["proposed"] == [
        {"path": "tests/test_core.py", "reason": "test imports listed module", "source": "src/pkg/core.py"}
    ]


def test_command_leaves_the_repository_untouched(repo: Path, tmp_path: Path) -> None:
    (repo / "src/pkg/core.py").write_text("VALUE = 3\n", encoding="utf-8")
    (repo / "scratch.txt").write_text("untracked\n", encoding="utf-8")
    intent = _intent_file(tmp_path, ["src/pkg/core.py", "tests/test_core.py", "src/pkg/__init__.py"])
    before = _snapshot(repo)
    _run(["intent-check", intent, "--target-worktree", str(repo)])
    _run(["intent-check", intent, "--target-worktree", str(repo), "--json"])
    assert _snapshot(repo) == before
    assert sorted(path.name for path in (tmp_path / "intents").iterdir()) == ["intent.json"]


def test_invalid_intent_file_exits_two(repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    intent = tmp_path / "broken.json"
    intent.write_text("not json", encoding="utf-8")
    assert _run(["intent-check", str(intent), "--target-worktree", str(repo)]) == 2
    assert capsys.readouterr().err.startswith("Error: cannot read intent file")


def test_missing_worktree_exits_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    intent = _intent_file(tmp_path, ["src/pkg/core.py"])
    assert _run(["intent-check", intent, "--target-worktree", str(tmp_path / "absent")]) == 2
    assert capsys.readouterr().err.startswith("Error: target worktree is not a directory")


def test_subdirectory_worktree_resolves_to_repository_root(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    intent = _intent_file(tmp_path, ["src/pkg/core.py"])
    assert _run(["intent-check", intent, "--target-worktree", str(repo / "src" / "pkg")]) == 1
    assert "tests/test_core.py" in capsys.readouterr().out


@pytest.mark.parametrize("as_json", [False, True])
def test_glob_scope_entry_exits_two(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], as_json: bool
) -> None:
    intent = _intent_file(tmp_path, ["src/pkg/*.py"])
    argv = ["intent-check", intent, "--target-worktree", str(repo), *(["--json"] if as_json else [])]
    assert _run(argv) == 2
    out = capsys.readouterr().out
    if as_json:
        assert json.loads(out)["violations"] == [
            "scope entry 'src/pkg/*.py' contains a glob character: list each file on its own; a directory or glob "
            "widens the touch set and blocks every other flow at Stage 3 admission"
        ]
    else:
        assert out.startswith("Violations:\n  - scope entry 'src/pkg/*.py' contains a glob character")


@pytest.mark.parametrize("as_json", [False, True])
def test_harness_reference_in_goal_exits_two_without_echoing_it(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], as_json: bool
) -> None:
    sample = sample_reference("agent-instruction directory")
    intent = _intent_file(tmp_path, ["src/pkg/lonely.py"], goal=f"Also {sample}")
    argv = ["intent-check", intent, "--target-worktree", str(repo), *(["--json"] if as_json else [])]
    assert _run(argv) == 2
    out = capsys.readouterr().out
    assert sample not in out
    assert "intent field 'goal' names a control-plane agent-instruction directory" in out
    if as_json:
        assert len(json.loads(out)["violations"]) == 1
