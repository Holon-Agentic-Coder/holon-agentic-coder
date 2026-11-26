import re
import subprocess
import textwrap
import tomllib
from pathlib import Path

import pytest

from scripts import check_self_explaining, pre_commit_check
from scripts.self_explaining_comments import find_comments_and_docstrings
from scripts.self_explaining_diff import added_line_numbers

REPO_ROOT = Path(__file__).resolve().parents[3]

BASE_PYTHON = textwrap.dedent(
    '''\
    """Existing module docstring."""

    # Existing comment.
    VALUE = 1


    def existing():
        """Existing function docstring."""
        return VALUE
    '''
)
BASE_SHELL = "#!/usr/bin/env bash\n# Existing shell comment.\necho hello\n"
BASE_YAML = "# Existing YAML comment.\nname: base\n"


def kinds_and_lines(path: str, text: str) -> list[tuple[str, int]]:
    return [(finding.kind, finding.first_line) for finding in find_comments_and_docstrings(path, text)]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('"""Module."""\nVALUE = 1\n', [("module docstring", 1)]),
        ('class Thing:\n    """Class."""\n', [("class docstring", 2)]),
        ('def run():\n    """Function."""\n', [("function docstring", 2)]),
        ('async def run():\n    """Async\n    function."""\n', [("function docstring", 2)]),
        ("VALUE = 1  # explains the value\n", [("comment", 1)]),
        ("# a full-line comment\nVALUE = 1\n", [("comment", 1)]),
    ],
)
def test_python_docstrings_and_comments_are_reported(source, expected):
    assert kinds_and_lines("module.py", source) == expected


def test_docstring_span_covers_every_line():
    source = 'def run():\n    """First line.\n\n    Last line.\n    """\n'
    [finding] = find_comments_and_docstrings("module.py", source)
    assert (finding.first_line, finding.last_line) == (2, 5)


@pytest.mark.parametrize(
    "source",
    [
        "#!/usr/bin/env python3\nVALUE = 1\n",
        "# -*- coding: utf-8 -*-\nVALUE = 1\n",
        "#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\nVALUE = 1\n",
        "VALUE = 1  # why: the upstream API rejects zero\n",
        "VALUE = 1  # noqa\n",
        "VALUE = 1  # noqa: E501\n",
        "VALUE: int = None  # type: ignore[assignment]\n",
        "if VALUE:  # pragma: no cover\n    pass\n",
        "# fmt: off\nVALUE = 1\n# fmt: on\n",
        "# shellcheck disable=SC2086\nVALUE = 1\n",
        "HASH = '#'\nTEXT = '# not a comment'\nOTHER = \"#\"\n",
        'TEXT = """\n# inside a string\n"""\n',
    ],
)
def test_python_allowed_comments_and_strings_are_not_reported(source):
    assert find_comments_and_docstrings("module.py", source) == []


@pytest.mark.parametrize(
    ("path", "source", "expected_lines"),
    [
        ("deploy.sh", "#!/usr/bin/env bash\n# explains\necho \"#\" '#' $# ${#items[@]}\n", [2]),
        ("run", "#!/bin/sh\necho hi  # explains\n", [2]),
        ("ci.yml", "# explains\nkey: \"a # b\"\nurl: page.html#anchor\nother: 'c # d'  # trailing\n", [1, 4]),
        ("action.yaml", "steps:\n  - run: echo hi # explains\n", [2]),
        ("Makefile", '# explains\nlint:\n\t@echo "#" \\# $(VAR)\n', [1]),
        ("rules.mk", "VAR = 1 # explains\n", [1]),
        ("Dockerfile", "# explains\nRUN echo '#'\n", [1]),
        ("Dockerfile.sandbox", "FROM base\n# explains\n", [2]),
        ("pyproject.toml", '[tool]\nkey = "#value" # explains\n', [2]),
        ("deploy.sh", "#!/usr/bin/env bash\n# shellcheck disable=SC2086\n# why: CI has no TTY\necho hi\n", []),
        ("deploy.sh", "cat <<EOF > notes.md\n# Heading\nEOF\necho hi # explains\n", [4]),
        ("deploy.sh", "cat <<-'EOF'\n\t# Heading\n\tEOF\n# explains\n", [4]),
        ("deploy.sh", 'read -r x <<< "$y" # explains\necho $((1<<2)) # explains\n', [1, 2]),
        ("ci.yml", "name: don't panic # explains\nother: it's 'x # y'\n", [1]),
        ("ci.yml", "body: |\n  # Heading\n  text # not a comment\nnext: 1 # explains\n", [4]),
        (
            "ci.yml",
            "steps:\n  - body: >-\n      # Heading\n\n      more\n"
            "  - run: |\n      # explains\n      cat <<EOF\n      # Heading\n      EOF\n",
            [7],
        ),
        (
            "pyproject.toml",
            "text = \"\"\"\n# inside\n\"\"\" # explains\nraw = '''\n# inside\n'''\nkey=1# explains\n",
            [3, 7],
        ),
    ],
)
def test_hash_language_comments_are_reported_outside_quotes(path, source, expected_lines):
    assert [line for _, line in kinds_and_lines(path, source)] == expected_lines


@pytest.mark.parametrize(
    "source",
    [
        "VALUE = 1  # pragmatically chosen\n",
        "VALUE = 1  # pragmatic: chosen\n",
        "VALUE = 1  # Type: explains\n",
        "VALUE = 1  # NOQA explains\n",
        "VALUE = 1  # noqarecord explains\n",
        "VALUE = 1  # fmt: the value\n",
        "VALUE = 1  # shellchecked by hand\n",
    ],
)
def test_explanatory_comments_resembling_directives_are_reported(source):
    assert kinds_and_lines("module.py", source) == [("comment", 1)]


def test_javascript_comments_are_reported_and_strings_ignored():
    source = 'const url = "//not-a-comment";\n// explains\n/**\n * JSDoc.\n */\nfunction run() {}\nrun(); /* x */\n'
    assert kinds_and_lines("app.js", source) == [("comment", 2), ("JSDoc comment", 3), ("block comment", 7)]
    spans = [(finding.first_line, finding.last_line) for finding in find_comments_and_docstrings("app.mjs", source)]
    assert spans == [(2, 2), (3, 5), (7, 7)]


def test_javascript_reason_comment_is_allowed():
    assert find_comments_and_docstrings("app.cjs", "// why: the browser caches this\nrun();\n") == []


@pytest.mark.parametrize("path", ["README.md", "data.json", "ledger.jsonl", "notes.txt", "run"])
def test_unscanned_files_report_nothing(path):
    assert find_comments_and_docstrings(path, "# heading\n// text\n") == []


@pytest.mark.parametrize(
    ("diff_text", "expected"),
    [
        ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -3 +3 @@\n-x\n+y\n", {"a.py": {3}}),
        ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -2,0 +3,2 @@\n+x\n+y\n", {"a.py": {3, 4}}),
        ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -3,2 +2,0 @@\n-x\n-y\n", {"a.py": set()}),
        ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n++++ b/fake\n", {"a.py": {1}}),
        (
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n-x\n+y\n+z\n"
            "diff --git a/b.sh b/b.sh\nnew file mode 100644\n--- /dev/null\n+++ b/b.sh\n@@ -0,0 +1,3 @@\n+a\n+b\n+c\n"
            "@@ -9,0 +10 @@\n+d\n",
            {"a.py": {1, 2}, "b.sh": {1, 2, 3, 10}},
        ),
    ],
)
def test_added_line_numbers_reads_unified_zero_context_hunks(diff_text, expected):
    assert {path: set(lines) for path, lines in added_line_numbers(diff_text).items()} == expected


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture(scope="module")
def scratch_repo(tmp_path_factory) -> Path:
    repo = tmp_path_factory.mktemp("self-explaining")
    (repo / "module.py").write_text(BASE_PYTHON, encoding="utf-8")
    (repo / "deploy.sh").write_text(BASE_SHELL, encoding="utf-8")
    (repo / "config.yml").write_text(BASE_YAML, encoding="utf-8")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


@pytest.fixture
def repo(scratch_repo: Path) -> Path:
    git(scratch_repo, "reset", "-q", "--hard", "origin/main")
    git(scratch_repo, "clean", "-qfd")
    return scratch_repo


def append(path: Path, text: str) -> None:
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def test_unchanged_docstrings_and_comments_pass(repo, capsys):
    append(repo / "module.py", "\n\nOTHER = VALUE + 1\n")
    (repo / "module.py").write_text(
        (repo / "module.py").read_text(encoding="utf-8").replace("VALUE = 1", "VALUE = 2"), encoding="utf-8"
    )
    append(repo / "deploy.sh", "echo bye\n")
    append(repo / "config.yml", "extra: true\n")
    assert check_self_explaining.main(["--root", str(repo)]) == 0
    assert "passed (3 changed file(s))" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("path", "addition", "expected"),
    [
        ("module.py", '\n\ndef added():\n    """Added docstring."""\n', "module.py:13: added function docstring"),
        ("module.py", "\n\nclass Added:\n    '''Added.'''\n", "module.py:13: added class docstring"),
        ("module.py", "OTHER = 2  # added comment\n", "module.py:10: added comment"),
        ("deploy.sh", "# added shell comment\n", "deploy.sh:4: added comment"),
        ("config.yml", "key: value # added YAML comment\n", "config.yml:3: added comment"),
    ],
)
def test_added_docstring_or_comment_fails(repo, capsys, path, addition, expected):
    append(repo / path, addition)
    assert check_self_explaining.main(["--root", str(repo)]) == 1
    assert expected in capsys.readouterr().err


def test_editing_an_existing_docstring_fails(repo, capsys):
    module = repo / "module.py"
    module.write_text(module.read_text(encoding="utf-8").replace("Existing function", "Edited function"), "utf-8")
    assert check_self_explaining.main(["--root", str(repo)]) == 1
    assert "module.py:8: added function docstring" in capsys.readouterr().err


def test_new_untracked_files_are_checked_whole(repo, capsys):
    (repo / "Makefile").write_text("# added make comment\nall:\n\t@echo done\n", encoding="utf-8")
    (repo / "fresh.py").write_text('"""Added module docstring."""\n', encoding="utf-8")
    assert check_self_explaining.main(["--root", str(repo)]) == 1
    err = capsys.readouterr().err
    assert "Makefile:1: added comment" in err
    assert "fresh.py:1: added module docstring" in err


def test_reason_comments_and_directives_pass(repo):
    append(repo / "module.py", "OTHER = 2  # why: the vendor rejects 1\nLONG = 3  # noqa: E501\n")
    append(repo / "deploy.sh", "# shellcheck disable=SC2086\necho $ARGS\n")
    assert check_self_explaining.main(["--root", str(repo)]) == 0


def test_staged_mode_checks_only_the_index(repo, capsys):
    append(repo / "deploy.sh", "# staged comment\n")
    git(repo, "add", "deploy.sh")
    append(repo / "module.py", "OTHER = 2  # unstaged comment\n")
    assert check_self_explaining.main(["--root", str(repo), "--staged"]) == 1
    err = capsys.readouterr().err
    assert "deploy.sh:4: added comment" in err
    assert "module.py" not in err


def test_staged_mode_ignores_unstaged_comments(repo):
    append(repo / "module.py", "OTHER = 2  # unstaged comment\n")
    assert check_self_explaining.main(["--root", str(repo), "--staged"]) == 0


def test_missing_merge_base_skips_with_notice(tmp_path, capsys):
    git(tmp_path, "init", "-q")
    assert check_self_explaining.main(["--root", str(tmp_path)]) == 0
    assert check_self_explaining.NO_MERGE_BASE_NOTICE in capsys.readouterr().out


def test_lint_task_runs_the_check():
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "python scripts/check_self_explaining.py" in pyproject["tool"]["taskipy"]["tasks"]["lint"]


def test_hygiene_workflow_has_a_named_step_for_the_check():
    workflow = (REPO_ROOT / ".github" / "workflows" / "test-hygiene.yml").read_text(encoding="utf-8")
    step = re.search(r"- name: Check self-explaining code\n(?:\s{8}\S.*\n)+", workflow)
    assert step is not None
    assert "run: uv run python scripts/check_self_explaining.py" in step.group()


@pytest.mark.parametrize(
    ("argv", "expected_calls"),
    [(["pre_commit_check.py"], [["--staged"]]), (["pre_commit_check.py", "--all"], []), (["x", "a.py"], [])],
)
def test_pre_commit_runs_the_check_only_in_staged_mode(monkeypatch, argv, expected_calls):
    calls = []
    monkeypatch.setattr("sys.argv", argv)
    monkeypatch.setattr(pre_commit_check, "get_git_files", lambda mode: [])
    monkeypatch.setattr(pre_commit_check, "run_checks", lambda files: 0)
    monkeypatch.setattr(pre_commit_check, "check_self_explaining", lambda args: calls.append(args) or 1)
    with pytest.raises(SystemExit) as exit_info:
        pre_commit_check.main()
    assert calls == expected_calls
    assert exit_info.value.code == (1 if expected_calls else 0)
