"""Tests for scripts/check_module_budgets.py: per-module line limits and the frozen budget configuration.

CI's hygiene job runs the script directly; ``uv run task lint`` runs it locally. These tests pin its behaviour on
hermetic ``tmp_path`` git repositories (discovery reads ``git ls-files``), and the live tests pin the configuration
against the real tree. ``test_module_budget_discovery.py`` pins discovery, ``exclude`` and the ratchet.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.check_module_budgets import ALLOW_CHANGE_ENV, DEFAULT_CONFIG, describe_change, main
from tests.flow.helpers import _git

PACKAGE = "apps/app/src/pkg"
BASE_CONFIG = {"default_max_lines": 10, "exclude": [], "overrides": {f"{PACKAGE}/big.py": 20}}


def _module(root: Path, path: str, lines: int) -> None:
    module = root / path
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text("".join(f"x{i} = {i}\n" for i in range(lines)), encoding="utf-8")


def _write_config(path: Path, config: dict) -> Path:
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path


def _track(root: Path) -> None:
    """Make ``root`` a git repository (once) and track every file in it, so discovery lists them."""
    if not (root / ".git").exists():
        _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")


def _repo(root: Path) -> Path:
    _module(root, f"{PACKAGE}/big.py", 15)
    _module(root, f"{PACKAGE}/small.py", 4)
    return _write_config(root / "budgets.json", BASE_CONFIG)


def _run(root: Path, config: Path, base: str | Path = "none") -> int:
    _track(root)
    return main(["--root", str(root), "--config", str(config), "--base", str(base)])


def _lines(capsys: pytest.CaptureFixture[str], prefix: str) -> list[str]:
    return [line for line in capsys.readouterr().out.splitlines() if line.startswith(prefix)]


@pytest.fixture(autouse=True)
def _no_authorisation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ALLOW_CHANGE_ENV, raising=False)


def test_the_live_configuration_passes_against_the_real_tree(capsys: pytest.CaptureFixture[str]):
    # Ratchet-down notices and stale-entry warnings are advisory: lowering or removing an override is a frozen
    # configuration change, so a partial split or a deleted module must not fail the suite before that lands.
    assert main(["--base", "none"]) == 0
    assert "Module budgets passed" in capsys.readouterr().out


def test_the_live_configuration_holds_only_the_three_keys_and_sorted_overrides_over_the_default():
    config = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    assert list(config) == ["default_max_lines", "exclude", "overrides"]
    # The maintainer's staged schedule lowers the default by 50 per pull request (400, 350, 300, 250).
    assert config["default_max_lines"] == 350
    assert all(isinstance(entry["reason"], str) and entry["reason"].strip() for entry in config["exclude"])
    assert list(config["overrides"]) == sorted(config["overrides"])
    assert all(type(limit) is int and limit > config["default_max_lines"] for limit in config["overrides"].values())


def test_a_new_module_under_the_default_needs_no_entry(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    config = _repo(tmp_path)
    _module(tmp_path, f"{PACKAGE}/stages/new.py", 9)
    assert _run(tmp_path, config) == 0
    assert "Module budgets passed: 3 modules (default 10, 1 overrides, 0 excluded)" in capsys.readouterr().out


@pytest.mark.parametrize(("module", "lines", "limit"), [("small.py", 11, 10), ("big.py", 21, 20)])
def test_a_module_over_its_limit_fails_and_says_split(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], module: str, lines: int, limit: int
):
    config = _repo(tmp_path)
    _module(tmp_path, f"{PACKAGE}/{module}", lines)
    assert _run(tmp_path, config) == 1
    errors = _lines(capsys, "::error::")
    assert len(errors) == 1
    assert f"{PACKAGE}/{module}: {lines} lines exceeds its limit of {limit}" in errors[0]
    assert "split" in errors[0]
    assert "may not be changed" in errors[0]


@pytest.mark.parametrize(
    ("update", "warning"),
    [
        ({"overrides": {f"{PACKAGE}/big.py": 20, f"{PACKAGE}/gone.py": 40}}, "names a missing module"),
        ({"overrides": {f"{PACKAGE}/big.py": 20, f"{PACKAGE}/small.py": 10}}, "at or below the default"),
        ({"overrides": {f"{PACKAGE}/big.py": 20, "elsewhere/mod.py": 40}}, "not a discovered source module"),
        ({"overrides": {f"{PACKAGE}/small.py": 30, f"{PACKAGE}/big.py": 20}}, "not sorted"),
        (
            {"exclude": [{"path": f"{PACKAGE}/big.py", "reason": "generated"}], "overrides": {f"{PACKAGE}/big.py": 20}},
            "names an excluded path",
        ),
        ({"exclude": [{"path": "scripts/gone.py", "reason": "vendored"}]}, "matches no tracked module"),
    ],
    ids=["missing-module", "redundant", "outside-source-roots", "unsorted", "excluded", "stale-exclude"],
)
def test_stale_or_redundant_entries_only_warn(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], update: dict, warning: str
):
    _repo(tmp_path)
    config = _write_config(tmp_path / "custom.json", {**BASE_CONFIG, **update})
    assert _run(tmp_path, config) == 0
    out = capsys.readouterr().out
    assert any(line.startswith("::warning::") and warning in line for line in out.splitlines())
    assert "::error::" not in out


def test_every_source_root_is_counted_and_enforced(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    config = _repo(tmp_path)
    _module(tmp_path, "apps/other/src/lib/mod.py", 12)
    _module(tmp_path, "scripts/tool.py", 3)
    assert _run(tmp_path, config) == 1
    assert "apps/other/src/lib/mod.py: 12 lines exceeds its limit of 10" in _lines(capsys, "::error::")[0]
    _module(tmp_path, "apps/other/src/lib/mod.py", 3)
    assert _run(tmp_path, config) == 0
    assert "Module budgets passed: 4 modules" in capsys.readouterr().out


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps({**BASE_CONFIG, "description": "history"}),
        json.dumps({"default_max_lines": 10, "packages": [PACKAGE], "overrides": {}}),
        json.dumps({**BASE_CONFIG, "default_max_lines": True}),
        json.dumps({**BASE_CONFIG, "exclude": {}}),
        json.dumps({**BASE_CONFIG, "exclude": [{"path": "/abs/mod.py", "reason": "generated"}]}),
        json.dumps({**BASE_CONFIG, "exclude": [{"path": "../mod.py", "reason": "generated"}]}),
        json.dumps({**BASE_CONFIG, "exclude": [{"path": "a.py", "reason": "generated", "note": "x"}]}),
        json.dumps({**BASE_CONFIG, "overrides": {f"{PACKAGE}/big.py": "15"}}),
    ],
    ids=[
        "not-json",
        "extra-key",
        "old-packages-schema",
        "bool-default",
        "exclude-not-a-list",
        "absolute-exclude",
        "parent-exclude",
        "extra-exclude-key",
        "string-limit",
    ],
)
def test_an_invalid_configuration_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str):
    _repo(tmp_path)
    config = tmp_path / "bad.json"
    config.write_text(content, encoding="utf-8")
    assert _run(tmp_path, config) == 1
    assert "::error::module budget configuration" in capsys.readouterr().out


CHANGES = {
    "raise": ({"overrides": {f"{PACKAGE}/big.py": 25}}, "override raised"),
    "lower": ({"overrides": {f"{PACKAGE}/big.py": 18}}, "override lowered"),
    "add": ({"overrides": {f"{PACKAGE}/big.py": 20, f"{PACKAGE}/small.py": 30}}, "override added"),
    "delete": ({"default_max_lines": 15, "overrides": {}}, "override removed"),
    "default": ({"default_max_lines": 11}, "default_max_lines 10 -> 11"),
    "exclude": ({"exclude": [{"path": f"{PACKAGE}/small.py", "reason": "generated"}]}, "exclude changed"),
}


def test_an_override_change_from_a_non_integer_base_limit_is_reported_as_changed():
    base = json.dumps({"overrides": {"a.py": "20"}})
    head = json.dumps({"overrides": {"a.py": 25}})
    assert describe_change(base, head) == ["override changed: a.py (20 -> 25)"]


def test_the_migration_from_packages_to_exclude_is_described_key_by_key():
    base = json.dumps({"default_max_lines": 200, "packages": ["flow"], "overrides": {"a.py": 300}})
    head = json.dumps({"default_max_lines": 200, "exclude": [], "overrides": {"a.py": 290}})
    assert describe_change(base, head) == ["packages removed", "exclude added", "override lowered: a.py (300 -> 290)"]


@pytest.mark.parametrize("change", sorted(CHANGES))
def test_any_change_against_the_base_copy_fails_unless_authorised(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, change: str
):
    base = _repo(tmp_path)
    update, description = CHANGES[change]
    config = _write_config(tmp_path / "changed.json", {**BASE_CONFIG, **update})
    assert _run(tmp_path, config, base) == 1
    errors = _lines(capsys, "::error::")
    assert len(errors) == 1
    assert description in errors[0]
    assert "frozen" in errors[0]
    assert "module-budget-change" in errors[0]

    monkeypatch.setenv(ALLOW_CHANGE_ENV, "1")
    assert _run(tmp_path, config, base) == 0
    notices = _lines(capsys, "::notice::")
    assert any(description in line and "authorised" in line for line in notices)


def test_a_reformatted_but_equal_configuration_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    base = _repo(tmp_path)
    config = tmp_path / "compact.json"
    config.write_text(json.dumps(BASE_CONFIG) + "\n", encoding="utf-8")
    assert _run(tmp_path, config, base) == 1
    assert "(reformatted)" in _lines(capsys, "::error::")[0]


def test_an_identical_base_copy_passes(tmp_path: Path):
    config = _repo(tmp_path)
    base = tmp_path / "base.json"
    base.write_bytes(config.read_bytes())
    assert _run(tmp_path, config, base) == 0


def test_no_base_skips_the_comparison(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    _repo(tmp_path)
    config = _write_config(tmp_path / "changed.json", {**BASE_CONFIG, "default_max_lines": 99})
    assert _run(tmp_path, config, "none") == 0
    assert "merge-base" not in capsys.readouterr().out


def _git_repo(root: Path) -> Path:
    _git(root, "init", "-q", "-b", "main")
    _module(root, f"{PACKAGE}/big.py", 15)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    return root / "budgets.json"


def test_a_configuration_missing_at_the_merge_base_is_skipped_with_a_notice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    config = _write_config(_git_repo(tmp_path), BASE_CONFIG)
    assert main(["--root", str(tmp_path), "--config", str(config)]) == 0
    assert "has no copy at the merge base" in _lines(capsys, "::notice::")[0]


def test_the_merge_base_copy_is_compared(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    config = _write_config(_git_repo(tmp_path), BASE_CONFIG)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "config")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert main(["--root", str(tmp_path), "--config", str(config)]) == 0
    _write_config(config, {**BASE_CONFIG, "overrides": {f"{PACKAGE}/big.py": 21}})
    assert main(["--root", str(tmp_path), "--config", str(config)]) == 1
    assert "override raised" in _lines(capsys, "::error::")[0]


def test_no_merge_base_skips_the_comparison_quietly(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    config = _write_config(tmp_path / "budgets.json", {**BASE_CONFIG, "overrides": {}})
    _module(tmp_path, f"{PACKAGE}/small.py", 4)
    _track(tmp_path)
    assert main(["--root", str(tmp_path), "--config", str(config)]) == 0
    assert "::notice::" not in capsys.readouterr().out
