import json
import os
import re
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

from scripts import check_self_explaining, pre_commit_check, self_explaining_diff
from scripts.self_explaining_comments import find_comments_and_docstrings
from scripts.self_explaining_diff import added_line_numbers, removed_line_numbers

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
CHECK_SCRIPT = REPO_ROOT / "scripts" / "check_self_explaining.py"
STEP_SUMMARY_VARIABLE = "GITHUB_STEP_SUMMARY"


@pytest.fixture(autouse=True)
def without_inherited_step_summary(monkeypatch):
    monkeypatch.delenv(STEP_SUMMARY_VARIABLE, raising=False)


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
        "VALUES = []  # type: list[int]\n",
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
        ("deploy.sh", "echo $(( y << shift ))\n(( x <<= bits ))\necho hi # explains\n", [3]),
        ("Makefile", "all:\n\techo $$((y << shift)) <<EOF\n# explains\n", [3]),
        ("ci.yml", 'msg: 5" screen # explains\nkey: "a # b"\n', [1]),
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


@pytest.mark.parametrize(
    ("diff_text", "expected"),
    [
        ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -3,2 +3 @@\n-x\n-y\n+z\n", {"a.py": {3, 4}}),
        ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -5 +4,0 @@\n-x\n", {"a.py": {5}}),
        ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -2,0 +3 @@\n+x\n", {"a.py": set()}),
        (
            "diff --git a/gone.sh b/gone.sh\ndeleted file mode 100644\n--- a/gone.sh\n+++ /dev/null\n"
            "@@ -1,3 +0,0 @@\n-a\n-b\n-c\n",
            {"gone.sh": {1, 2, 3}},
        ),
        (
            "diff --git a/old.py b/new.py\nsimilarity index 90%\nrename from old.py\nrename to new.py\n"
            "--- a/old.py\n+++ b/new.py\n@@ -2 +2 @@\n-x\n+y\n",
            {"old.py": {2}},
        ),
        ("diff --git a/b.sh b/b.sh\nnew file mode 100644\n--- /dev/null\n+++ b/b.sh\n@@ -0,0 +1,2 @@\n+a\n+b\n", {}),
        ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n---- a/fake\n+x\n", {"a.py": {1}}),
    ],
)
def test_removed_line_numbers_reads_old_side_hunks(diff_text, expected):
    assert {path: set(lines) for path, lines in removed_line_numbers(diff_text).items()} == expected


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


def git_stdout(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def committed_repo(repo: Path, files: dict[str, str]) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-q", "-b", "main")
    for name, text in files.items():
        (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "initial")
    return repo


def test_missing_merge_base_without_remote_fails(tmp_path, capsys):
    committed_repo(tmp_path, {"deploy.sh": BASE_SHELL})
    assert check_self_explaining.main(["--root", str(tmp_path)]) != 0
    captured = capsys.readouterr()
    assert "origin/main" in captured.err
    assert "passed" not in captured.out
    assert not hasattr(check_self_explaining, "NO_MERGE_BASE_NOTICE")


def test_unrelated_origin_history_fails_after_fetch(tmp_path, capsys):
    upstream = committed_repo(tmp_path / "upstream", {"module.py": BASE_PYTHON})
    git(tmp_path, "clone", "-q", "--bare", str(upstream), str(tmp_path / "remote.git"))
    local = committed_repo(tmp_path / "local", {"deploy.sh": BASE_SHELL})
    git(local, "remote", "add", "origin", str(tmp_path / "remote.git"))
    assert check_self_explaining.main(["--root", str(local)]) != 0
    assert "origin/main" in capsys.readouterr().err
    assert git_stdout(local, "rev-parse", "refs/remotes/origin/main") == git_stdout(upstream, "rev-parse", "HEAD")


def shallow_feature_clone(tmp_path: Path) -> tuple[Path, str]:
    work = committed_repo(tmp_path / "work", {"deploy.sh": BASE_SHELL})
    base = git_stdout(work, "rev-parse", "HEAD")
    git(work, "checkout", "-q", "-b", "feature")
    append(work / "deploy.sh", "echo feature\n")
    git(work, "commit", "-q", "-am", "feature")
    git(work, "checkout", "-q", "main")
    append(work / "deploy.sh", "echo main\n")
    git(work, "commit", "-q", "-am", "later main")
    bare = tmp_path / "remote.git"
    git(tmp_path, "clone", "-q", "--bare", str(work), str(bare))
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "--depth", "1", "--single-branch", "--branch", "feature", bare.as_uri(), str(clone))
    git(clone, "config", "user.name", "Self Explaining Test")
    git(clone, "config", "user.email", "self-explaining@example.invalid")
    return clone, base


def test_merge_base_deepens_shallow_clone(tmp_path):
    clone, base = shallow_feature_clone(tmp_path)
    assert git_stdout(clone, "rev-parse", "--is-shallow-repository") == "true"
    assert self_explaining_diff.existing_merge_base(clone) is None
    assert self_explaining_diff.merge_base(clone) == base
    assert git_stdout(clone, "rev-parse", "--is-shallow-repository") == "false"


def test_shallow_clone_check_runs_after_deepening(tmp_path, capsys):
    clone, _ = shallow_feature_clone(tmp_path)
    append(clone / "deploy.sh", "# added comment\n")
    assert check_self_explaining.main(["--root", str(clone)]) == 1
    assert "deploy.sh:5: added comment" in capsys.readouterr().err


def test_violation_report_counts_per_file_and_total(repo, capsys):
    append(repo / "module.py", "OTHER = 2  # first added comment\n# second added comment\n")
    append(repo / "deploy.sh", "# added shell comment\n")
    assert check_self_explaining.main(["--root", str(repo)]) == 1
    err_lines = capsys.readouterr().err.splitlines()
    module_count = err_lines.index("module.py: 2 added docstring(s)/comment(s)")
    shell_count = err_lines.index("deploy.sh: 1 added docstring(s)/comment(s)")
    violation_lines = [index for index, line in enumerate(err_lines) if ": added comment (" in line]
    assert len(violation_lines) == 3
    assert max(violation_lines) < min(module_count, shell_count)
    assert err_lines[-1] == "3 added docstring(s) or comment(s) found."


def test_staged_mode_passes_without_merge_base(tmp_path, capsys):
    committed_repo(tmp_path, {"deploy.sh": BASE_SHELL})
    append(tmp_path / "deploy.sh", "echo staged\n")
    git(tmp_path, "add", "deploy.sh")
    assert check_self_explaining.main(["--root", str(tmp_path), "--staged"]) == 0
    assert "passed (1 changed file(s))" in capsys.readouterr().out


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


def run_check_script(repo: Path, env_overrides: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    inherited = {name: value for name, value in os.environ.items() if name != STEP_SUMMARY_VARIABLE}
    return subprocess.run(
        [sys.executable, str(CHECK_SCRIPT), "--root", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        env={**inherited, **env_overrides},
    )


DOCSTRING_AND_COMMENT_ADDITION = '\n\ndef added():\n    """Added docstring."""\n    return 1  # explains\n'


def test_step_summary_lists_counts_on_failure(repo, tmp_path):
    summary = tmp_path / "summary.md"
    append(repo / "module.py", DOCSTRING_AND_COMMENT_ADDITION)
    completed = run_check_script(repo, {STEP_SUMMARY_VARIABLE: str(summary)})
    assert completed.returncode == 1
    text = summary.read_text(encoding="utf-8")
    assert "Status: failed" in text
    assert "| Changed files checked | 1 |" in text
    assert "| Added docstrings | 1 |" in text
    assert "| Added comments (not '# why:') | 1 |" in text


def test_step_summary_lists_counts_on_pass(repo, tmp_path):
    summary = tmp_path / "summary.md"
    append(repo / "module.py", "VALUE_TWO = 2\n")
    completed = run_check_script(repo, {STEP_SUMMARY_VARIABLE: str(summary)})
    assert completed.returncode == 0
    text = summary.read_text(encoding="utf-8")
    assert "Status: passed" in text
    assert "| Changed files checked | 1 |" in text
    assert "| Added docstrings | 0 |" in text
    assert "| Added comments (not '# why:') | 0 |" in text


def test_no_step_summary_without_the_variable(repo, tmp_path):
    before = set(tmp_path.rglob("*"))
    append(repo / "module.py", DOCSTRING_AND_COMMENT_ADDITION)
    assert run_check_script(repo, {}).returncode == 1
    git(repo, "checkout", "-q", "--", "module.py")
    append(repo / "module.py", "VALUE_TWO = 2\n")
    assert run_check_script(repo, {}).returncode == 0
    assert not (tmp_path / "summary.md").exists()
    assert set(tmp_path.rglob("*")) == before


def test_unwritable_step_summary_keeps_the_exit_code(repo, tmp_path):
    append(repo / "module.py", "VALUE_TWO = 2\n")
    completed = run_check_script(repo, {STEP_SUMMARY_VARIABLE: str(tmp_path / "missing" / "summary.md")})
    assert completed.returncode == 0
    assert "could not append the step summary" in completed.stderr


def test_step_summary_counts_removed_docstrings_and_comments(repo, tmp_path):
    summary = tmp_path / "summary.md"
    module = repo / "module.py"
    text = module.read_text(encoding="utf-8")
    module.write_text(
        text.replace("# Existing comment.\n", "").replace('    """Existing function docstring."""\n', ""), "utf-8"
    )
    completed = run_check_script(repo, {STEP_SUMMARY_VARIABLE: str(summary)})
    assert completed.returncode == 0
    summary_text = summary.read_text(encoding="utf-8")
    assert "| Removed docstrings | 1 |" in summary_text
    assert "| Removed comments (not '# why:') | 1 |" in summary_text
    assert "| Added docstrings | 0 |" in summary_text


def test_counts_json_compares_head_with_merge_base(tmp_path):
    work = committed_repo(tmp_path / "work", {"module.py": BASE_PYTHON})
    git(work, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(work, "checkout", "-q", "-b", "feature")
    module = work / "module.py"
    module.write_text(module.read_text(encoding="utf-8").replace("# Existing comment.\n", ""), "utf-8")
    append(module, '\n\ndef added():\n    """Added docstring."""\n')
    git(work, "commit", "-q", "-am", "feature")
    git(work, "checkout", "-q", "main")
    append(module, "OTHER = 2  # working tree comment\n")
    completed = run_check_script(work, {}, "--head", "feature", "--counts-json")
    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {
        "files_checked": 1,
        "added_docstrings": 1,
        "added_comments": 0,
        "removed_docstrings": 0,
        "removed_comments": 1,
    }


def test_staged_mode_counts_removed_comment(repo, tmp_path):
    summary = tmp_path / "summary.md"
    module = repo / "module.py"
    module.write_text(module.read_text(encoding="utf-8").replace("# Existing comment.\n", ""), "utf-8")
    git(repo, "add", "module.py")
    completed = run_check_script(repo, {STEP_SUMMARY_VARIABLE: str(summary)}, "--staged")
    assert completed.returncode == 0
    assert "| Removed comments (not '# why:') | 1 |" in summary.read_text(encoding="utf-8")


def test_staged_mode_rejects_head(repo):
    completed = run_check_script(repo, {}, "--staged", "--head", "main")
    assert completed.returncode == 2
    assert "cannot be combined with --head" in completed.stderr
