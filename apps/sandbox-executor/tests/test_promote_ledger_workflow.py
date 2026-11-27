"""Pin the safety properties of ``.github/workflows/promote-ledger.yml`` on a structural parse of the file.

No YAML parser is a project dependency (see ``test_workflow_triggers.py``), so the ``on:`` block is read with that
module's ``parse_triggers`` and the rest with the small indentation-aware reader below: a block mapping becomes a dict
of scalars, nested lines or ``run: |`` literal text, and a block list becomes a list of such dicts. The reader
understands only the shapes this workflow uses; its behaviour is pinned by the snippet tests at the end. The tests are
read-only.
"""

import re
import textwrap
from pathlib import Path

from scripts.self_explaining_comments import find_comments_and_docstrings
from tests.test_workflow_triggers import parse_triggers

_KEY = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")
_TOKEN = "HOLON_LEDGER_BOT_TOKEN"
_TOKEN_SET = f"env.{_TOKEN} != ''"
_LEDGER = "holon-knowledge/ledger"

Node = str | list[str]

_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / ".github" / "workflows").is_dir())
_WORKFLOW = _ROOT / ".github" / "workflows" / "promote-ledger.yml"
_README = _ROOT / ".github" / "workflows" / "README.md"


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _unquote(value: str) -> str:
    if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _content(lines: list[str]) -> list[str]:
    return [line.rstrip() for line in lines if line.strip() and not line.strip().startswith("#")]


def _value(inline: str, children: list[str]) -> Node:
    if inline == "|":
        return textwrap.dedent("\n".join(children))
    return _unquote(inline) if inline else children


def mapping(lines: list[str]) -> dict[str, Node]:
    """Map each key at the block's own indentation to its scalar, its literal text or its nested lines."""
    body = _content(lines)
    result: dict[str, Node] = {}
    if not body:
        return result
    level = _indent(body[0])
    key, inline, children = "", "", []
    for line in [*body, ""]:
        if line and _indent(line) > level:
            children.append(line)
            continue
        if key:
            result[key] = _value(inline, children)
        match = _KEY.match(line.strip())
        key, inline, children = (match.group(1), match.group(2), []) if match else ("", "", [])
    return result


def items(lines: list[str]) -> list[dict[str, Node]]:
    """Split a block list of mappings (``- name: ...``) into one mapping per item."""
    body = _content(lines)
    groups: list[list[str]] = []
    for line in body:
        if line.lstrip().startswith("- ") and _indent(line) == _indent(body[0]):
            groups.append([])
            line = line.replace("- ", "  ", 1)
        groups[-1].append(line)
    return [mapping(group) for group in groups]


def _child(node: dict[str, Node], key: str) -> dict[str, Node]:
    value = node[key]
    assert isinstance(value, list), f"{key} is not a block mapping"
    return mapping(value)


def _text(node: dict[str, Node], key: str) -> str:
    value = node.get(key, "")
    assert isinstance(value, str), f"{key} is not a scalar"
    return value


def _workflow() -> dict[str, Node]:
    return mapping(_WORKFLOW.read_text(encoding="utf-8").splitlines())


def _job() -> dict[str, Node]:
    return _child(_child(_workflow(), "jobs"), "promote")


def _steps() -> list[dict[str, Node]]:
    steps = _job()["steps"]
    assert isinstance(steps, list)
    return items(steps)


def _step(prefix: str) -> tuple[int, dict[str, Node]]:
    found = [(index, step) for index, step in enumerate(_steps()) if _text(step, "name").startswith(prefix)]
    assert len(found) == 1, f"expected one step named {prefix!r}"
    return found[0]


def test_triggers_are_daily_schedule_and_dispatch_only() -> None:
    triggers = parse_triggers(_WORKFLOW.read_text(encoding="utf-8"))
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert "push" not in triggers
    on = _child(_workflow(), "on")
    schedule = on["schedule"]
    assert isinstance(schedule, list)
    crons = [_text(entry, "cron") for entry in items(schedule)]
    assert len(crons) == 1 and len(crons[0].split()) == 5
    force = _child(_child(_child(on, "workflow_dispatch"), "inputs"), "force")
    assert (force["type"], force["default"]) == ("boolean", "false")


def test_runs_are_serialised_and_the_default_token_is_read_only() -> None:
    workflow = _workflow()
    concurrency = _child(workflow, "concurrency")
    assert _text(concurrency, "group")
    assert concurrency["cancel-in-progress"] == "false"
    assert _child(workflow, "permissions") == {"contents": "read"}


def test_a_missing_token_warns_with_pending_backlog_and_every_later_step_requires_it() -> None:
    assert _child(_job(), "env") == {_TOKEN: f"${{{{ secrets.{_TOKEN} }}}}"}
    steps = _steps()
    sparse_checkout, warning = steps[0], steps[1]
    assert sparse_checkout["if"] == f"env.{_TOKEN} == ''"
    sparse_with = _child(sparse_checkout, "with")
    assert sparse_with["sparse-checkout"] == f"{_LEDGER}/pending"
    assert "token" not in sparse_with
    assert warning["if"] == f"env.{_TOKEN} == ''"
    warning_run = _text(warning, "run")
    assert "::warning::" in warning_run
    assert "::notice::" not in warning_run
    assert f"{_LEDGER}/pending" in warning_run
    assert _TOKEN in warning_run
    assert "exit 1" not in warning_run
    for step in steps[2:]:
        assert _TOKEN_SET in _text(step, "if"), f"{step['name']} runs without the bot token"


def test_the_bots_own_commit_does_not_trigger_another_promotion() -> None:
    assert "push" not in parse_triggers(_WORKFLOW.read_text(encoding="utf-8"))
    message = re.search(r'git commit -m "([^"]+)"', _text(_step("Commit and push")[1], "run"))
    assert message and "[ledger-promote]" in message.group(1)


def test_the_path_guard_runs_before_the_commit_and_only_the_ledger_is_staged() -> None:
    guard_index, guard = _step("Fail on changes outside")
    commit_index, commit = _step("Commit and push")
    assert guard_index < commit_index
    assert f"'^{_LEDGER}/'" in _text(guard, "run") and "exit 1" in _text(guard, "run")
    assert f"steps.{_text(guard, 'id')}.outputs.changed == 'true'" in _text(commit, "if")
    adds = [line.strip() for line in _text(commit, "run").splitlines() if line.strip().startswith("git add")]
    assert adds == [f"git add -A -- {_LEDGER}"]


def test_the_push_is_retried_a_bounded_number_of_times_after_a_fetch_and_rebase() -> None:
    script = _text(_step("Commit and push")[1], "run")
    assert re.search(r"^for attempt in 1 2 3; do$", script, re.MULTILINE)
    commands = ("git push origin HEAD:main", "git fetch origin main", "git rebase origin/main", "git rebase --abort")
    for command in commands:
        assert command in script


def test_secrets_reach_only_the_job_environment_and_the_checkout_token() -> None:
    for step in _steps():
        assert "secrets." not in _text(step, "run")
        assert "secrets." not in str(step.get("env", ""))
    read_only_checkout = _child(_step("Checkout pending ledger directory")[1], "with")
    assert "secrets." not in str(read_only_checkout.get("token", ""))
    checkout = _child(_step("Checkout repository")[1], "with")
    assert checkout["token"] == f"${{{{ secrets.{_TOKEN} }}}}"
    assert checkout["ref"] == "main"


def test_the_readme_describes_the_workflow_and_its_secret() -> None:
    text = _README.read_text(encoding="utf-8")
    assert "- `promote-ledger.yml`: " in text
    workflow_bullet = next(line for line in text.splitlines() if line.startswith("- `promote-ledger.yml`: "))
    assert "pushes to `main`" not in workflow_bullet
    ledger_section = text.partition("\n## Ledger Promotion\n")[2]
    assert _TOKEN in ledger_section
    assert "::warning::" in ledger_section
    assert "no `push` trigger" in ledger_section


def test_workflow_yaml_is_self_explaining() -> None:
    findings = find_comments_and_docstrings(
        ".github/workflows/promote-ledger.yml",
        _WORKFLOW.read_text(encoding="utf-8"),
    )
    assert findings == []


_SNIPPET = """\
top: 'quoted'
job:
  # a comment
  steps:
    - name: One
      run: |
        echo one
          indented
    - name: Two
      with:
        token: x
"""


def test_mapping_reads_scalars_and_nested_blocks() -> None:
    top = mapping(_SNIPPET.splitlines())
    assert top["top"] == "quoted"
    assert set(_child(top, "job")) == {"steps"}


def test_items_split_steps_and_keep_literal_run_blocks() -> None:
    steps = _child(mapping(_SNIPPET.splitlines()), "job")["steps"]
    assert isinstance(steps, list)
    one, two = items(steps)
    assert one == {"name": "One", "run": "echo one\n  indented"}
    assert _child(two, "with") == {"token": "x"}
