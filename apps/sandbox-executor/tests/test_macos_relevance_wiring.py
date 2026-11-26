"""Text guards for the wiring of the macOS relevance gate into the unit workflow, its action and its README section.

The job must keep its key, its matrix and its check name ``unit (macos-latest)`` on every head, so only step-level
guards may change; the step splitting is the one ``test_ci_bookkeeping_wiring`` pins.
"""

from __future__ import annotations

import re

from scripts.ci_bookkeeping_chain import FULL_RUN_STEP
from scripts.macos_relevance import FORCE_LABEL, RUN_RULES, SKIP_RULES
from scripts.macos_relevance_rules import CONTENT_PATTERNS
from tests.test_ci_bookkeeping_wiring import (
    GATE_STEP,
    GUARD,
    README,
    RELEVANCE_GATE_STEP,
    RELEVANCE_GUARD,
    REPO_ROOT,
    steps_of,
    workflow_text,
)

WORKFLOW = "test-unit.yml"
ACTION_PATH = "./.github/actions/macos-relevance-gate"
ACTION_FILE = REPO_ROOT / ".github" / "actions" / "macos-relevance-gate" / "action.yml"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
GATE_CONDITION = f"matrix.os == 'macos-latest' && {GUARD}"
EXPENSIVE_STEPS = ("Install uv", "Run unit tests", "CI Timing Gate (compare with main)", FULL_RUN_STEP)


def test_job_has_no_condition_and_keeps_both_runners() -> None:
    job = workflow_text(WORKFLOW).partition("\njobs:\n")[2].partition("\n    steps:\n")[0]

    assert not re.search(r"^    if:", job, flags=re.MULTILINE)
    assert "        os: [ubuntu-latest, macos-latest]\n" in job


def test_relevance_gate_follows_the_bookkeeping_gate_on_the_macos_leg_only() -> None:
    steps = steps_of(WORKFLOW)
    names = [step.name for step in steps]
    gate = steps[names.index(RELEVANCE_GATE_STEP)]

    assert names.index(RELEVANCE_GATE_STEP) == names.index(GATE_STEP) + 1
    assert gate.condition == GATE_CONDITION
    assert "        id: macos_relevance\n" in gate.text
    assert f"        uses: {ACTION_PATH}\n" in gate.text
    assert [step.name for step in steps if ACTION_PATH in step.text] == [RELEVANCE_GATE_STEP]


def test_expensive_steps_carry_the_combined_guard() -> None:
    by_name = {step.name: step for step in steps_of(WORKFLOW)}

    assert [by_name[name].condition for name in EXPENSIVE_STEPS] == [RELEVANCE_GUARD] * len(EXPENSIVE_STEPS)


def test_no_guard_compares_with_false() -> None:
    # On ubuntu the relevance gate never runs and its output is empty, so only "!= 'true'" leaves that leg unchanged.
    assert "== 'false'" not in workflow_text(WORKFLOW)


def test_timing_record_is_uploaded_on_pushes_to_main_only() -> None:
    by_name = {step.name: step for step in steps_of(WORKFLOW)}

    assert by_name["Upload timing record"].condition == (
        "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    )


def test_action_calls_the_gate_module_and_falls_back_to_a_run() -> None:
    text = ACTION_FILE.read_text(encoding="utf-8")
    script = text.partition("      run: |\n")[2]

    assert 'name: "macOS Relevance Gate"' in text
    assert 'using: "composite"' in text
    assert "inputs:" not in text
    assert "outputs:\n  skip:\n" in text
    assert "value: ${{ steps.decide.outputs.skip }}" in text
    assert "      id: decide\n" in text
    assert "      working-directory: ${{ github.workspace }}\n" in text
    assert "GITHUB_TOKEN" not in text, "the gate reads local git only"
    assert "if ! python3 -m scripts.macos_relevance; then" in script
    assert "::warning::" in script
    assert 'echo "skip=false" >> "${GITHUB_OUTPUT}"' in script
    assert "skip=true" not in text
    assert "${{" not in script


def test_action_deepens_the_merge_commit_on_pull_requests_only_and_tolerates_a_failure() -> None:
    script = ACTION_FILE.read_text(encoding="utf-8").partition("      run: |\n")[2]
    fetch = 'git fetch --no-tags --no-recurse-submodules --depth=2 origin "${GITHUB_SHA}" ||'

    assert 'if [ "${GITHUB_EVENT_NAME}" = "pull_request" ]; then' in script
    assert fetch in script
    assert script.index(fetch) < script.index("python3 -m scripts.macos_relevance")


def test_other_workflows_do_not_use_the_relevance_gate() -> None:
    others = [path.name for path in WORKFLOWS_DIR.glob("*.yml") if path.name != WORKFLOW]

    assert "test-stress.yml" in others
    assert [name for name in others if "macos_relevance" in workflow_text(name)] == []
    assert [name for name in others if "macos-relevance-gate" in workflow_text(name)] == []


def test_readme_documents_the_gate_and_every_rule_it_prints() -> None:
    text = README.read_text(encoding="utf-8")
    section = text.partition("\n## macOS Relevance Gate\n")[2].partition("\n## ")[0]

    assert section, "the README has the section about the gate"
    assert text.index("## Bookkeeping-Only Push Gate") < text.index("## macOS Relevance Gate")
    assert text.index("## macOS Relevance Gate") < text.index("## CI Timing Regression Gate")
    assert "`scripts/macos_relevance.py`" in section
    assert f"`{FORCE_LABEL}`" in section
    assert f"`{FULL_RUN_STEP}`" in section
    assert "### Forcing the macOS leg" in section
    assert set(re.findall(r"^- `([a-z-]+)`: ", section, flags=re.MULTILINE)) == set(RUN_RULES + SKIP_RULES)
    assert "- `.github/actions/macos-relevance-gate/action.yml`: " in text.partition("\n## Composite Actions\n")[2]


def test_readme_lists_every_content_pattern_and_the_accepted_risk() -> None:
    section = README.read_text(encoding="utf-8").partition("\n## macOS Relevance Gate\n")[2].partition("\n## ")[0]

    assert [pattern.pattern for _, pattern in CONTENT_PATTERNS if f"`{pattern.pattern}`" not in section] == []
    assert "### Limits and accepted risk" in section
    assert "judged neutral" in section
    assert "catches it" in section
