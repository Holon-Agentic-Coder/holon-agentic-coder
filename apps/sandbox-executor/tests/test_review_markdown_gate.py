"""The resolver's Markdown gate formats only changed Markdown, hermetically, with a fake ``npx`` on PATH."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.entrypoint import review_loop
from sandbox_executor.entrypoint.review_markdown_gate import changed_markdown_files, run_markdown_gate

PATHSPEC = review_loop._RESOLVER_PATHSPEC

# A fake Prettier: a Markdown file is "formatted" when it has no trailing whitespace. It logs every invocation.
FAKE_NPX = """#!/usr/bin/env python3
import sys, os
args = sys.argv[1:]
open(os.environ["FAKE_NPX_LOG"], "a").write(" ".join(args) + "\\n")
mode = args[1]
files = args[args.index("--") + 1:]
if os.environ.get("FAKE_NPX_STUCK") and mode == "--write":
    sys.exit(0)
bad = []
for f in files:
    text = open(f).read()
    clean = "\\n".join(line.rstrip() for line in text.split("\\n"))
    if mode == "--write":
        open(f, "w").write(clean)
    elif clean != text:
        bad.append(f)
for f in bad:
    print("[warn] " + f, file=sys.stderr)
sys.exit(1 if bad else 0)
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "feature-x")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    (root / "unrelated.md").write_text("dirty   \n")
    (root / "notes.md").write_text("clean\n")
    (root / "code.py").write_text("x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npx = bin_dir / "npx"
    npx.write_text(FAKE_NPX)
    npx.chmod(npx.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "npx.log"
    log.write_text("")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_NPX_LOG", str(log))
    return root


def _npx_calls(repo: Path) -> list[str]:
    return Path(os.environ["FAKE_NPX_LOG"]).read_text().splitlines()


def _run(repo: Path):
    return run_markdown_gate(str(repo), lambda a: review_loop.run_cmd(a, cwd=str(repo), check=False), PATHSPEC)


def test_unformatted_changed_markdown_is_formatted_and_committed(repo: Path) -> None:
    (repo / "notes.md").write_text("changed   \n")
    outcome = _run(repo)
    assert outcome.error is None and outcome.formatted == ("notes.md",)
    assert (repo / "notes.md").read_text() == "changed\n"
    _git(repo, "add", "-A", *PATHSPEC)
    _git(repo, "commit", "-qm", "fix")
    assert _git(repo, "show", "HEAD:notes.md") == "changed\n"


def test_change_without_markdown_starts_no_prettier(repo: Path) -> None:
    (repo / "code.py").write_text("x = 2\n")
    assert _run(repo).skipped
    assert _npx_calls(repo) == []


def test_unrelated_unformatted_markdown_is_untouched(repo: Path) -> None:
    (repo / "notes.md").write_text("changed   \n")
    _run(repo)
    assert (repo / "unrelated.md").read_text() == "dirty   \n"
    assert all("unrelated.md" not in call for call in _npx_calls(repo))


def test_path_with_a_space_and_a_deleted_file(repo: Path) -> None:
    (repo / "my notes.md").write_text("spaced   \n")
    (repo / "unrelated.md").unlink()
    assert changed_markdown_files(
        str(repo), lambda a: review_loop.run_cmd(a, cwd=str(repo), check=False), PATHSPEC
    ) == ["my notes.md"]
    assert _run(repo).formatted == ("my notes.md",)


def test_missing_npx_names_the_tool(repo: Path, monkeypatch, tmp_path: Path) -> None:
    (repo / "notes.md").write_text("changed   \n")
    git = Path(subprocess.run(["which", "git"], capture_output=True, text=True, check=True).stdout.strip())
    only_git = tmp_path / "only-git"
    only_git.mkdir()
    (only_git / "git").symlink_to(git)
    monkeypatch.setenv("PATH", str(only_git))
    outcome = _run(repo)
    assert outcome.error is not None and "'npx'" in outcome.error and "Prettier" in outcome.error


def test_format_that_cannot_converge_names_the_file(repo: Path, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_NPX_STUCK", "1")
    (repo / "notes.md").write_text("changed   \n")
    outcome = _run(repo)
    assert outcome.error is not None and "notes.md" in outcome.error


def _verify(repo: Path, monkeypatch, lint_code: int, test_code: int):
    calls: list[list[str]] = []
    real = review_loop.run_cmd

    def fake(args, cwd=None, env=None, check=True):
        if args[:3] == ["uv", "run", "task"]:
            calls.append(args)
            code = lint_code if args[-1] == "lint" else test_code
            return subprocess.CompletedProcess(args, code, stdout="", stderr="boom")
        return real(args, cwd=cwd, env=env, check=check)

    monkeypatch.setattr(review_loop, "run_cmd", fake)
    return review_loop._verify_before_push(str(repo)), calls


def test_lint_failure_never_reaches_the_gate(repo: Path, monkeypatch) -> None:
    (repo / "notes.md").write_text("changed   \n")
    (lint_ok, _, error), _ = _verify(repo, monkeypatch, 1, 0)
    assert not lint_ok and error == "Resolver pre-push lint failed (exit code 1): boom"
    assert _npx_calls(repo) == []


def test_test_failure_after_clean_markdown_keeps_its_message(repo: Path, monkeypatch) -> None:
    (repo / "notes.md").write_text("changed   \n")
    (lint_ok, tests_ok, error), _ = _verify(repo, monkeypatch, 0, 1)
    assert lint_ok and not tests_ok and error is not None
    assert error.startswith("Resolver pre-push test failed (exit code 1)")
    assert (repo / "notes.md").read_text() == "changed\n"


def test_verification_passes_after_formatting(repo: Path, monkeypatch) -> None:
    (repo / "notes.md").write_text("changed   \n")
    assert _verify(repo, monkeypatch, 0, 0)[0] == (True, True, None)
