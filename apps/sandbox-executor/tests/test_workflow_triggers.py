"""Guard the CI workflow triggers: pull requests into any base branch run the checks, pushes only on ``main``.

When a plan is decomposed, each sub-intent opens its pull request against the plan branch that listed it, not
``main``. A ``pull_request`` trigger restricted to named base branches gives such stacked pull requests no checks at
all, and the Stage 4 review loop then halts with ``test results not measured``. These tests fail if a base-branch filter
(``branches`` or ``branches-ignore``) returns to any ``pull_request`` trigger, and if ``push`` widens beyond ``main``
(which would check every stacked commit twice).

No YAML parser is a project dependency, so the reader below is a small, indentation-aware scan of the top-level ``on:``
block only. It understands the shapes the workflows use: the block mapping of events, block and flow lists of values,
flow mappings of an event's keys (``pull_request: {branches: [main]}``), the flow forms ``on: pull_request`` and
``on: [push, pull_request]``, the quoted ``"on":`` key and the YAML 1.1 ``true:`` alias. Its own behaviour is pinned
by the snippet tests at the end of the module.
"""

import re
from pathlib import Path

import pytest

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
    """Split a scalar or a flow list (``[a, b]``) into its unquoted items."""
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return [item.strip().strip("'\"") for item in value.split(",") if item.strip()]


def _split_top_level(value: str) -> list[str]:
    """Split on commas that are not nested inside ``[...]`` or ``{...}``."""
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
    """Map each key of a flow mapping (``{branches: [main], types: [opened]}``) to its unquoted items."""
    mapping: dict[str, list[str]] = {}
    for entry in _split_top_level(value.strip()[1:-1]):
        key, _, item = entry.partition(":")
        mapping[key.strip().strip("'\"")] = _flow_items(item)
    return mapping


def _event_keys(value: str) -> dict[str, list[str]]:
    """Return an event's keys when its value is given inline as a flow mapping, else none."""
    value = value.strip()
    return _flow_mapping(value) if value.startswith("{") and value.endswith("}") else {}


def _on_block(text: str) -> tuple[str, list[str]]:
    """Return the inline value after the top-level ``on:`` key and the indented lines below it."""
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
    """Map each ``on:`` event to its directly nested keys and their listed values."""
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
    """A moved or renamed workflow directory must not make the guards below pass vacuously."""
    files = _workflow_files()
    assert files, "no workflow files found under .github/workflows"
    assert any(_PULL_REQUEST_EVENTS[0] in _read_triggers(path) for path in files)


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
