import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from scripts import check_self_explaining
from scripts.self_explaining_comments import find_comments_and_docstrings

STEP_SUMMARY_VARIABLE = "GITHUB_STEP_SUMMARY"

PYTHON_SNIPPET = textwrap.dedent(
    '''\
    def example():
        """Explains the function."""
        return 1  # explains the return
    '''
)

EXISTING_PLAN = textwrap.dedent(
    """\
    # Existing Plan

    ```python
    VALUE = 1  # pre-existing comment
    ```

    Initial prose paragraph.
    """
)


@pytest.fixture(autouse=True)
def without_inherited_step_summary(monkeypatch):
    monkeypatch.delenv(STEP_SUMMARY_VARIABLE, raising=False)


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture(scope="module")
def scratch_repo(tmp_path_factory) -> Path:
    repo = tmp_path_factory.mktemp("self-explaining-markdown")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Self Explaining Test")
    git(repo, "config", "user.email", "self-explaining@example.invalid")
    (repo / "plans").mkdir(parents=True, exist_ok=True)
    (repo / "plans" / "existing.md").write_text(EXISTING_PLAN, encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


@pytest.fixture
def repo(scratch_repo: Path) -> Path:
    git(scratch_repo, "reset", "-q", "--hard", "origin/main")
    git(scratch_repo, "clean", "-qfd")
    return scratch_repo


def write_repo_file(repo: Path, relative_path: str, content: str) -> Path:
    target = repo / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def kinds_and_lines(path: str, text: str) -> list[tuple[str, int]]:
    return [(finding.kind, finding.first_line) for finding in find_comments_and_docstrings(path, text)]


@pytest.mark.parametrize("relative_path", ["plans/P-1.md", "executions/E-1.md"])
def test_fenced_python_docstring_and_comment_in_plan_or_execution_fail(repo, capsys, relative_path):
    content = f"# Record\n\n```python\n{PYTHON_SNIPPET}```\n"
    write_repo_file(repo, relative_path, content)
    assert check_self_explaining.main(["--root", str(repo)]) == 1
    err = capsys.readouterr().err
    assert f"{relative_path}:5: added function docstring" in err
    assert f"{relative_path}:6: added comment" in err


def test_same_python_docstring_in_added_python_file_fails(repo, capsys):
    write_repo_file(repo, "sample.py", PYTHON_SNIPPET)
    assert check_self_explaining.main(["--root", str(repo)]) == 1
    err = capsys.readouterr().err
    assert "sample.py:2: added function docstring" in err
    assert "sample.py:3: added comment" in err


@pytest.mark.parametrize(
    "case",
    [
        ("plans/shell.md", "sh", "echo ok\n# explains shell\n", 5),
        ("executions/shell.md", "bash", "echo ok  # explains bash\n", 4),
        ("plans/shell_alt.md", "shell", "# explains shell alias\necho hi\n", 4),
        ("plans/config.md", "yaml", "key: value  # explains yaml\n", 4),
        ("executions/config.md", "yml", "# explains yml\nkey: value\n", 4),
        ("plans/short_py.md", "py", "VALUE = 1  # explains py\n", 4),
    ],
)
def test_fenced_shell_yaml_and_py_comments_fail(repo, capsys, case):
    relative_path, fence_lang, body, expected_line = case
    content = f"# Record\n\n```{fence_lang}\n{body}```\n"
    write_repo_file(repo, relative_path, content)
    assert check_self_explaining.main(["--root", str(repo)]) == 1
    assert f"{relative_path}:{expected_line}: added comment" in capsys.readouterr().err


@pytest.mark.parametrize("relative_path", ["plans/allowed.md", "executions/allowed.md"])
def test_fenced_reason_comments_directives_and_shebangs_pass(repo, relative_path):
    content = textwrap.dedent(
        """\
        # Allowed Fences

        ```python
        #!/usr/bin/env python3
        VALUE = 1  # why: upstream requires one
        LONG = 2  # noqa: E501
        ```

        ```bash
        #!/usr/bin/env bash
        # shellcheck disable=SC2086
        # why: CI has no TTY
        echo $ARGS
        ```

        ```yaml
        # why: schema version is pinned
        version: 1
        ```
        """
    )
    write_repo_file(repo, relative_path, content)
    assert check_self_explaining.main(["--root", str(repo)]) == 0


def test_markdown_prose_non_code_fences_nested_fences_and_jsonl_ledger_pass(repo):
    plan_text = textwrap.dedent(
        """\
        # Heading One

        ## Heading Two

        Prose paragraph mentioning # hashes and triple quotes.

        ```text
        # plain text comment
        ```

        ```json
        {"note": "# inside json"}
        ```

        ```markdown
        # inner heading
        ```

        ```
        # bare fence line
        ```

        ````markdown
        ```python
        def nested():
            \"\"\"Nested docstring inside outer markdown fence.\"\"\"
            return 1  # nested comment
        ```
        ````
        """
    )
    ledger_row = json.dumps({"entry_type": "action", "note": "# comment in jsonl"}) + "\n"
    write_repo_file(repo, "plans/prose.md", plan_text)
    write_repo_file(repo, "holon-knowledge/ledger/executions.jsonl", ledger_row)
    assert check_self_explaining.main(["--root", str(repo)]) == 0


def test_editing_prose_in_existing_plan_with_untouched_commented_fence_passes(repo):
    existing = repo / "plans" / "existing.md"
    existing.write_text(existing.read_text(encoding="utf-8") + "\nAdded prose line.\n", encoding="utf-8")
    assert check_self_explaining.main(["--root", str(repo)]) == 0


@pytest.mark.parametrize("relative_path", ["README.md", "docs/guide.md"])
def test_markdown_outside_plans_and_executions_stays_exempt(repo, relative_path):
    content = f"# Doc\n\n```python\n{PYTHON_SNIPPET}```\n"
    write_repo_file(repo, relative_path, content)
    assert check_self_explaining.main(["--root", str(repo)]) == 0


def test_tilde_and_indented_list_item_fences_report_exact_lines():
    markdown = textwrap.dedent(
        '''\
        # Plan

        ~~~python
        def tilde():
            """Tilde docstring."""
            return 1  # tilde comment
        ~~~

        - Item:

          ```yaml
          key: value  # indented yaml comment
          ```

          ~~~sh
          #!/usr/bin/env bash
          echo hi  # indented shell comment
          ~~~
        '''
    )
    assert kinds_and_lines("plans/fences.md", markdown) == [
        ("function docstring", 5),
        ("comment", 6),
        ("comment", 12),
        ("comment", 17),
    ]


@pytest.mark.parametrize(
    ("path", "source", "expected"),
    [
        ("mod.py", 'def f():\n    """Doc."""\n    return 1  # c\n', [("function docstring", 2), ("comment", 3)]),
        ("run.sh", "#!/usr/bin/env bash\n# c\necho hi\n", [("comment", 2)]),
        ("cfg.yml", "key: 1  # c\n", [("comment", 1)]),
        ("app.js", "// c\nrun();\n", [("comment", 1)]),
        ("Makefile", "# c\nall:\n\t@echo ok\n", [("comment", 1)]),
        ("Dockerfile", "FROM alpine\n# c\n", [("comment", 2)]),
    ],
)
def test_existing_source_file_classification_is_unchanged(path, source, expected):
    assert kinds_and_lines(path, source) == expected
