"""Tests for the ``holon intent-check`` scope rules on hermetic ``tmp_path`` git repositories."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.harness_reference_patterns import find_references, sample_reference
from sandbox_executor.intent_assumption_rules import default_assumptions_fields
from sandbox_executor.intent_scope_check import (
    DraftIntent,
    IntentScopeReport,
    ScopeEntry,
    check_intent_scope,
    load_intent,
    render_text,
    to_json,
)
from sandbox_executor.intent_scope_graph import imported_modules, module_name

_LAYOUT = {
    "src/pkg/__init__.py": "",
    "src/pkg/core.py": "def thing():\n    return 1\n",
    "src/pkg/util.py": "VALUE = 1\n",
    "tests/test_core.py": "import pytest\n\nfrom pkg.core import thing\n\nassert thing() == 1\n",
    "README.md": "# pkg\n",
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _make_repo(tmp_path: Path, overrides: dict[str, str] | None = None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main", "-q")
    # One commit per file, so the setup itself adds no co-change pairs.
    for rel, text in {**_LAYOUT, **(overrides or {})}.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
        _commit(repo, f"add {rel}")
    return repo


def _touch_together(repo: Path, paths: list[str], times: int) -> None:
    for round_number in range(times):
        for path in paths:
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as handle:
                handle.write(f"# change {round_number}\n")
        _commit(repo, f"co-change {round_number}")


def _check(repo: Path, scope: list[str], criteria: list[str] | None = None) -> IntentScopeReport:
    intent = DraftIntent(
        scope=[ScopeEntry.parse(entry) for entry in scope],
        acceptance_criteria=criteria or [],
        payload={**default_assumptions_fields(), "scope": scope, "acceptance_criteria": criteria or []},
    )
    return check_intent_scope(intent, str(repo))


def _proposed(report: IntentScopeReport) -> dict[str, str]:
    return {item.path: item.reason for item in report.proposed}


def test_module_name_strips_src_and_maps_tests() -> None:
    assert module_name("apps/x/src/pkg/core.py") == "pkg.core"
    assert module_name("src/pkg/__init__.py") == "pkg"
    assert module_name("apps/x/tests/cli/test_a.py") == "tests.cli.test_a"
    assert module_name("README.md") is None


def test_relative_imports_resolve_against_the_package(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"src/pkg/sub/mod.py": "from .. import core\nfrom . import sibling\n"})
    assert {"pkg", "pkg.core", "pkg.sub", "pkg.sub.sibling"} <= imported_modules(str(repo), "src/pkg/sub/mod.py")


def test_listed_source_proposes_the_test_that_imports_it(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    report = _check(repo, ["src/pkg/core.py"])
    assert _proposed(report) == {"tests/test_core.py": "test imports listed module"}
    assert report.proposed[0].source == "src/pkg/core.py"


def test_listed_test_proposes_the_source_modules_it_imports(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    report = _check(repo, ["tests/test_core.py"])
    assert _proposed(report) == {"src/pkg/core.py": "listed test imports this module"}


@pytest.mark.parametrize("init_text", ["from .core import thing\n", "from pkg import core\n"])
def test_reexporting_package_init_is_proposed(tmp_path: Path, init_text: str) -> None:
    repo = _make_repo(tmp_path, {"src/pkg/__init__.py": init_text})
    report = _check(repo, ["src/pkg/core.py", "tests/test_core.py"])
    assert _proposed(report) == {"src/pkg/__init__.py": "package __init__.py re-exports listed module"}


def test_package_init_that_does_not_import_the_module_is_not_proposed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"src/pkg/__init__.py": "from .util import VALUE\n"})
    assert _proposed(_check(repo, ["src/pkg/core.py", "tests/test_core.py"])) == {}


@pytest.mark.parametrize(
    ("times", "expected"), [(3, {"README.md": "co-changed 3x with src/pkg/util.py"}), (2, {})], ids=["three", "two"]
)
def test_partner_is_proposed_from_three_shared_commits(tmp_path: Path, times: int, expected: dict[str, str]) -> None:
    repo = _make_repo(tmp_path)
    _touch_together(repo, ["src/pkg/util.py", "README.md"], times=times)
    assert _proposed(_check(repo, ["src/pkg/util.py"])) == expected


def test_bookkeeping_partner_is_never_proposed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _touch_together(repo, ["src/pkg/util.py", "plans/P-1.md"], times=4)
    assert _proposed(_check(repo, ["src/pkg/util.py"])) == {}


def test_repository_without_history_proposes_nothing(tmp_path: Path) -> None:
    repo = tmp_path / "empty"
    repo.mkdir()
    _git(repo, "init", "-b", "main", "-q")
    report = _check(repo, ["new: src/a.py"])
    assert (report.missing, report.proposed) == ([], [])


def test_missing_listed_path_is_reported(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    report = _check(repo, ["src/pkg/absent.py", "/etc/passwd"])
    assert report.missing == ["/etc/passwd", "src/pkg/absent.py"]


@pytest.mark.parametrize("entry", ["src/pkg/absent.py (new)", "new: src/pkg/absent.py", "NEW:src/pkg/absent.py"])
def test_path_marked_new_is_not_missing(tmp_path: Path, entry: str) -> None:
    repo = _make_repo(tmp_path)
    parsed = ScopeEntry.parse(entry)
    assert (parsed.path, parsed.marked_new) == ("src/pkg/absent.py", True)
    assert _check(repo, [entry]).missing == []


def test_unnamed_listed_file_is_a_warning_and_not_a_proposal(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    report = _check(repo, ["src/pkg/util.py"], ["The tool works."])
    assert report.warnings == ["src/pkg/util.py: no acceptance criterion names it by path, file name or module name"]
    assert report.proposed == []


@pytest.mark.parametrize(
    "criterion",
    ["src/pkg/util.py is updated", "util.py is updated", "the util helper is updated", "pkg.util exports VALUE"],
)
def test_naming_the_file_clears_the_warning(tmp_path: Path, criterion: str) -> None:
    repo = _make_repo(tmp_path)
    assert _check(repo, ["src/pkg/util.py"], [criterion]).warnings == []


def test_stem_inside_a_longer_word_does_not_clear_the_warning(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    assert _check(repo, ["src/pkg/util.py"], ["utilities are updated"]).warnings != []


def test_proposals_skip_scope_paths_and_are_sorted_and_unique(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"src/pkg/__init__.py": "from .core import thing\n"})
    _touch_together(repo, ["src/pkg/core.py", "tests/test_core.py", "src/pkg/util.py"], times=3)
    report = _check(repo, ["src/pkg/core.py", "src/pkg/util.py"])
    paths = [item.path for item in report.proposed]
    assert paths == ["src/pkg/__init__.py", "tests/test_core.py"]
    assert _proposed(report)["tests/test_core.py"] == "test imports listed module"


def test_load_intent_reads_scope_and_criteria(tmp_path: Path) -> None:
    intent_file = tmp_path / "intent.json"
    intent_file.write_text('{"scope": ["./a.py (new)"], "acceptance_criteria": ["a.py exists"]}', encoding="utf-8")
    intent = load_intent(str(intent_file))
    assert intent.scope == [ScopeEntry("a.py", True)]
    assert intent.acceptance_criteria == ["a.py exists"]


@pytest.mark.parametrize("text", ["not json", "[]", '{"scope": "a.py"}', '{"scope": [], "acceptance_criteria": "x"}'])
def test_load_intent_rejects_invalid_files(tmp_path: Path, text: str) -> None:
    intent_file = tmp_path / "intent.json"
    intent_file.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_intent(str(intent_file))


def test_render_text_and_json_show_every_section(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    report = _check(repo, ["src/pkg/core.py", "src/pkg/absent.py"], ["core.py is changed"])
    text = render_text(report)
    assert "Missing:\n  - src/pkg/absent.py" in text
    assert "tests/test_core.py (test imports listed module; from src/pkg/core.py)" in text
    assert text.endswith("1 proposal(s).")
    assert set(to_json(report)) == {"missing", "proposed", "warnings"}
    assert render_text(IntentScopeReport([], [], [])).count("none") == 3


@pytest.mark.parametrize(
    ("entry", "problem"), [("src/pkg/", "ends in a slash"), ("src/pkg", "is an existing directory")]
)
def test_directory_entry_is_a_violation(tmp_path: Path, entry: str, problem: str) -> None:
    repo = _make_repo(tmp_path)
    report = _check(repo, [entry], ["pkg is changed"])
    assert len(report.violations) == 1
    assert f"scope entry '{entry}' {problem}" in report.violations[0]
    assert render_text(report).startswith("Violations:\n  - scope entry")
    assert to_json(report)["violations"] == report.violations


def test_hub_file_entry_adds_a_warning_only(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"tests/conftest.py": ""})
    report = _check(repo, ["tests/conftest.py"], ["conftest.py is changed"])
    assert report.violations == []
    assert report.warnings == [
        "tests/conftest.py: hub file that shares fixtures with every test below it; "
        "expect overlap with other flows at Stage 3 admission"
    ]
    assert not render_text(report).startswith("Violations:")
    assert set(to_json(report)) == {"missing", "proposed", "warnings"}


def test_loaded_payload_text_fields_are_checked(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    sample = sample_reference("task id")
    intent_file = tmp_path / "intent.json"
    intent_file.write_text(
        json.dumps(
            {
                **default_assumptions_fields(),
                "goal": f"Close {sample}.",
                "scope": ["src/pkg/core.py"],
                "acceptance_criteria": ["core.py"],
            }
        ),
        encoding="utf-8",
    )
    report = check_intent_scope(load_intent(str(intent_file)), str(repo))
    assert len(report.violations) == 1
    assert "intent field 'goal' names a control-plane task id" in report.violations[0]
    assert find_references(render_text(report)) == []
