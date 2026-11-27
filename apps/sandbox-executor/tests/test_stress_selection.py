import textwrap
from pathlib import Path

import pytest

from tests.hermetic_fixtures import REPO_ROOT, TESTS_DIR
from tests.stress_selection import (
    canary_selection,
    hazard_modules,
    referenced_names,
    select_modules,
)
from tests.test_stress_support import CANARY_HAZARDS, HERMETIC_BODY, SURVIVAL_HAZARDS

# why: index-written is excluded because conftest.py's checkout_knowledge_index_guard covers it unconditionally
WORKSPACE_HAZARDS = {
    **{name: body for name, (body, _) in CANARY_HAZARDS.items() if name != "index-written"},
    **{name: body for name, (body, _) in SURVIVAL_HAZARDS.items()},
}


def _test_module(directory: Path, name: str, body: str) -> Path:
    path = directory / f"test_{name.replace('-', '_')}.py"
    path.write_text(
        "import os\nimport shutil\nimport subprocess\nfrom pathlib import Path\n\n\n"
        f"def test_generated(tmp_path):\n{textwrap.indent(body, '    ')}\n"
    )
    return path


@pytest.mark.parametrize("hazard", sorted(WORKSPACE_HAZARDS))
def test_rule_selects_every_workspace_hazard_the_canary_controls_trip_on(hazard: str, tmp_path: Path) -> None:
    module = _test_module(tmp_path, hazard, WORKSPACE_HAZARDS[hazard])
    assert select_modules([module], set()) == [module]


def test_rule_leaves_out_a_hermetic_module(tmp_path: Path) -> None:
    assert select_modules([_test_module(tmp_path, "hermetic", HERMETIC_BODY)], set()) == []


@pytest.mark.parametrize(
    "reference",
    [
        "from pkg.cleanup import wipe",
        "from pkg import cleanup",
        "import pkg.cleanup",
        "from unittest import mock\nPATCH = mock.patch('pkg.cleanup.wipe')",
    ],
)
def test_rule_selects_a_module_that_reaches_a_hazard_module_directly(reference: str, tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    (source_root / "pkg").mkdir(parents=True)
    (source_root / "pkg" / "__init__.py").write_text("")
    (source_root / "pkg" / "cleanup.py").write_text("import shutil\n\ndef wipe(p):\n    shutil.rmtree(p)\n")
    (source_root / "pkg" / "pure.py").write_text("def add(a, b):\n    return a + b\n")
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    reaching = tests_root / "test_reaching.py"
    reaching.write_text(f"{reference}\n")
    pure = tests_root / "test_pure.py"
    pure.write_text("from pkg.pure import add\n")
    hazards = hazard_modules(source_root, tests_root)
    assert hazards == {"pkg.cleanup"}
    assert select_modules([reaching, pure], hazards) == [reaching]


def test_referenced_names_include_every_parent_of_a_dotted_reference() -> None:
    names = referenced_names("from a.b import c\nX = 'd.e'\n")
    assert {"a", "a.b", "a.b.c", "d", "d.e"} <= names


def test_conftest_is_never_a_hazard_module(tmp_path: Path) -> None:
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "conftest.py").write_text("import os\nHOME = os.environ['HOME']\n")
    (tests_root / "helpers.py").write_text("import shutil\nrm = shutil.rmtree\n")
    assert hazard_modules(tmp_path / "src", tests_root) == {"tests.helpers"}


def test_repository_selection_covers_the_incident_module_and_narrows_the_suite() -> None:
    selected = canary_selection(REPO_ROOT, TESTS_DIR)
    every_module = [p for p in (Path(REPO_ROOT) / TESTS_DIR).rglob("test_*.py") if "e2e" not in p.parts]
    assert f"{TESTS_DIR}/test_agent_runner.py" in selected
    assert all((Path(REPO_ROOT) / path).is_file() for path in selected)
    assert not any("/e2e/" in path for path in selected)
    assert 0 < len(selected) < len(every_module)


def test_empty_selection_fails_instead_of_passing_vacuously(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    _test_module(tests_dir, "hermetic", HERMETIC_BODY)
    with pytest.raises(RuntimeError, match="selected no test module"):
        canary_selection(str(tmp_path), "tests")
