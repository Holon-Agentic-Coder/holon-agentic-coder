"""Text guards for the wiring of the bookkeeping-only push gate into the six CI workflows.

No YAML parser is a project dependency, so the workflow text after ``steps:`` is split into step blocks at the lines
that start a list item at the step indentation; ``test_split_steps_reads_names_and_conditions`` pins that splitting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts.ci_bookkeeping_chain import FULL_RUN_STEP
from scripts.ci_bookkeeping_gate import RESULT_AFFECTING_LABELS

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
ACTION_PATH = "./.github/actions/bookkeeping-gate"
ACTION_FILE = REPO_ROOT / ".github" / "actions" / "bookkeeping-gate" / "action.yml"

GATE_STEP = "Bookkeeping gate"
GUARD = "steps.bookkeeping.outputs.skip != 'true'"
SKIP_ONLY = "steps.bookkeeping.outputs.skip == 'true'"
JSON_STEP = "Check JSON and JSONL files (bookkeeping-only push)"
MARKDOWN_STEP = "Check markdown formatting (bookkeeping-only push)"
BEFORE_THE_GATE = ("Record job start time", "Checkout repository")
UNGUARDED = {"CI Timing Gate (Start)", "Upload timing record"}
UNGUARDED_IN = {"test-hygiene.yml": {"Set up Node.js", JSON_STEP, MARKDOWN_STEP}}


@dataclass(frozen=True)
class Wiring:
    """What one workflow must pass to the gate: its job key, the name the jobs API shows and whether it has a matrix."""

    job_key: str
    job_name: str
    matrix: bool


WIRING = {
    "test-unit.yml": Wiring("unit", "unit", True),
    "test-stress.yml": Wiring("workspace-survival", "Workspace survival", True),
    "test-integration.yml": Wiring("integration", "integration", True),
    "test-hygiene.yml": Wiring("hygiene", "hygiene", True),
    "make.yml": Wiring("build", "build", True),
    "docker-build.yml": Wiring("build", "build", False),
}
EACH_WORKFLOW = pytest.mark.parametrize("workflow", sorted(WIRING))


@dataclass(frozen=True)
class Step:
    """One step block of a workflow: its name, the text of its ``if:`` line (empty when it has none) and its text."""

    name: str
    condition: str
    text: str


def split_steps(workflow_text: str) -> list[Step]:
    """Return the step blocks after the one ``steps:`` line of ``workflow_text``."""
    lines = workflow_text.splitlines()
    starts = [index for index, line in enumerate(lines) if line.strip() == "steps:"]
    assert len(starts) == 1, "the splitter reads workflows with exactly one job"
    item = " " * (len(lines[starts[0]]) - len(lines[starts[0]].lstrip()) + 2) + "- "
    blocks: list[list[str]] = []
    for line in lines[starts[0] + 1 :]:
        if line.startswith(item):
            blocks.append([line])
        elif blocks:
            blocks[-1].append(line)
    return [_step(block, item) for block in blocks]


def _step(block: list[str], item: str) -> Step:
    assert block[0].startswith(f"{item}name: "), f"a step does not start with its name: {block[0]!r}"
    key = " " * len(item) + "if: "
    conditions = [line[len(key) :] for line in block if line.startswith(key)]
    assert len(conditions) <= 1, f"a step has several if lines: {block[0]!r}"
    return Step(block[0][len(f"{item}name: ") :], conditions[0] if conditions else "", "\n".join(block))


def workflow_text(workflow: str) -> str:
    return (WORKFLOWS / workflow).read_text(encoding="utf-8")


def steps_of(workflow: str) -> list[Step]:
    return split_steps(workflow_text(workflow))


def gate_index(steps: list[Step]) -> int:
    indexes = [index for index, step in enumerate(steps) if ACTION_PATH in step.text]
    assert len(indexes) == 1, "exactly one step uses the bookkeeping gate action"
    return indexes[0]


SNIPPET = """\
name: Example
jobs:
  example:
    runs-on: ubuntu-latest
    steps:
      - name: First
        uses: actions/checkout@v7
        with:
          submodules: true

      # A comment between two steps belongs to the step above it.
      - name: Second (with brackets)
        if: always() && steps.bookkeeping.outputs.skip != 'true'
        env:
          TEXT: "if: not a condition"
        run: |
          - name: not a step
          if: not a condition either
      - name: Third
        # A comment between the name and the condition.
        if: steps.bookkeeping.outputs.skip == 'true'
        run: make lint-md
"""


def test_split_steps_reads_names_and_conditions() -> None:
    steps = split_steps(SNIPPET)

    assert [(step.name, step.condition) for step in steps] == [
        ("First", ""),
        ("Second (with brackets)", f"always() && {GUARD}"),
        ("Third", SKIP_ONLY),
    ]
    assert "submodules: true" in steps[0].text
    assert "- name: not a step" in steps[1].text
    assert steps[2].text.endswith("run: make lint-md")


def test_split_steps_refuses_a_workflow_with_two_jobs() -> None:
    with pytest.raises(AssertionError, match="exactly one job"):
        split_steps(SNIPPET + "  other:\n    steps:\n      - name: Fourth\n")


def test_every_workflow_that_runs_on_a_pull_request_is_wired() -> None:
    on_pull_request = {path.name for path in WORKFLOWS.glob("*.yml") if "\n  pull_request:" in path.read_text("utf-8")}

    assert on_pull_request == set(WIRING)


@EACH_WORKFLOW
def test_job_keeps_its_key_and_its_name(workflow: str) -> None:
    text = workflow_text(workflow)
    wiring = WIRING[workflow]
    jobs = text.partition("\njobs:\n")[2]

    assert re.findall(r"^  ([\w-]+):$", jobs, flags=re.MULTILINE) == [wiring.job_key]
    shown_names = re.findall(r"^    name: (.+)$", jobs, flags=re.MULTILINE)
    assert shown_names == ([] if wiring.job_name == wiring.job_key else [wiring.job_name])
    assert ("runs-on: ${{ matrix.os }}" in jobs) is wiring.matrix


@EACH_WORKFLOW
def test_gate_is_the_first_step_after_the_checkout(workflow: str) -> None:
    steps = steps_of(workflow)
    index = gate_index(steps)
    gate = steps[index]

    assert gate.name == GATE_STEP
    assert gate.condition == ""
    assert "        id: bookkeeping\n" in gate.text
    assert f"        uses: {ACTION_PATH}\n" in gate.text
    assert steps[index - 1].name == "Checkout repository"
    assert all(step.name in BEFORE_THE_GATE for step in steps[:index])


@EACH_WORKFLOW
def test_gate_is_told_the_job_name_and_the_runner_label(workflow: str) -> None:
    steps = steps_of(workflow)
    gate = steps[gate_index(steps)]
    wiring = WIRING[workflow]

    assert re.findall(r"^          job-name: (.+)$", gate.text, flags=re.MULTILINE) == [wiring.job_name]
    assert ("          os: ${{ matrix.os }}" in gate.text.splitlines()) is wiring.matrix
    assert ("          os:" in gate.text) is wiring.matrix


@EACH_WORKFLOW
def test_every_step_after_the_gate_is_guarded_or_on_the_allow_list(workflow: str) -> None:
    steps = steps_of(workflow)
    allowed = UNGUARDED | UNGUARDED_IN.get(workflow, set())
    later = steps[gate_index(steps) + 1 :]

    unguarded = [step.name for step in later if step.condition not in (GUARD, f"always() && {GUARD}")]

    assert [name for name in unguarded if name not in allowed] == []
    assert len(later) - len(unguarded) >= 3, "the job has guarded steps besides the marker step"


@EACH_WORKFLOW
def test_marker_step_is_the_last_step_and_never_runs_on_a_skip(workflow: str) -> None:
    steps = steps_of(workflow)

    assert [step.name for step in steps].count(FULL_RUN_STEP) == 1
    assert steps[-1].name == FULL_RUN_STEP
    assert steps[-1].condition == GUARD
    assert "always()" not in steps[-1].text
    assert "continue-on-error" not in workflow_text(workflow)


@EACH_WORKFLOW
def test_job_may_read_the_runs_and_jobs_of_the_repository(workflow: str) -> None:
    job = workflow_text(workflow).partition("\njobs:\n")[2].partition("\n    steps:\n")[0]

    assert "\n    permissions:\n      contents: read\n      actions: read\n" in job


def test_hygiene_checks_json_and_markdown_on_a_skip() -> None:
    steps = steps_of("test-hygiene.yml")
    names = [step.name for step in steps]
    by_name = {step.name: step for step in steps}

    assert names.index("Set up Node.js") + 1 == names.index(JSON_STEP) == names.index(MARKDOWN_STEP) - 1
    assert by_name["Set up Node.js"].condition == ""
    assert by_name[JSON_STEP].condition == by_name[MARKDOWN_STEP].condition == SKIP_ONLY
    assert by_name[JSON_STEP].text.rstrip().endswith("        run: make lint-json")
    assert by_name[MARKDOWN_STEP].text.rstrip().endswith("        run: make lint-md")
    assert by_name["Run Makefile lint"].condition == GUARD
    assert "fetch-depth: 0" in by_name["Checkout repository"].text


def test_skip_only_steps_exist_in_the_hygiene_workflow_only() -> None:
    for workflow in WIRING:
        skip_only = [step.name for step in steps_of(workflow) if SKIP_ONLY in step.text]
        assert skip_only == ([JSON_STEP, MARKDOWN_STEP] if workflow == "test-hygiene.yml" else [])


def test_every_label_a_workflow_reads_is_result_affecting() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in sorted(WORKFLOWS.glob("*.yml")))

    labels = set(re.findall(r"labels\.\*\.name,\s*'([^']+)'", text))

    assert len(labels) >= 2, "the scan found the labels the workflows read"
    assert labels <= RESULT_AFFECTING_LABELS
    assert text.count("labels.*.name") == len(re.findall(r"labels\.\*\.name,\s*'[^']+'", text))


def test_action_calls_the_gate_module_and_falls_back_to_a_full_run() -> None:
    text = ACTION_FILE.read_text(encoding="utf-8")
    script = text.partition("      run: |\n")[2]

    assert 'name: "Bookkeeping Gate"' in text
    assert 'using: "composite"' in text
    assert "outputs:\n  skip:\n" in text
    assert "value: ${{ steps.decide.outputs.skip }}" in text
    assert "      id: decide\n" in text
    assert "      shell: bash\n" in text
    assert "      working-directory: ${{ github.workspace }}\n" in text
    assert "        GITHUB_TOKEN: ${{ github.token }}\n" in text
    assert 'python3 -m scripts.ci_bookkeeping_gate --job-name "${GATE_JOB_NAME}" --os "${GATE_JOB_OS}"' in script
    assert "::warning::" in script
    assert 'echo "skip=false" >> "${GITHUB_OUTPUT}"' in script
    assert "skip=true" not in text
    assert "${{" not in script, "inputs reach the script through env, never as pasted text"
