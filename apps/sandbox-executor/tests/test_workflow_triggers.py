import re
from pathlib import Path

import pytest

# why: no YAML parser is a project dependency, so only the top-level on: block is scanned by indentation
_ON_KEY = re.compile(r"""^(?:on|"on"|'on'|true):\s*(.*)$""")
_KEY_LINE = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")
_COMMENT = re.compile(r"(?:^|\s)#.*$")
_PULL_REQUEST_EVENTS = ("pull_request", "pull_request_target")
_BASE_FILTERS = ("branches", "branches-ignore")

Triggers = dict[str, dict[str, list[str]]]


def _workflows_dir() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".github" / "workflows"
        if candidate.is_dir():
            return candidate
    raise AssertionError("no .github/workflows directory above the test module")


def _workflow_files() -> list[Path]:
    workflows = _workflows_dir()
    return sorted([*workflows.glob("*.yml"), *workflows.glob("*.yaml")])


def _flow_items(value: str) -> list[str]:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return [item.strip().strip("'\"") for item in value.split(",") if item.strip()]


def _split_top_level(value: str) -> list[str]:
    parts: list[str] = []
    depth = start = 0
    for index, char in enumerate(value):
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(value[start:index])
            start = index + 1
    parts.append(value[start:])
    return [part.strip() for part in parts if part.strip()]


def _flow_mapping(value: str) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for entry in _split_top_level(value.strip()[1:-1]):
        key, _, item = entry.partition(":")
        mapping[key.strip().strip("'\"")] = _flow_items(item)
    return mapping


def _event_keys(value: str) -> dict[str, list[str]]:
    value = value.strip()
    return _flow_mapping(value) if value.startswith("{") and value.endswith("}") else {}


def _on_block(text: str) -> tuple[str, list[str]]:
    lines = [_COMMENT.sub("", line).rstrip() for line in text.splitlines()]
    for index, line in enumerate(lines):
        match = _ON_KEY.match(line)
        if not match:
            continue
        body: list[str] = []
        for following in lines[index + 1 :]:
            if following and not following[0].isspace():
                break
            if following.strip():
                body.append(following)
        return match.group(1), body
    return "", []


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def parse_triggers(text: str) -> Triggers:
    inline, body = _on_block(text)
    if inline and inline != "{}":
        return {event: {} for event in _flow_items(inline)}
    triggers: Triggers = {}
    event_indent = _indent(body[0]) if body else 0
    event = key = ""
    key_indent = 0
    for line in body:
        stripped = line.strip()
        if _indent(line) == event_indent:
            if stripped.startswith("- "):
                event, triggers[event] = _flow_items(stripped[2:])[0], {}
            else:
                event, _, value = stripped.partition(":")
                triggers[event] = _event_keys(value)
            key, key_indent = "", 0
        elif stripped.startswith("- ") and key:
            triggers[event][key].extend(_flow_items(stripped[2:]))
        elif (match := _KEY_LINE.match(stripped)) and key_indent in (0, _indent(line)):
            key, key_indent = match.group(1), _indent(line)
            triggers[event][key] = _flow_items(match.group(2))
    return triggers


def _read_triggers(path: Path) -> Triggers:
    return parse_triggers(path.read_text(encoding="utf-8"))


def test_workflow_files_are_found() -> None:
    # why: a moved or renamed workflow directory must not make the guards below pass vacuously
    files = _workflow_files()
    assert files, "no workflow files found under .github/workflows"
    assert any(_PULL_REQUEST_EVENTS[0] in _read_triggers(path) for path in files)


# why: stacked sub-intent pull requests target a plan branch; a base filter leaves them unchecked and halts Stage 4
@pytest.mark.parametrize("path", _workflow_files(), ids=lambda path: path.name)
def test_pull_request_trigger_has_no_base_branch_filter(path: Path) -> None:
    triggers = _read_triggers(path)
    for event in _PULL_REQUEST_EVENTS:
        filters = sorted(set(triggers.get(event, {})) & set(_BASE_FILTERS))
        assert not filters, (
            f"{path.name}: '{event}' is restricted by {filters}; stacked sub-intent pull requests target their parent "
            "intent branch and must run the same checks as pull requests into main"
        )


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda path: path.name)
def test_push_trigger_stays_on_main(path: Path) -> None:
    push = _read_triggers(path).get("push", {})
    if "branches" in push:
        assert push["branches"] == ["main"], (
            f"{path.name}: push runs on {push['branches']}; it must stay on main so that a commit on an intent branch "
            "is checked once, by its pull request"
        )


_FILTERED = """\
name: Filtered
on:
  push:
    branches:
      - main
  # a comment between events
  pull_request:
    types: [opened, synchronize]
    branches:
      - main  # trailing comment
  workflow_dispatch:

jobs:
  build:
    steps:
      - name: pull_request
"""

_UNFILTERED = """\
on:
    pull_request:
        types: [opened]
    push:
        branches: [main, 'release']
jobs: {}
"""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            _FILTERED,
            {
                "push": {"branches": ["main"]},
                "pull_request": {"types": ["opened", "synchronize"], "branches": ["main"]},
                "workflow_dispatch": {},
            },
        ),
        (_UNFILTERED, {"pull_request": {"types": ["opened"]}, "push": {"branches": ["main", "release"]}}),
        ("on: pull_request\njobs: {}\n", {"pull_request": {}}),
        ("'on': [push, pull_request]\n", {"push": {}, "pull_request": {}}),
        (
            "true:\n  pull_request_target:\n    branches-ignore: [main]\n",
            {"pull_request_target": {"branches-ignore": ["main"]}},
        ),
        ("on:\n  - push\n  - pull_request\n", {"push": {}, "pull_request": {}}),
        (
            "on:\n  pull_request: {branches: [main, 'release'], types: [opened]}\n  push: {}\n",
            {"pull_request": {"branches": ["main", "release"], "types": ["opened"]}, "push": {}},
        ),
        ("name: none\njobs: {}\n", {}),
    ],
    ids=["filtered", "flow-lists", "scalar", "quoted-flow", "yaml11-alias", "block-list", "flow-mapping", "no-on"],
)
def test_parse_triggers_reads_on_block(text: str, expected: Triggers) -> None:
    assert parse_triggers(text) == expected


def test_the_hygiene_job_checks_module_budgets_against_main_behind_the_maintainer_label():
    # why: the freeze needs git merge-base HEAD origin/main, and a shallow checkout would skip that comparison silently
    text = (_workflows_dir() / "test-hygiene.yml").read_text(encoding="utf-8")
    assert re.search(r"^\s+fetch-depth: 0\s*$", text, re.MULTILINE)
    assert "uv run python scripts/check_module_budgets.py" in text
    assert "contains(github.event.pull_request.labels.*.name, 'module-budget-change')" in text
    assert "export HOLON_ALLOW_MODULE_BUDGET_CHANGE=1" in text


def test_every_hygiene_step_running_the_budget_check_honours_the_maintainer_label():
    # why: make lint runs task lint, which re-runs the budget check, so a step without the label export fails on its own
    text = (_workflows_dir() / "test-hygiene.yml").read_text(encoding="utf-8")
    steps = re.split(r"^\s+- name: ", text, flags=re.MULTILINE)
    checking = [step for step in steps if re.search(r"(make lint|check_module_budgets\.py)\s*$", step, re.MULTILINE)]
    assert len(checking) == 2
    for step in checking:
        assert "contains(github.event.pull_request.labels.*.name, 'module-budget-change')" in step
        assert "HOLON_ALLOW_MODULE_BUDGET_CHANGE" in step
