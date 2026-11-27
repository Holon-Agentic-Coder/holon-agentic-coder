from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor"

MODULE_BROAD_EXCEPT_BASELINES: dict[str, int] = {
    "agent_runner.py": 6,
    "attribution_check.py": 1,
    "calibration/branches.py": 2,
    "calibration/generate.py": 3,
    "calibration/physics.py": 1,
    "calibration/refs.py": 11,
    "calibration/run.py": 1,
    "calibration_predicted.py": 1,
    "cli/commands/calibrate.py": 1,
    "cli/commands/flow.py": 1,
    "cli/commands/review.py": 1,
    "cli/doctor.py": 1,
    "cli/doctor_env.py": 5,
    "entrypoint/executor/__main__.py": 1,
    "entrypoint/executor/agent_output.py": 1,
    "entrypoint/executor/outcome.py": 3,
    "entrypoint/executor/pre_push.py": 2,
    "entrypoint/executor/run.py": 2,
    "entrypoint/executor_test_run.py": 1,
    "entrypoint/planner.py": 3,
    "entrypoint/resolver_test_failure.py": 1,
    "entrypoint/review_loop.py": 4,
    "entrypoint/test_failure_classify.py": 1,
    "entrypoint/test_failure_loop.py": 1,
    "entrypoint/test_failure_record.py": 1,
    "flow/calibrate_promotion.py": 1,
    "flow/calibrate_rerun.py": 2,
    "flow/calibrate_retry.py": 1,
    "flow/calibrate_stale_warning.py": 3,
    "flow/conflict_resolver_role.py": 1,
    "flow/github/approval_invalidation.py": 2,
    "flow/github/ci.py": 1,
    "flow/github/ci_failure_detail.py": 1,
    "flow/github/ci_final_head.py": 2,
    "flow/github/ci_final_head_rerun.py": 1,
    "flow/github/ci_final_head_seams.py": 1,
    "flow/github/ci_head_checks.py": 1,
    "flow/github/ci_head_reruns.py": 1,
    "flow/github/ci_reverify.py": 2,
    "flow/github/ci_slowest_tests.py": 1,
    "flow/github/ci_timing_rerun.py": 1,
    "flow/github/pr.py": 3,
    "flow/github/review_post.py": 3,
    "flow/github/stage5_test_failure.py": 1,
    "flow/github/token_scopes.py": 1,
    "flow/pipeline.py": 2,
    "flow/review/approval_fingerprint.py": 1,
    "flow/review/ci_noise_rerun.py": 1,
    "flow/review/consensus_withdrawal.py": 1,
    "flow/review/delta_review_request.py": 1,
    "flow/review/ensemble.py": 3,
    "flow/review/ledger_commit.py": 1,
    "flow/review/local_tests.py": 1,
    "flow/review/loop.py": 3,
    "flow/review/merged_head_gate.py": 1,
    "flow/review/posting.py": 1,
    "flow/review/proxy_env.py": 1,
    "flow/review/resolver.py": 2,
    "flow/review/verdict.py": 1,
    "flow/stage5.py": 1,
    "flow/stage_conflicts.py": 1,
    "flow/stages/calibrate.py": 2,
    "flow/stages/children.py": 2,
    "flow/stages/execute_veto.py": 1,
    "flow/stages/review.py": 1,
    "flow/stages/stage_support.py": 1,
    "flow/target_sync_merge.py": 2,
    "formatting.py": 1,
    "knowledge/consult.py": 1,
    "knowledge/ledger_fragment_sync.py": 1,
    "knowledge/ledger_sync.py": 1,
    "knowledge/promotion_rules.py": 1,
    "knowledge/records.py": 2,
    "knowledge/stats.py": 1,
    "launcher/coherence.py": 1,
    "launcher/containers.py": 1,
    "ledger/lock.py": 1,
    "ledger/reconcile.py": 2,
    "ledger/rows.py": 3,
    "review_ensemble.py": 2,
    "review_report.py": 1,
    "worktree_head.py": 2,
}

PACKAGE_BROAD_EXCEPT_BASELINES: dict[str, int] = {
    "": 14,
    "calibration": 18,
    "cli": 6,
    "cli/commands": 3,
    "entrypoint": 12,
    "entrypoint/executor": 9,
    "flow": 14,
    "flow/github": 22,
    "flow/review": 18,
    "flow/stages": 7,
    "knowledge": 7,
    "launcher": 2,
    "ledger": 6,
}


def _is_broad_exception_type(expr: ast.expr | None) -> bool:
    if expr is None:
        return False
    if isinstance(expr, ast.Name):
        return expr.id == "Exception"
    if isinstance(expr, ast.Attribute):
        return expr.attr == "Exception" and isinstance(expr.value, ast.Name) and expr.value.id == "builtins"
    if isinstance(expr, ast.Tuple):
        return any(_is_broad_exception_type(elt) for elt in expr.elts)
    return False


def count_broad_except_in_source(source: str) -> int:
    tree = ast.parse(source)
    return sum(
        1 for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler) and _is_broad_exception_type(node.type)
    )


def scan_package_broad_except_counts(package_dir: Path) -> tuple[dict[str, int], dict[str, int]]:
    module_counts: dict[str, int] = {}
    package_counts: dict[str, int] = {}
    for path in sorted(package_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        count = count_broad_except_in_source(path.read_text(encoding="utf-8"))
        if count == 0:
            continue
        module_key = path.relative_to(package_dir).as_posix()
        package_key = "" if path.parent == package_dir else path.parent.relative_to(package_dir).as_posix()
        module_counts[module_key] = count
        package_counts[package_key] = package_counts.get(package_key, 0) + count
    return module_counts, package_counts


def find_ratchet_violations(
    module_counts: dict[str, int],
    package_counts: dict[str, int],
    module_baselines: dict[str, int],
    package_baselines: dict[str, int],
) -> list[str]:
    violations: list[str] = []
    for module, count in sorted(module_counts.items()):
        cap = module_baselines.get(module, 0)
        if count > cap:
            violations.append(f"module {module}: {count} broad except Exception catches (cap {cap})")
    for package, count in sorted(package_counts.items()):
        cap = package_baselines.get(package, 0)
        if count > cap:
            label = package or "<root>"
            violations.append(f"package {label}: {count} broad except Exception catches (cap {cap})")
    return violations


def _package_key_for_module(module_key: str) -> str:
    return module_key.rsplit("/", 1)[0] if "/" in module_key else ""


def test_broad_except_baseline_consistency() -> None:
    assert MODULE_BROAD_EXCEPT_BASELINES
    assert PACKAGE_BROAD_EXCEPT_BASELINES
    assert all(count > 0 for count in MODULE_BROAD_EXCEPT_BASELINES.values())
    assert all(count > 0 for count in PACKAGE_BROAD_EXCEPT_BASELINES.values())
    expected_packages: dict[str, int] = {}
    for module_key, count in MODULE_BROAD_EXCEPT_BASELINES.items():
        package_key = _package_key_for_module(module_key)
        expected_packages[package_key] = expected_packages.get(package_key, 0) + count
    assert expected_packages == PACKAGE_BROAD_EXCEPT_BASELINES


def test_sandbox_executor_broad_except_ratchet() -> None:
    assert any(PACKAGE_DIR.rglob("*.py"))
    module_counts, package_counts = scan_package_broad_except_counts(PACKAGE_DIR)
    violations = find_ratchet_violations(
        module_counts,
        package_counts,
        MODULE_BROAD_EXCEPT_BASELINES,
        PACKAGE_BROAD_EXCEPT_BASELINES,
    )
    assert violations == [], "\n".join(violations)


def _write_module(root: Path, relative_path: str, source: str) -> None:
    target = root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("try:\n    pass\nexcept Exception:\n    pass\n", 1),
        ("try:\n    pass\nexcept Exception as exc:\n    _ = exc\n", 1),
        ("try:\n    pass\nexcept (ValueError, Exception):\n    pass\n", 1),
        ("import builtins\ntry:\n    pass\nexcept builtins.Exception:\n    pass\n", 1),
        ("try:\n    pass\nexcept* Exception:\n    pass\n", 1),
        ("try:\n    pass\nexcept ValueError:\n    pass\n", 0),
        ("try:\n    pass\nexcept (OSError, http.client.HTTPException):\n    pass\n", 0),
        ("try:\n    pass\nexcept BaseException:\n    pass\n", 0),
        ("try:\n    pass\nexcept:\n    pass\n", 0),
        ('text = "except Exception:"\n# except Exception:\nx = 1\n', 0),
    ],
)
def test_count_broad_except_in_source(source: str, expected: int) -> None:
    assert count_broad_except_in_source(source) == expected


def test_ratchet_fails_when_baselined_module_exceeds_cap(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        "sub/alpha.py",
        "try:\n    pass\nexcept Exception:\n    pass\ntry:\n    pass\nexcept Exception:\n    pass\n",
    )
    module_counts, package_counts = scan_package_broad_except_counts(tmp_path)
    violations = find_ratchet_violations(
        module_counts,
        package_counts,
        {"sub/alpha.py": 1},
        {"sub": 1},
    )
    assert len(violations) == 2
    assert any("module sub/alpha.py: 2" in item and "(cap 1)" in item for item in violations)
    assert any("package sub: 2" in item and "(cap 1)" in item for item in violations)


def test_ratchet_fails_for_unlisted_module_and_unlisted_package(tmp_path: Path) -> None:
    _write_module(tmp_path, "fresh/new_mod.py", "try:\n    pass\nexcept Exception:\n    pass\n")
    module_counts, package_counts = scan_package_broad_except_counts(tmp_path)
    violations = find_ratchet_violations(module_counts, package_counts, {}, {})
    assert len(violations) == 2
    assert any("module fresh/new_mod.py: 1" in item and "(cap 0)" in item for item in violations)
    assert any("package fresh: 1" in item and "(cap 0)" in item for item in violations)


def test_ratchet_fails_for_unlisted_module_in_root_package(tmp_path: Path) -> None:
    _write_module(tmp_path, "root_mod.py", "try:\n    pass\nexcept Exception:\n    pass\n")
    module_counts, package_counts = scan_package_broad_except_counts(tmp_path)
    violations = find_ratchet_violations(module_counts, package_counts, {}, {})
    assert len(violations) == 2
    assert any("module root_mod.py: 1" in item and "(cap 0)" in item for item in violations)
    assert any("package <root>: 1" in item and "(cap 0)" in item for item in violations)


def test_ratchet_fails_when_package_cap_exceeded_despite_module_caps(tmp_path: Path) -> None:
    _write_module(tmp_path, "pkg/one.py", "try:\n    pass\nexcept Exception:\n    pass\n")
    _write_module(tmp_path, "pkg/two.py", "try:\n    pass\nexcept Exception:\n    pass\n")
    module_counts, package_counts = scan_package_broad_except_counts(tmp_path)
    violations = find_ratchet_violations(
        module_counts,
        package_counts,
        {"pkg/one.py": 1, "pkg/two.py": 1},
        {"pkg": 1},
    )
    assert violations == ["package pkg: 2 broad except Exception catches (cap 1)"]


def test_ratchet_passes_when_counts_equal_baseline(tmp_path: Path) -> None:
    _write_module(tmp_path, "root.py", "try:\n    pass\nexcept Exception:\n    pass\n")
    _write_module(tmp_path, "pkg/mod.py", "try:\n    pass\nexcept Exception:\n    pass\n")
    module_counts, package_counts = scan_package_broad_except_counts(tmp_path)
    violations = find_ratchet_violations(
        module_counts,
        package_counts,
        {"root.py": 1, "pkg/mod.py": 1},
        {"": 1, "pkg": 1},
    )
    assert violations == []


def test_ratchet_passes_when_broad_catches_are_reduced(tmp_path: Path) -> None:
    _write_module(tmp_path, "pkg/mod.py", "try:\n    pass\nexcept ValueError:\n    pass\n")
    module_counts, package_counts = scan_package_broad_except_counts(tmp_path)
    violations = find_ratchet_violations(
        module_counts,
        package_counts,
        {"pkg/mod.py": 2},
        {"pkg": 2},
    )
    assert violations == []


def test_ratchet_passes_when_baselined_module_removed_and_clean_module_added(tmp_path: Path) -> None:
    _write_module(tmp_path, "pkg/clean.py", "try:\n    pass\nexcept OSError:\n    pass\n")
    module_counts, package_counts = scan_package_broad_except_counts(tmp_path)
    violations = find_ratchet_violations(
        module_counts,
        package_counts,
        {"pkg/removed.py": 1},
        {"pkg": 1},
    )
    assert violations == []
