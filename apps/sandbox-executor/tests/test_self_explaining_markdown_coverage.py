import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from scripts.self_explaining_comments import find_comments_and_docstrings

REPO_ROOT = Path(__file__).resolve().parents[3]
CHECK_SCRIPT = REPO_ROOT / "scripts" / "check_self_explaining.py"
STEP_SUMMARY_VARIABLE = "GITHUB_STEP_SUMMARY"

PYTHON_DOCSTRING_AND_COMMENT = textwrap.dedent(
    '''\
    def sample():
        """Explains sample."""
        return 1  # plain comment
    '''
)


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def write_repo_file(repo: Path, relative_path: str, content: str) -> Path:
    target = repo / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def init_temp_repo(tmp_path: Path, initial_files: dict[str, str] | None = None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Self Explaining Test")
    git(repo, "config", "user.email", "self-explaining@example.invalid")
    write_repo_file(repo, "README.md", "# Repo\n")
    for relative_path, content in (initial_files or {}).items():
        write_repo_file(repo, relative_path, content)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "initial")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


def run_check_script(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {name: value for name, value in os.environ.items() if name != STEP_SUMMARY_VARIABLE}
    return subprocess.run(
        [sys.executable, str(CHECK_SCRIPT), "--root", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def kinds_and_lines(path: str, text: str) -> list[tuple[str, int]]:
    return [(finding.kind, finding.first_line) for finding in find_comments_and_docstrings(path, text)]


def test_plan_markdown_fenced_python_docstring_and_comment_fail(tmp_path: Path):
    repo = init_temp_repo(tmp_path)
    write_repo_file(
        repo,
        "plans/P-sample.md",
        f"# Plan\n\n```python\n{PYTHON_DOCSTRING_AND_COMMENT}```\n",
    )
    completed = run_check_script(repo)
    assert completed.returncode == 1
    assert "plans/P-sample.md:5: added function docstring" in completed.stderr
    assert "plans/P-sample.md:6: added comment" in completed.stderr


def test_execution_markdown_fenced_python_shell_and_yaml_fail(tmp_path: Path):
    repo = init_temp_repo(tmp_path)
    markdown = textwrap.dedent(
        '''\
        # Execution

        ```py
        class Sample:
            """Explains class."""
        ```

        ```bash
        echo hi  # plain bash comment
        ```

        ```sh
        # plain sh comment
        echo ok
        ```

        ```yaml
        key: value  # plain yaml comment
        ```

        ```yml
        # plain yml comment
        other: true
        ```
        '''
    )
    write_repo_file(repo, "executions/E-sample.md", markdown)
    completed = run_check_script(repo)
    assert completed.returncode == 1
    assert "executions/E-sample.md:5: added class docstring" in completed.stderr
    assert "executions/E-sample.md:9: added comment" in completed.stderr
    assert "executions/E-sample.md:13: added comment" in completed.stderr
    assert "executions/E-sample.md:18: added comment" in completed.stderr
    assert "executions/E-sample.md:22: added comment" in completed.stderr


def test_fenced_reason_comments_directives_and_shebang_pass(tmp_path: Path):
    repo = init_temp_repo(tmp_path)
    markdown = textwrap.dedent(
        """\
        # Plan

        ```python
        # why: upstream rejects zero
        VALUE = 1  # noqa: E501
        ```

        ```bash
        #!/usr/bin/env bash
        # shellcheck disable=SC2086
        # why: CI has no TTY
        echo $ARGS
        ```

        ```yaml
        # why: required by schema
        enabled: true
        ```
        """
    )
    write_repo_file(repo, "plans/P-allowed.md", markdown)
    completed = run_check_script(repo)
    assert completed.returncode == 0


def test_markdown_prose_non_code_fences_and_jsonl_ledger_row_pass(tmp_path: Path):
    repo = init_temp_repo(tmp_path)
    plan_prose = textwrap.dedent(
        '''\
        # Heading With Prose

        Paragraph with `inline # backticks` and explanation.

        ```json
        {"note": "# not a comment"}
        ```

        ````markdown
        ```python
        def example():
            """Nested example inside markdown fence."""
            return 1  # nested comment
        ```
        ````
        '''
    )
    execution_prose = "# Execution Notes\n\nPlain prose paragraph with # hash.\n"
    ledger_row = '{"entry_type":"action","note":"# not code","docstring":"\\"\\"\\"text\\"\\"\\""}\n'
    write_repo_file(repo, "plans/P-prose.md", plan_prose)
    write_repo_file(repo, "executions/E-prose.md", execution_prose)
    write_repo_file(repo, "holon-knowledge/ledger/executions.jsonl", ledger_row)
    completed = run_check_script(repo)
    assert completed.returncode == 0


def test_same_docstring_in_added_python_file_fails(tmp_path: Path):
    repo = init_temp_repo(tmp_path)
    write_repo_file(repo, "sample.py", PYTHON_DOCSTRING_AND_COMMENT)
    completed = run_check_script(repo)
    assert completed.returncode == 1
    assert "sample.py:2: added function docstring" in completed.stderr
    assert "sample.py:3: added comment" in completed.stderr


def test_editing_only_prose_in_committed_plan_with_fenced_comment_passes(tmp_path: Path):
    committed_plan = textwrap.dedent(
        """\
        # Plan

        Original prose.

        ```python
        VALUE = 1  # pre-existing comment
        ```
        """
    )
    repo = init_temp_repo(tmp_path, {"plans/P-existing.md": committed_plan})
    write_repo_file(repo, "plans/P-existing.md", committed_plan.replace("Original prose.", "Updated prose."))
    completed = run_check_script(repo)
    assert completed.returncode == 0


@pytest.mark.parametrize("relative_path", ["README.md", "docs/guide.md"])
def test_markdown_outside_plans_and_executions_stays_exempt(tmp_path: Path, relative_path: str):
    repo = init_temp_repo(tmp_path)
    write_repo_file(
        repo,
        relative_path,
        f"# Docs\n\n```python\n{PYTHON_DOCSTRING_AND_COMMENT}```\n",
    )
    completed = run_check_script(repo)
    assert completed.returncode == 0


def test_indented_fenced_block_in_list_reports_exact_markdown_lines():
    markdown = textwrap.dedent(
        '''\
        - Step 1:
          ```python
          def indented():
              """Indented docstring."""
              return 1  # indented comment
          ```
        '''
    )
    assert kinds_and_lines("plans/P-indented.md", markdown) == [("function docstring", 4), ("comment", 5)]


def test_tilde_fence_is_scanned():
    markdown = textwrap.dedent(
        '''\
        # Plan

        ~~~python
        def tilde_fn():
            """Tilde docstring."""
            return 1  # tilde comment
        ~~~
        '''
    )
    assert kinds_and_lines("executions/E-tilde.md", markdown) == [("function docstring", 5), ("comment", 6)]


def test_nested_four_backtick_markdown_fence_ignores_inner_code_fence():
    markdown = textwrap.dedent(
        '''\
        ````markdown
        ```python
        def nested():
            """Nested."""
            return 1  # nested
        ```
        ````
        '''
    )
    assert find_comments_and_docstrings("plans/P-nested.md", markdown) == []


@pytest.mark.parametrize(
    ("path", "source", "expected"),
    [
        (
            "module.py",
            'def run():\n    """Doc."""\n    return 1  # comment\n',
            [("function docstring", 2), ("comment", 3)],
        ),
        ("deploy.sh", "#!/usr/bin/env bash\n# shell comment\necho ok\n", [("comment", 2)]),
        ("config.yml", "key: value  # yaml comment\n", [("comment", 1)]),
        ("holon-knowledge/ledger/executions.jsonl", '{"comment":"# ignored"}\n', []),
    ],
)
def test_source_and_jsonl_classification_is_unchanged(path: str, source: str, expected: list[tuple[str, int]]):
    assert kinds_and_lines(path, source) == expected
