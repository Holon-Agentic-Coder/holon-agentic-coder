from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.check_self_doc_behavior import (
    ChangedFileEntry,
    TriggerDecision,
    compare_python_sources,
    main,
    normalise_python_source,
    parse_labels_json,
    should_run_self_doc_gate,
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    return repo


def _commit_files(repo: Path, files: dict[str, str], message: str) -> str:
    for relative_path, content in files.items():
        target = repo / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repo, "add", *files.keys())
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def test_stripping_docstrings_and_comments_preserves_normalised_ast() -> None:
    before = '''"""Module docstring."""
# Standalone comment


class Greeter:
    """Class docstring."""

    def greet(self, name: str) -> str:
        """Function docstring."""
        # Inline explanation
        def format_name(value: str) -> str:
            """Nested docstring."""
            return value.strip()  # trailing comment

        return f"hi {format_name(name)}"

    async def greet_async(self, name: str) -> str:
        """Async docstring."""
        return self.greet(name)
'''
    after = """class Greeter:
    def greet(self, name: str) -> str:
        def format_name(value: str) -> str:
            return value.strip()

        return f"hi {format_name(name)}"

    async def greet_async(self, name: str) -> str:
        return self.greet(name)
"""
    assert normalise_python_source(before) == normalise_python_source(after)
    result = compare_python_sources("sample.py", before, after)
    assert result.equivalent is True


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ('class Empty:\n    """Only a docstring."""\n', "class Empty:\n    pass\n"),
        ('def noop() -> None:\n    """Only a docstring."""\n', "def noop() -> None:\n    pass\n"),
        ('async def anoop() -> None:\n    """Only a docstring."""\n', "async def anoop() -> None:\n    pass\n"),
    ],
    ids=["class", "function", "async-function"],
)
def test_empty_body_docstring_removal_matches_pass_substitution(before: str, after: str) -> None:
    result = compare_python_sources("empty_body.py", before, after)
    assert result.equivalent is True


@pytest.mark.parametrize("after", ["", "# comment only\n"], ids=["empty", "comment-only"])
def test_docstring_only_module_matches_empty_or_comment_only_module(after: str) -> None:
    before = '"""Only a module docstring."""\n'
    result = compare_python_sources("module_only.py", before, after)
    assert result.equivalent is True


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (
            'def f() -> int:\n    x = 1\n    "not a docstring"\n    return x\n',
            "def f() -> int:\n    x = 1\n    return x\n",
        ),
        (
            'def f(flag: bool) -> None:\n    if flag:\n        "branch string"\n',
            "def f(flag: bool) -> None:\n    if flag:\n        pass\n",
        ),
    ],
    ids=["second-statement", "if-branch"],
)
def test_removing_non_leading_string_expression_fails(before: str, after: str) -> None:
    result = compare_python_sources("non_leading.py", before, after)
    assert result.equivalent is False


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ('def f() -> int:\n    """doc"""\n    return 1\n', "def f() -> int:\n    return 2\n"),
        (
            'def f(a: int, b: int) -> int:\n    """doc"""\n    return a + b\n',
            "def f(a: int, b: int) -> int:\n    return a - b\n",
        ),
        ('def f(x: int = 1) -> int:\n    """doc"""\n    return x\n', "def f(x: int = 2) -> int:\n    return x\n"),
        ('def f(x: int) -> int:\n    """doc"""\n    return x\n', "def f(x: int) -> int:\n    x += 1\n    return x\n"),
    ],
    ids=["return-value", "operator", "default-arg", "extra-statement"],
)
def test_sneaked_code_change_fails(before: str, after: str) -> None:
    result = compare_python_sources("sneaked.py", before, after)
    assert result.equivalent is False


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("def broken(:\n    pass\n", "def ok() -> None:\n    pass\n"),
        ("def ok() -> None:\n    pass\n", "def broken(:\n    pass\n"),
    ],
    ids=["invalid-before", "invalid-after"],
)
def test_syntax_error_in_before_or_after_fails(before: str, after: str) -> None:
    result = compare_python_sources("broken.py", before, after)
    assert result.equivalent is False
    assert "syntax error" in result.detail


def test_parse_labels_json_handles_github_objects_strings_and_invalid_payloads() -> None:
    github_payload = json.dumps([{"name": "Self-Doc"}, {"name": "CI"}])
    assert parse_labels_json(github_payload) == ("self-doc", "ci")
    assert parse_labels_json(json.dumps(["self-explaining", ""])) == ("self-explaining",)
    assert parse_labels_json("") == ()
    assert parse_labels_json("{not json") == ()
    assert parse_labels_json(json.dumps({"name": "self-doc"})) == ()


def test_should_run_self_doc_gate_on_label_even_with_code_change() -> None:
    code_change = [ChangedFileEntry("M", "app.py", "app.py", "x = 1\n", "x = 2\n")]
    labels = parse_labels_json(json.dumps([{"name": "Self-Explaining"}]))
    assert should_run_self_doc_gate(labels, "unrelated title", code_change) == TriggerDecision(True, "label")
    assert should_run_self_doc_gate(["self-doc"], "unrelated title", code_change) == TriggerDecision(True, "label")


@pytest.mark.parametrize("title", ["refactor: self-doc cleanup", "[self-doc] strip comments", "Self Explaining pass"])
def test_should_run_self_doc_gate_on_title_even_with_code_change(title: str) -> None:
    code_change = [ChangedFileEntry("M", "app.py", "app.py", "x = 1\n", "x = 2\n")]
    assert should_run_self_doc_gate([], title, code_change) == TriggerDecision(True, "title")


def test_should_run_self_doc_gate_on_diff_only() -> None:
    doc_only = ChangedFileEntry("M", "app.py", "app.py", '"""doc"""\nx = 1\n', "x = 1\n")
    test_file = ChangedFileEntry(
        "A", "tests/test_check_self_doc_behavior.py", "tests/test_check_self_doc_behavior.py", None, None
    )
    assert should_run_self_doc_gate([], "chore: tidy", [doc_only]) == TriggerDecision(True, "diff-only")
    assert should_run_self_doc_gate([], "chore: tidy", [test_file]) == TriggerDecision(True, "diff-only")
    assert should_run_self_doc_gate([], "chore: tidy", [doc_only, test_file]) == TriggerDecision(True, "diff-only")


@pytest.mark.parametrize(
    "entries",
    [
        [],
        [ChangedFileEntry("M", "app.py", "app.py", "x = 1\n", "x = 2\n")],
        [ChangedFileEntry("M", "docs/guide.md", "docs/guide.md", None, None)],
    ],
    ids=["empty", "python-code-change", "non-python-file"],
)
def test_should_run_self_doc_gate_skips_non_self_doc_changes(entries: list[ChangedFileEntry]) -> None:
    assert should_run_self_doc_gate([], "feat: update behavior", entries) == TriggerDecision(False, "not-self-doc")


def test_cli_passes_on_docstring_and_comment_removal_with_pass_substitution(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base = _commit_files(
        repo,
        {
            "pkg/mod.py": '"""Module doc."""\n\n\nclass Empty:\n    """Empty doc."""\n\n\ndef add(a: int, b: int) -> int:\n    """Add."""\n    # sum values\n    return a + b\n'
        },
        "base",
    )
    head = _commit_files(
        repo,
        {"pkg/mod.py": "class Empty:\n    pass\n\n\ndef add(a: int, b: int) -> int:\n    return a + b\n"},
        "strip docstrings and comments",
    )
    assert main(["--root", str(repo), "--base", base, "--head", head]) == 0
    assert main(["--root", str(repo), "--range", f"{base}..{head}", "--auto-trigger"]) == 0


def test_cli_fails_on_sneaked_code_change(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base = _commit_files(repo, {"pkg/mod.py": 'def value() -> int:\n    """Return one."""\n    return 1\n'}, "base")
    head = _commit_files(repo, {"pkg/mod.py": "def value() -> int:\n    return 2\n"}, "sneaked change")
    labels_json = json.dumps([{"name": "self-doc"}])
    assert main(["--root", str(repo), "--base", base, "--head", head]) == 1
    assert (
        main(["--root", str(repo), "--base", base, "--head", head, "--auto-trigger", "--pr-labels-json", labels_json])
        == 1
    )
    assert (
        main(["--root", str(repo), "--base", base, "--head", head, "--auto-trigger", "--pr-title", "[self-doc] tidy"])
        == 1
    )


def test_cli_skips_normal_code_change_under_auto_trigger(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _init_repo(tmp_path)
    base = _commit_files(repo, {"pkg/mod.py": "VALUE = 1\n"}, "base")
    head = _commit_files(repo, {"pkg/mod.py": "VALUE = 2\n"}, "normal feature")
    exit_code = main(
        [
            "--root",
            str(repo),
            "--base",
            base,
            "--head",
            head,
            "--auto-trigger",
            "--pr-title",
            "feat: bump value",
            "--pr-labels-json",
            "[]",
        ]
    )
    assert exit_code == 0
    assert "skipped" in capsys.readouterr().out


def test_cli_returns_two_on_invalid_revisions(tmp_path: Path) -> None:
    assert main(["--root", str(tmp_path), "--range", "invalid-range"]) == 2
    assert main(["--root", str(tmp_path), "--base", "HEAD", "--head", "HEAD", "--range", "HEAD..HEAD"]) == 2
