from __future__ import annotations

from pathlib import Path

import pytest

from tests.self_doc_scanner import (
    count_lines,
    find_comments,
    find_disallowed_comments,
    find_docstrings_and_string_statements,
    is_allowlisted_comment,
)

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor"

GOVERNED_MODULES_BY_PACKAGE: dict[str, tuple[str, ...]] = {
    "": (
        "agent_api_error.py",
        "agent_attribution.py",
        "agent_defaults.py",
        "approval_stamp.py",
        "artefacts.py",
        "attribution_check.py",
        "budget_limits.py",
        "budgets.py",
        "claude_credential.py",
        "claude_keychain.py",
        "claude_token_migration.py",
        "config_env.py",
        "config_env_keys.py",
        "config_env_model.py",
        "config_env_parse.py",
        "config_env_secrets.py",
        "decomposition_rule.py",
        "e2e_guards.py",
        "e2e_harness.py",
        "e2e_teardown.py",
        "e2e_types.py",
        "flow_guard.py",
        "formatting.py",
        "git.py",
        "git_flow.py",
        "git_revisions.py",
        "local_llm.py",
        "naming.py",
        "plan_attempt.py",
        "plan_paths.py",
        "pr_patch_paths.py",
        "preflight.py",
        "ref_names.py",
        "review_failure.py",
        "review_paths.py",
        "review_report.py",
        "review_target.py",
        "touch_set.py",
        "touch_set_overlap.py",
        "worktree_head.py",
    ),
    "cli/commands": (
        "__init__.py",
        "_flow_resume.py",
        "_flow_worktree.py",
        "_host_publish.py",
        "_role.py",
        "_worktree_guard.py",
        "calibrate.py",
        "consult.py",
        "doctor.py",
        "execute.py",
        "init.py",
        "intent.py",
        "ledger.py",
        "ledger_promote.py",
        "models.py",
        "plan.py",
        "promote.py",
        "reconcile.py",
        "record.py",
        "review.py",
        "review_fetch.py",
        "stats.py",
        "veto.py",
    ),
    "entrypoint": (
        "agent_output_block.py",
        "changed_markdown.py",
        "empty_diff_excerpt.py",
        "execution_args.py",
        "execution_workspace.py",
        "executor_test_run.py",
        "git_locks.py",
        "intent_creator.py",
        "partial_resume.py",
        "pytest_summary.py",
        "rejected_attempt.py",
        "review_failure_detail.py",
        "review_markdown_gate.py",
        "review_verification.py",
        "sync_conflict.py",
        "taken_ids.py",
        "test_measurement.py",
        "uv_lock.py",
    ),
    "entrypoint/executor": (
        "__main__.py",
        "agent_output.py",
        "commands.py",
        "outcome.py",
        "pre_push.py",
        "repair.py",
        "sub_intents.py",
    ),
    "flow": ("git_refs.py",),
    "flow/github": (
        "adapter.py",
        "adapter_runner.py",
        "approval_invalidation.py",
        "ci_final_head.py",
        "ci_head_checks.py",
        "ci_reverify.py",
        "ci_timing_rerun.py",
        "ci_wait.py",
        "merge.py",
        "merge_method.py",
        "mergeability_policy.py",
        "pr.py",
        "pr_head.py",
        "pr_metadata.py",
        "review_policy.py",
        "review_post.py",
        "token_scopes.py",
    ),
    "knowledge": (
        "__init__.py",
        "consult.py",
        "consult_filters.py",
        "index.py",
        "ledger_sync.py",
        "promotion.py",
        "promotion_rules.py",
        "records.py",
        "stats.py",
        "wisdom.py",
    ),
    "launcher": (
        "__init__.py",
        "antigravity_mounts.py",
        "coherence.py",
        "container_env.py",
        "containers.py",
        "env_files.py",
        "messages.py",
        "mounts.py",
        "pi_mounts.py",
        "proxy_attach.py",
        "proxy_image.py",
        "proxy_paths.py",
        "proxy_readiness.py",
        "role_dispatch.py",
        "root_ca.py",
        "root_ca_openssl.py",
        "sidecar.py",
        "sidecar_plan.py",
    ),
    "ledger": (
        "__init__.py",
        "fragments.py",
        "lock.py",
        "merged.py",
        "order.py",
        "promote.py",
        "promote_threshold.py",
        "query.py",
        "query_filters.py",
        "reconcile.py",
        "route.py",
        "rows.py",
    ),
}

GOVERNED_MODULES: tuple[str, ...] = tuple(
    sorted(
        f"{package}/{filename}" if package else filename
        for package, filenames in GOVERNED_MODULES_BY_PACKAGE.items()
        for filename in filenames
    )
)

BASELINE_LINE_CEILINGS: dict[str, int] = {"git_flow.py": 1008, "local_llm.py": 1049}


def _read_module(relative_path: str) -> str:
    return (PACKAGE_DIR / relative_path).read_text(encoding="utf-8")


def _under_ceiling(source: str, ceiling: int) -> bool:
    return count_lines(source) < ceiling


def test_registry_has_146_unique_modules() -> None:
    assert len(GOVERNED_MODULES) == 146
    assert len(set(GOVERNED_MODULES)) == len(GOVERNED_MODULES)
    assert set(BASELINE_LINE_CEILINGS) <= set(GOVERNED_MODULES)


@pytest.mark.parametrize("relative_path", GOVERNED_MODULES)
def test_governed_module_exists(relative_path: str) -> None:
    assert (PACKAGE_DIR / relative_path).is_file(), f"Missing governed module: {relative_path}"


@pytest.mark.parametrize("relative_path", GOVERNED_MODULES)
def test_governed_module_has_no_docstrings_or_string_statements(relative_path: str) -> None:
    findings = find_docstrings_and_string_statements(_read_module(relative_path))
    assert findings == [], f"{relative_path} contains docstrings or string statements: {findings}"


@pytest.mark.parametrize("relative_path", GOVERNED_MODULES)
def test_governed_module_has_no_disallowed_comments(relative_path: str) -> None:
    findings = find_disallowed_comments(_read_module(relative_path))
    assert findings == [], f"{relative_path} contains non-allowlisted comments: {findings}"


@pytest.mark.parametrize(("relative_path", "ceiling"), BASELINE_LINE_CEILINGS.items())
def test_module_stays_under_baseline_line_ceiling(relative_path: str, ceiling: int) -> None:
    source = _read_module(relative_path)
    assert _under_ceiling(source, ceiling), f"{relative_path} has {count_lines(source)} lines; expected < {ceiling}"


def test_ledger_registry_matches_package_directory() -> None:
    actual = tuple(sorted(path.name for path in (PACKAGE_DIR / "ledger").glob("*.py")))
    assert actual == GOVERNED_MODULES_BY_PACKAGE["ledger"]


def test_ledger_lock_keeps_type_ignore_pragma() -> None:
    comments = [text for _, text in find_comments(_read_module("ledger/lock.py"))]
    assert "# type: ignore[assignment]" in comments


def test_intent_creator_keeps_shebang() -> None:
    assert _read_module("entrypoint/intent_creator.py").splitlines()[0] == "#!/usr/bin/env python3"


@pytest.mark.parametrize(
    ("line_number", "comment_text", "column", "expected"),
    [
        (1, "#!/usr/bin/env python3", 0, True),
        (2, "#!/usr/bin/env python3", 0, False),
        (1, "#!/usr/bin/env python3", 4, False),
        (1, "# -*- coding: utf-8 -*-", 0, True),
        (2, "# coding=utf-8", 0, True),
        (2, "# coding: utf-8", 0, True),
        (3, "# coding: utf-8", 0, False),
        (3, "# -*- coding: utf-8 -*-", 0, False),
        (10, "# noqa", 0, True),
        (10, "# noqa: E501", 0, True),
        (10, "# noqa: E501, F401", 0, True),
        (10, "# noqa: C901, PLR0912", 0, True),
        (10, "# NOQA: PLR0913", 0, True),
        (10, "# type: ignore", 0, True),
        (10, "# type: ignore[assignment]", 0, True),
        (10, "# type: ignore[arg-type]", 0, True),
        (10, "# type: ignore[return-value]", 0, True),
        (10, "# pragma: no cover", 0, True),
        (10, "# PRAGMA: NO COVER", 0, True),
        (10, "# Explanatory prose comment.", 0, False),
        (10, "# (LedgerQuery field, predicate) in evaluation order.", 0, False),
        (10, "# TODO: remove later", 0, False),
        (10, "#: sphinx attribute comment", 0, False),
        (10, "# --- Section banner ---", 0, False),
        (10, "#", 0, False),
        (10, "# noqa because of reasons", 0, False),
        (10, "# noqa: E501 extra prose", 0, False),
        (10, "# noqa: PLR0913 because reasons", 0, False),
        (10, "# noquality check needed", 0, False),
        (10, "# explain why noqa is needed", 0, False),
        (10, "# type: ignore explanatory note", 0, False),
        (10, "# type: ignore[arg-type] extra text", 0, False),
        (10, "# type information for callers", 0, False),
        (10, "# mentions type: ignore in prose", 0, False),
        (10, "# pragma: no cover extra text", 0, False),
        (10, "# pragma note about coverage", 0, False),
        (10, "# mentions pragma: no cover in prose", 0, False),
    ],
)
def test_comment_allowlist_rules(line_number: int, comment_text: str, column: int, expected: bool) -> None:
    assert is_allowlisted_comment(line_number, comment_text, column) is expected


def test_find_disallowed_comments_reports_line_numbers_and_columns() -> None:
    source = (
        "#!/usr/bin/env python3\n"
        "# -*- coding: utf-8 -*-\n"
        "x = 1  # noqa: E501\n"
        "y = 2  # type: ignore[arg-type]\n"
        "if x:  # pragma: no cover\n"
        "    pass\n"
        "#!/usr/bin/env python3\n"
        "# coding: utf-8\n"
        "z = 3  # explain z\n"
    )
    assert find_disallowed_comments(source) == [
        (7, "#!/usr/bin/env python3"),
        (8, "# coding: utf-8"),
        (9, "# explain z"),
    ]


def test_detector_reports_docstrings_and_string_statements() -> None:
    source = (
        '"""module doc"""\n'
        "class Example:\n"
        '    """class doc"""\n'
        "    field: int\n"
        '    """attribute doc"""\n'
        "    def method(self) -> None:\n"
        '        """"""\n'
        "def sync_fn() -> None:\n"
        "    value = 1\n"
        '    "standalone string"\n'
        "async def async_fn() -> None:\n"
        '    """async doc"""\n'
        "try:\n"
        "    pass\n"
        "finally:\n"
        '    "finally string"\n'
    )
    assert find_docstrings_and_string_statements(source) == [
        (1, "docstring:<module>"),
        (3, "docstring:Example"),
        (5, "string statement"),
        (7, "docstring:method"),
        (10, "string statement"),
        (12, "docstring:async_fn"),
        (16, "string statement"),
    ]


def test_detector_ignores_strings_inside_expressions() -> None:
    source = 'value = "text"\nprint("text")\nnames = ["a", "b"]\nlabel = f"{value}"\n'
    assert find_docstrings_and_string_statements(source) == []


def test_inserted_docstring_fails_governed_module_check() -> None:
    mutated = '"""Inserted module docstring."""\n' + _read_module("ledger/route.py")
    assert find_docstrings_and_string_statements(mutated) == [(1, "docstring:<module>")]


def test_inserted_comment_fails_governed_module_check() -> None:
    source = _read_module("ledger/route.py")
    mutated = source + "# Explain why this exists.\n"
    assert find_disallowed_comments(mutated) == [(count_lines(mutated), "# Explain why this exists.")]


@pytest.mark.parametrize(("relative_path", "ceiling"), BASELINE_LINE_CEILINGS.items())
def test_lines_past_baseline_fail_ceiling_check(relative_path: str, ceiling: int) -> None:
    source = _read_module(relative_path)
    assert source.endswith("\n")
    mutated = source + "\n" * (ceiling - count_lines(source))
    assert count_lines(mutated) == ceiling
    assert not _under_ceiling(mutated, ceiling)
