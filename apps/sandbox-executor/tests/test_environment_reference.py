"""Two-way drift guard between the ``HOLON_*`` names the code reads, the typed registry and the reference document.

The source side is scanned from Python string constants (``ast``, so identifiers such as ``HOLON_OAUTH_TOKEN_ENV`` and
comment fragments do not count) and from the raw text of the entrypoint scripts, the image build files, the CI
workflows, the ``Makefile`` and the ``holon`` launcher. Read-only: files are only written under ``tmp_path``.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path

from sandbox_executor import config_env, env_registry
from sandbox_executor.config_env_keys import DOCUMENTED_KEYS, HOST_SET_KEYS

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parents[1]
DOC = REPO / "docs" / "environment_variables.md"
REGISTRY = PACKAGE / "src" / "sandbox_executor" / "env_registry.py"

# A HOLON_* name not glued to an identifier on either side, nor a glob (`HOLON_TURN_*`) or a brace template.
NAME_PATTERN = re.compile(r"(?<![A-Za-z0-9_])HOLON_[A-Z0-9_]*[A-Z0-9](?![A-Za-z0-9_*{])")
# Matches of the pattern that are not environment variables, each with the reason.
NOT_ENVIRONMENT_NAMES = frozenset(
    {
        # A bash array local to apps/sandbox-executor/entrypoint/role_dispatcher.sh (the CA files to merge); never
        # exported.
        "HOLON_CA_SOURCES",
    }
)
# Non-Python files scanned as raw text, relative to the repository and to the package; absent ones are skipped.
TEXT_FILES = ("Dockerfile", "docker-bake.hcl", "build_all_images.sh", "Makefile", "holon")
DOC_ROW = re.compile(r"^\|\s*`(HOLON_[A-Z0-9_]+)`\s*\|")
TOKEN_PREFIXES = ("sk-" + "ant-", "gh" + "p_", "github" + "_pat_", "gh" + "o_")
# How each registry ``read_side`` value is written in the document's "read by" column.
READ_SIDE_CELLS = {
    "host": "host",
    "container": "container",
    "both": "both",
    "ci": "CI",
    "ci and container": "CI, container",
}


def python_names(path: Path) -> set[str]:
    """``HOLON_*`` names inside the string constants of one Python file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.update(NAME_PATTERN.findall(node.value))
    return names


def text_names(path: Path) -> set[str]:
    """``HOLON_*`` names anywhere in the raw text of one file."""
    return set(NAME_PATTERN.findall(path.read_text(encoding="utf-8", errors="replace")))


def _python_sources(repo: Path) -> Iterable[Path]:
    """The executor package sources (the registry itself excluded) and the repository scripts."""
    package_src = repo / "apps" / "sandbox-executor" / "src"
    yield from (path for path in package_src.rglob("*.py") if path.name != REGISTRY.name)
    yield from (repo / "scripts").glob("*.py")


def _text_sources(repo: Path) -> Iterable[Path]:
    """Entrypoint scripts, image build files, CI workflows, the Makefile and the launcher that exist in ``repo``."""
    package = repo / "apps" / "sandbox-executor"
    yield from (path for path in (package / "entrypoint").rglob("*") if path.is_file())
    for name in TEXT_FILES:
        yield from (path for path in (repo / name, package / name) if path.is_file())
    for pattern in ("*.yml", "*.yaml"):
        yield from (repo / ".github").rglob(pattern)


def source_names(repo: Path) -> set[str]:
    """Every ``HOLON_*`` environment variable name the code under ``repo`` reads or sets."""
    names: set[str] = set()
    for path in _python_sources(repo):
        names |= python_names(path)
    for path in _text_sources(repo):
        names |= text_names(path)
    return names - NOT_ENVIRONMENT_NAMES


def doc_rows(text: str) -> list[list[str]]:
    """The cells of every table row whose first cell is a backticked ``HOLON_*`` name."""
    return [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in text.splitlines()
        if DOC_ROW.match(line)
    ]


def doc_names(text: str) -> list[str]:
    """The variable names of the reference rows, in document order (duplicates kept)."""
    return [row[0].strip("`") for row in doc_rows(text)]


def doc_sections(text: str) -> dict[str, str]:
    """Each documented variable name mapped to the ``##`` heading it is listed under, lower-cased."""
    sections: dict[str, str] = {}
    heading = ""
    for line in text.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip().lower()
        elif match := DOC_ROW.match(line):
            sections[match.group(1)] = heading
    return sections


def drift(expected: set[str], actual: set[str]) -> tuple[set[str], set[str]]:
    """``(missing, stale)``: names in ``expected`` absent from ``actual``, and names in ``actual`` not expected."""
    return expected - actual, actual - expected


def _drift_message(where: Path, missing: set[str], stale: set[str]) -> str:
    """Assertion message naming the drifted names and the file to update."""
    return (
        f"{where.relative_to(REPO)} is out of step with the source: read in code but not listed {sorted(missing)}; "
        f"listed but no longer read in code {sorted(stale)}."
    )


def _doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


def test_source_scan_is_rooted():
    assert (PACKAGE / "src" / "sandbox_executor").is_dir()
    names = source_names(REPO)
    assert len(names) > 50
    assert "HOLON_TOKEN_REDUCE" in names


def test_registry_matches_source():
    missing, stale = drift(source_names(REPO), env_registry.registry_names())
    assert not missing and not stale, _drift_message(REGISTRY, missing, stale)


def test_doc_matches_source():
    missing, stale = drift(source_names(REPO), set(doc_names(_doc_text())))
    assert not missing and not stale, _drift_message(DOC, missing, stale)


def test_doc_rows_are_unique():
    names = doc_names(_doc_text())
    assert len(names) == len(set(names)), sorted({name for name in names if names.count(name) > 1})


def test_registry_names_are_unique():
    names = [variable.name for variable in env_registry.ENVIRONMENT_VARIABLES]
    assert len(names) == len(set(names)), sorted({name for name in names if names.count(name) > 1})


def test_layered_config_keys_are_registered():
    keys = {key for key, _default, _purpose in DOCUMENTED_KEYS} | set(HOST_SET_KEYS)
    holon_keys = {key for key in keys if key.startswith("HOLON_")}
    assert holon_keys - env_registry.registry_names() == set()


def test_secret_flags_agree():
    for variable in env_registry.ENVIRONMENT_VARIABLES:
        assert variable.secret == config_env.is_secret_key(variable.name), variable.name
        if variable.secret:
            assert variable.default == "unset", variable.name
    doc_secret = {row[0].strip("`") for row in doc_rows(_doc_text()) if row[7] == "yes"}
    assert doc_secret == {v.name for v in env_registry.ENVIRONMENT_VARIABLES if v.secret}


def test_doc_rows_have_every_column():
    assert {len(row) for row in doc_rows(_doc_text())} == {9}


def test_doc_groups_follow_registry_areas():
    sections = doc_sections(_doc_text())
    for variable in env_registry.ENVIRONMENT_VARIABLES:
        assert sections.get(variable.name) == variable.area.value.lower(), variable.name


def test_doc_marks_internal_variables():
    purposes = {row[0].strip("`"): row[1] for row in doc_rows(_doc_text())}
    for variable in env_registry.ENVIRONMENT_VARIABLES:
        assert purposes[variable.name].startswith("(internal)") == variable.internal, variable.name


def expected_row(variable: env_registry.EnvironmentVariable) -> list[str]:
    """The document row the registry entry renders to, cell by cell."""
    return [
        f"`{variable.name}`",
        f"(internal) {variable.purpose}" if variable.internal else variable.purpose,
        variable.value_type,
        variable.default,
        f"Unset: {variable.when_unset}. Invalid: {variable.when_invalid}.",
        READ_SIDE_CELLS[variable.read_side],
        variable.forwarding,
        "yes" if variable.secret else "no",
        variable.since,
    ]


def test_doc_cells_match_registry():
    rows = {row[0].strip("`"): row for row in doc_rows(_doc_text())}
    for variable in env_registry.ENVIRONMENT_VARIABLES:
        assert rows[variable.name] == expected_row(variable), variable.name


def test_layered_config_defaults_match_registry():
    registry = {variable.name: variable for variable in env_registry.ENVIRONMENT_VARIABLES}
    for key, default, _purpose in DOCUMENTED_KEYS:
        if key in registry:
            assert default == registry[key].default, key


def test_doc_has_no_secret_values():
    text = _doc_text()
    assert not [prefix for prefix in TOKEN_PREFIXES if prefix in text]
    for variable in env_registry.ENVIRONMENT_VARIABLES:
        if variable.secret:
            for value in re.findall(rf"(?<![A-Za-z0-9_]){variable.name}=(\S*)", text):
                assert value.startswith("<"), f"{variable.name} is assigned a non-placeholder value in {DOC.name}"


def test_drift_reports_a_name_missing_from_the_registry():
    assert drift({"HOLON_A", "HOLON_B"}, {"HOLON_A"}) == ({"HOLON_B"}, set())


def test_drift_reports_a_stale_registry_name():
    assert drift({"HOLON_A"}, {"HOLON_A", "HOLON_GONE"}) == (set(), {"HOLON_GONE"})


def test_python_names_skips_identifiers(tmp_path):
    module = tmp_path / "module.py"
    module.write_text('HOLON_SOME_CONST = "HOLON_REAL_NAME"\n# HOLON_IN_A_COMMENT\n', encoding="utf-8")
    assert python_names(module) == {"HOLON_REAL_NAME"}


def test_name_pattern_skips_globs_and_glued_identifiers():
    text = "HOLON_TURN_*_SECONDS _HOLON_KEY_RE ALLOWED_NON_HOLON_KEYS ${HOLON_ROLE:-} HOLON_X{y}"
    assert set(NAME_PATTERN.findall(text)) == {"HOLON_ROLE"}
