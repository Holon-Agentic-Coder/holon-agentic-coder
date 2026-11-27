from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.check_self_doc_behavior import (
    ChangedEntry,
    compare_python_sources,
    detect_trigger_reason,
    has_self_doc_label,
    has_self_doc_title,
    is_diff_only_self_doc,
    is_self_doc_test_path,
    main,
    normalised_ast_dump,
)

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github" / "workflows" / "test-self-doc-behavior.yml"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _commit(repo: Path, files: dict[str, str]) -> str:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "change")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-b", "main", "-q")
    _commit(
        path,
        {
            "pkg/mod.py": (
                '"""Module doc."""\n'
                "# top comment\n"
                "class Empty:\n"
                '    """Class doc."""\n\n'
                "def compute(x: int = 1) -> int:\n"
                '    """Compute doc."""\n'
                "    # inline comment\n"
                "    return x + 1\n\n"
                "async def noop() -> None:\n"
                '    """Async doc."""\n'
            ),
            "pkg/__init__.py": '"""Package doc."""\n',
        },
    )
    return path


def test_docstring_and_comment_removals_preserve_normalised_ast() -> None:
    before = (
        '"""Module doc."""\n'
        "# module comment\n"
        "class Box:\n"
        '    """Class doc."""\n'
        "    value = 1  # trailing\n\n"
        "    def get(self):\n"
        '        """Method doc."""\n'
        "        return self.value\n\n"
        "async def fetch(url):\n"
        '    """Async doc."""\n'
        "    return url\n"
    )
    after = (
        "class Box:\n"
        "    value = 1\n\n"
        "    def get(self):\n"
        "        return self.value\n\n"
        "async def fetch(url):\n"
        "    return url\n"
    )
    assert normalised_ast_dump(before) == normalised_ast_dump(after)
    assert compare_python_sources(before, after).equivalent is True


def test_docstring_only_bodies_replaced_with_pass_preserve_normalised_ast() -> None:
    before = (
        "class CustomError(Exception):\n"
        '    """Empty class."""\n\n'
        "def stub():\n"
        '    """Empty func."""\n\n'
        "async def async_stub():\n"
        '    """Empty async func."""\n'
    )
    after = "class CustomError(Exception):\n    pass\n\ndef stub():\n    pass\n\nasync def async_stub():\n    pass\n"
    assert normalised_ast_dump(before) == normalised_ast_dump(after)
    assert compare_python_sources(before, after).equivalent is True


def test_module_only_docstring_strips_to_empty_module_without_pass() -> None:
    assert normalised_ast_dump('"""Only a docstring."""\n') == normalised_ast_dump("")
    assert normalised_ast_dump('"""Only a docstring."""\n') != normalised_ast_dump("pass\n")


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ('def f():\n    "doc"\n    "second"\n', "def f():\n    pass\n"),
        ('def f(x):\n    if x:\n        "in branch"\n', "def f(x):\n    if x:\n        pass\n"),
        ('def f():\n    x = 1\n    "after stmt"\n', "def f():\n    x = 1\n"),
    ],
    ids=["second-string", "branch-string", "trailing-string"],
)
def test_non_leading_string_expressions_are_not_stripped(before: str, after: str) -> None:
    result = compare_python_sources(before, after)
    assert result.equivalent is False
    assert "AST changed" in result.reason


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("def f(x=1):\n    return x\n", "def f(x=2):\n    return x\n"),
        ("def f(a, b):\n    return a + b\n", "def f(a, b):\n    return a - b\n"),
        ("def f():\n    return True\n", "def f():\n    return False\n"),
        ("def f():\n    x = 1\n    return x\n", "def f():\n    x = 1\n    y = 2\n    return x\n"),
        ("def f():\n    x = 1\n    return x\n", "def f():\n    y = 1\n    return y\n"),
    ],
    ids=["default-arg", "operator", "return-value", "added-stmt", "renamed-var"],
)
def test_sneaked_runtime_code_changes_fail_comparison(before: str, after: str) -> None:
    assert compare_python_sources(before, after).equivalent is False


def test_syntax_error_returns_parse_failure() -> None:
    after_err = compare_python_sources("x = 1\n", "def broken(:\n")
    assert after_err.equivalent is False
    assert "after syntax error" in after_err.reason

    before_err = compare_python_sources("def broken(:\n", "x = 1\n")
    assert before_err.equivalent is False
    assert "before syntax error" in before_err.reason


def test_cli_passes_on_real_docstring_and_comment_removal_with_pass_substitution(repo: Path) -> None:
    _git(repo, "checkout", "-q", "-b", "self-doc")
    head = _commit(
        repo,
        {
            "pkg/mod.py": (
                "class Empty:\n"
                "    pass\n\n"
                "def compute(x: int = 1) -> int:\n"
                "    return x + 1\n\n"
                "async def noop() -> None:\n"
                "    pass\n"
            ),
            "pkg/__init__.py": "",
        },
    )
    assert main(["--base", "main", "--head", head, "--repo", str(repo)]) == 0


def test_cli_fails_on_sneaked_code_change_and_prints_path(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _git(repo, "checkout", "-q", "-b", "sneaky")
    head = _commit(
        repo,
        {
            "pkg/mod.py": (
                "class Empty:\n"
                "    pass\n\n"
                "def compute(x: int = 2) -> int:\n"
                "    return x + 1\n\n"
                "async def noop() -> None:\n"
                "    pass\n"
            ),
        },
    )
    assert main(["--base", "main", "--head", head, "--repo", str(repo)]) == 1
    out = capsys.readouterr().out
    assert "pkg/mod.py" in out


@pytest.mark.parametrize(("base", "head"), [("no-such-revision", "HEAD"), ("", "HEAD"), ("main", " ")])
def test_cli_unreadable_revision_exits_2(repo: Path, capsys: pytest.CaptureFixture[str], base: str, head: str) -> None:
    assert main(["--base", base, "--head", head, "--repo", str(repo)]) == 2
    assert capsys.readouterr().out.startswith("cannot read the pull request diff")


def test_trigger_helpers_and_precedence() -> None:
    assert has_self_doc_label(["bug", "Self-Doc"]) is True
    assert has_self_doc_label(["self_doc"]) is True
    assert has_self_doc_label(["self-explaining-cleanup"]) is True
    assert has_self_doc_label(["enhancement"]) is False

    assert has_self_doc_title("Apply self-doc cleanup") is True
    assert has_self_doc_title("SELF_EXPLAINING pass") is True
    assert has_self_doc_title("Refactor executor") is False

    assert is_self_doc_test_path("apps/sandbox-executor/tests/test_self_doc_slice.py") is True
    assert is_self_doc_test_path("apps/sandbox-executor/tests/self_doc_scanner.py") is True
    assert is_self_doc_test_path("apps/sandbox-executor/tests/test_other.py") is False

    entries = [ChangedEntry("M", "a.py", "a.py")]
    blobs = {("base", "a.py"): '"""doc."""\nx = 1\n', ("head", "a.py"): "x = 1\n"}

    def read_blob(side: str, path: str) -> str:
        return blobs[(side, path)]

    assert is_diff_only_self_doc(entries, read_blob) is True
    assert is_diff_only_self_doc([], read_blob) is False
    assert detect_trigger_reason(["self-doc"], "self-doc title", entries, read_blob) == "label"
    assert detect_trigger_reason([], "self-doc title", entries, read_blob) == "title"
    assert detect_trigger_reason([], "unrelated", entries, read_blob) == "diff-only"

    changed_blobs = {("base", "a.py"): "x = 1\n", ("head", "a.py"): "x = 2\n"}
    assert detect_trigger_reason([], "unrelated", entries, lambda s, p: changed_blobs[(s, p)]) is None


@pytest.mark.parametrize(
    "labels_payload",
    [json.dumps(["self-doc"]), json.dumps([{"name": "self-explaining"}])],
    ids=["string-labels", "object-labels"],
)
def test_cli_label_trigger_runs_check_and_catches_sneaked_change(
    repo: Path, monkeypatch: pytest.MonkeyPatch, labels_payload: str
) -> None:
    _git(repo, "checkout", "-q", "-b", "labelled")
    head = _commit(
        repo,
        {
            "pkg/mod.py": (
                "class Empty:\n"
                "    pass\n\n"
                "def compute(x: int = 1) -> int:\n"
                "    return x + 2\n\n"
                "async def noop() -> None:\n"
                "    pass\n"
            ),
        },
    )
    monkeypatch.setenv("PR_TITLE", "Regular PR title")
    monkeypatch.setenv("PR_LABELS", labels_payload)
    exit_code = main(
        [
            "--base",
            "main",
            "--head",
            head,
            "--repo",
            str(repo),
            "--pr-title-env",
            "PR_TITLE",
            "--pr-labels-env",
            "PR_LABELS",
            "--only-when-triggered",
        ]
    )
    assert exit_code == 1


def test_cli_title_trigger_runs_check_and_catches_sneaked_change(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _git(repo, "checkout", "-q", "-b", "titled")
    head = _commit(repo, {"pkg/__init__.py": "VALUE = 1\n"})
    monkeypatch.setenv("PR_TITLE", "Remove docstrings (Self-Doc)")
    monkeypatch.setenv("PR_LABELS", "[]")
    exit_code = main(
        [
            "--base",
            "main",
            "--head",
            head,
            "--repo",
            str(repo),
            "--pr-title-env",
            "PR_TITLE",
            "--pr-labels-env",
            "PR_LABELS",
            "--only-when-triggered",
        ]
    )
    assert exit_code == 1


def test_cli_diff_only_trigger_passes_for_docstring_removal_and_self_doc_test(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _git(repo, "checkout", "-q", "-b", "diff-only")
    head = _commit(
        repo,
        {
            "pkg/__init__.py": "",
            "apps/sandbox-executor/tests/test_self_doc_slice.py": "def test_ok():\n    assert True\n",
        },
    )
    monkeypatch.setenv("PR_TITLE", "Minor cleanup")
    monkeypatch.setenv("PR_LABELS", "[]")
    exit_code = main(
        [
            "--base",
            "main",
            "--head",
            head,
            "--repo",
            str(repo),
            "--pr-title-env",
            "PR_TITLE",
            "--pr-labels-env",
            "PR_LABELS",
            "--only-when-triggered",
        ]
    )
    assert exit_code == 0
    assert "triggered by: diff-only" in capsys.readouterr().out


@pytest.mark.parametrize(
    "changes",
    [{"pkg/__init__.py": "VALUE = 1\n"}, {"README.md": "hello\n"}],
    ids=["runtime-code", "non-python-file"],
)
def test_cli_skips_non_self_doc_pr(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], changes: dict[str, str]
) -> None:
    _git(repo, "checkout", "-q", "-b", "feature")
    head = _commit(repo, changes)
    monkeypatch.setenv("PR_TITLE", "Add feature")
    monkeypatch.setenv("PR_LABELS", "[]")
    exit_code = main(
        [
            "--base",
            "main",
            "--head",
            head,
            "--repo",
            str(repo),
            "--pr-title-env",
            "PR_TITLE",
            "--pr-labels-env",
            "PR_LABELS",
            "--only-when-triggered",
        ]
    )
    assert exit_code == 0
    assert "skipped" in capsys.readouterr().out


def test_workflow_structure_and_injection_safety() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "\n  pull_request:" not in text
    assert "branches:" not in text
    assert "fetch-depth: 0" in text
    assert "scripts/check_self_doc_behavior.py" in text
    assert "--only-when-triggered" in text
    assert "PR_TITLE: ${{ github.event.pull_request.title }}" in text
    assert "PR_LABELS: ${{ toJSON(github.event.pull_request.labels) }}" in text
    run_lines = [line for line in text.splitlines() if line.lstrip().startswith("run:")]
    assert len(run_lines) == 1
    assert "${{" not in run_lines[0]
