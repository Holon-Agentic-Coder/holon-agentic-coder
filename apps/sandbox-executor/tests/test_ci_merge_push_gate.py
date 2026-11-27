"""Hermetic tests for the merge-push route of the bookkeeping gate: the evidence, its rules, the scan and fail-open.

Every API answer is served by ``tests.ci_bookkeeping_fakes.FakeGitHub``; nothing here reaches the network. The usual
scene is run 50 of ``test-unit.yml`` for the push of ``NEW_HEAD_SHA``, which merges the main commit ``BASE_HEAD_SHA``
into the previous head ``PREVIOUS_HEAD_SHA``. The job really succeeded on the previous head (run 49) and on the main
commit (push run 70). Every test runs in an empty working directory, so the local checkout holds no file unless the
test writes one.
"""

from __future__ import annotations

import ast
import json
import sys
import urllib.error
from collections.abc import Mapping
from dataclasses import replace
from email.message import Message
from pathlib import Path

import pytest

from scripts.ci_bookkeeping_api import GateClient, GateContext, context_from, load_event
from scripts.ci_bookkeeping_chain import CHAIN_RULES, Decision
from scripts.ci_bookkeeping_gate import RUN_RULES, SKIP_RULES, main, safe_decide
from scripts.ci_main_push_gate import MAIN_PUSH_RUN_RULES, MAIN_PUSH_SKIP_RULES
from scripts.ci_merge_push_gate import (
    CONFLICT,
    MERGE_PUSH_RUN_RULES,
    MERGE_PUSH_SKIP_RULES,
    MergeTrees,
    changed_paths,
    config_coupling,
    conflict_path,
    expected_entry,
    is_config_path,
    overlap,
)
from scripts.ci_merge_push_imports import MAX_BLOB_READS, imports_of, is_test_pair, module_names
from tests.ci_bookkeeping_fakes import (
    BASE_HEAD_SHA,
    BASE_PUSH_RUN,
    BRANCH,
    MERGE_BASE_SHA,
    NEW_HEAD_SHA,
    PREVIOUS_HEAD_SHA,
    PREVIOUS_RUN,
    REPO,
    TOKEN,
    TREE_IDS,
    WORKFLOW,
    FakeGitHub,
    blob_id,
    cancelled_job,
    comparison,
    gate_skipped_job,
    job,
    legacy_success_job,
    merge_base_answer,
    merge_scene,
    real_success_job,
)

ROOT = Path(__file__).resolve().parents[3]
OLDER_SHA = "d" * 40
OTHER_SHA = "9" * 40
CURRENT_RUN = 50
CODE_FILE = "scripts/ci_x.py"
ENGINE = "apps/sandbox-executor/src/sandbox_executor/pipeline/engine.py"
ROWS = "apps/sandbox-executor/src/sandbox_executor/ledger/rows.py"
BASE_FILES = {
    CODE_FILE: "VALUE = 1\n",
    ENGINE: "import json\n",
    ROWS: "import json\n",
    "docs/a.md": "a\n",
    "executions/E-0.md": "old\n",
}
PR_CHANGES: dict[str, str | None] = {
    CODE_FILE: "VALUE = 2\n",
    "executions/E-1.md": "pull request report\n",
    "holon-knowledge/ledger/pending/plans/I-1.jsonl": "{}\n",
    "plans/I-1-intent/P-1.md": "plan\n",
}
BASE_CHANGES: dict[str, str | None] = {
    ENGINE: "import os\n",
    "docs/a.md": "b\n",
    "executions/E-2.md": "main report\n",
    "holon-knowledge/ledger/pending/plans/I-2.jsonl": "{}\n",
}
PUSH_RUN_ID = 1000 + BASE_PUSH_RUN
PREVIOUS_RUN_ID = 1000 + PREVIOUS_RUN


@pytest.fixture(autouse=True)
def _empty_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


def apply(files: Mapping[str, str], changes: Mapping[str, str | None]) -> dict[str, str]:
    """Return ``files`` with ``changes`` applied; a ``None`` content deletes the path."""
    result = dict(files)
    for path, text in changes.items():
        if text is None:
            result.pop(path, None)
        else:
            result[path] = text
    return result


def scene(
    pr: Mapping[str, str | None] | None = None,
    base: Mapping[str, str | None] | None = None,
    merged: Mapping[str, str | None] | None = None,
    too_large: bool = False,
) -> FakeGitHub:
    """Return the merge scene; ``merged`` edits the clean merge result, ``too_large`` caps the push's compare."""
    pr_changes = PR_CHANGES if pr is None else pr
    base_changes = BASE_CHANGES if base is None else base
    clean = apply(apply(BASE_FILES, base_changes), pr_changes)
    fake = merge_scene(
        BASE_FILES, apply(BASE_FILES, pr_changes), apply(BASE_FILES, base_changes), apply(clean, merged or {})
    )
    pushed = [f"apps/f{index}.py" for index in range(300)] if too_large else [CODE_FILE]
    fake.compares[(PREVIOUS_HEAD_SHA, NEW_HEAD_SHA)] = comparison(*pushed)
    return fake


def context(**overrides: object) -> GateContext:
    base = GateContext(
        event_name="pull_request",
        action="synchronize",
        label="",
        before=PREVIOUS_HEAD_SHA,
        head_sha=NEW_HEAD_SHA,
        branch=BRANCH,
        run_number=CURRENT_RUN,
        run_attempt=1,
        workflow_file=WORKFLOW,
        job_name="unit",
        os_name="ubuntu-latest",
        base_ref="main",
    )
    return replace(base, **overrides)


def gate(fake: FakeGitHub, gate_context: GateContext | None = None) -> Decision:
    return safe_decide(gate_context or context(), GateClient(REPO, TOKEN, fake))


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.github.com/x", code, "error", Message(), None)


def skip_detail(count: int = 4) -> str:
    return (
        f"{count} base-side paths merged from {BASE_HEAD_SHA[:12]}; "
        f"real successes on {PREVIOUS_HEAD_SHA[:12]} and {BASE_HEAD_SHA[:12]}"
    )


# --- the skip ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("too_large", [False, True], ids=["non-bookkeeping-change", "diff-too-large"])
def test_clean_two_parent_main_merge_with_real_successes_skips(too_large: bool) -> None:
    fake = scene(too_large=too_large)

    assert gate(fake) == Decision(True, "merge-push-clean-main-merge", skip_detail())
    assert len(fake.urls) <= 15
    assert len(fake.calls("git/blobs/")) == 2


def test_bookkeeping_files_in_the_same_directories_on_both_sides_still_skip() -> None:
    fake = scene(
        pr={**PR_CHANGES, "plans/I-1-intent/P-2.md": "x\n"}, base={**BASE_CHANGES, "plans/I-1-intent/P-3.md": "y\n"}
    )

    assert gate(fake).rule == "merge-push-clean-main-merge"


def test_older_real_success_over_bookkeeping_pushes_skips() -> None:
    fake = scene()
    fake.jobs[PREVIOUS_RUN_ID] = [gate_skipped_job()]
    fake.add_run(48, OLDER_SHA, real_success_job())
    fake.compares[(OLDER_SHA, PREVIOUS_HEAD_SHA)] = comparison("plans/x.md")

    assert gate(fake).rule == "merge-push-clean-main-merge"


# --- (a) conflict-resolution edits ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "merged",
    [
        pytest.param({ENGINE: "import sys\n"}, id="edit differs from both parents"),
        pytest.param({ENGINE: "import json\n"}, id="reverts a base-side change"),
        pytest.param({"docs/new.md": "x\n"}, id="adds a file neither parent has"),
        pytest.param({"executions/E-2.md": None}, id="drops a bookkeeping file"),
    ],
)
def test_merge_result_that_is_not_the_three_way_merge_runs(merged: dict[str, str | None]) -> None:
    decision = gate(scene(merged=merged))

    assert decision == Decision(False, "merge-push-conflict-resolution", next(iter(merged)))


def test_path_changed_differently_on_both_sides_runs_even_when_the_head_takes_one_side() -> None:
    fake = scene(pr={**PR_CHANGES, "docs/a.md": "pr\n"})

    assert gate(fake) == Decision(False, "merge-push-conflict-resolution", "docs/a.md")


# --- (b) overlapping changes ----------------------------------------------------------------------------------------


def test_same_code_path_changed_identically_on_both_sides_runs() -> None:
    fake = scene(base={**BASE_CHANGES, CODE_FILE: "VALUE = 2\n"})

    assert gate(fake) == Decision(False, "merge-push-overlapping-change", CODE_FILE)


def test_code_files_in_the_same_directory_run() -> None:
    fake = scene(base={**BASE_CHANGES, "scripts/ci_y.py": "VALUE = 3\n"})

    assert gate(fake) == Decision(False, "merge-push-overlapping-change", "scripts/")


def test_root_level_files_on_both_sides_share_the_root_directory() -> None:
    fake = scene(pr={**PR_CHANGES, "Makefile": "a\n"}, base={**BASE_CHANGES, "pyproject.toml": "b\n"})

    assert gate(fake) == Decision(False, "merge-push-overlapping-change", "./")


@pytest.mark.parametrize(
    ("pr", "base", "detail"),
    [
        (PR_CHANGES, {**BASE_CHANGES, "uv.lock": "version = 2\n"}, "uv.lock"),
        (PR_CHANGES, {**BASE_CHANGES, "pyproject.toml": "[tool.ruff]\n"}, "pyproject.toml"),
        (
            PR_CHANGES,
            {**BASE_CHANGES, "apps/sandbox-executor/pyproject.toml": "b\n"},
            "apps/sandbox-executor/pyproject.toml",
        ),
        ({**PR_CHANGES, ".github/workflows/test-unit.yml": "a\n"}, BASE_CHANGES, ".github/workflows/test-unit.yml"),
        ({**PR_CHANGES, "Makefile": "a\n"}, {"docs/a.md": "b\n"}, "Makefile"),
    ],
)
def test_configuration_change_on_one_side_with_any_change_on_the_other_runs(
    pr: dict[str, str | None], base: dict[str, str | None], detail: str
) -> None:
    fake = scene(pr=pr, base=base)

    assert gate(fake) == Decision(False, "merge-push-config-change", detail)


# --- (c) no real success on the previous head -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("previous_job", "rule"),
    [
        (job(conclusion="failure"), "previous-run-not-successful"),
        (gate_skipped_job(), "previous-run-missing"),
        (legacy_success_job(), "previous-run-unverified"),
    ],
)
def test_previous_head_without_a_real_success_runs_under_the_chain_rule(previous_job: dict, rule: str) -> None:
    fake = scene()
    fake.jobs[PREVIOUS_RUN_ID] = [previous_job]

    assert gate(fake).rule == rule
    assert fake.calls("git/trees/") == []


def test_previous_head_without_any_run_runs() -> None:
    fake = scene()
    fake.runs = [run for run in fake.runs if run["event"] == "push"]

    assert gate(fake).rule == "previous-run-missing"


def test_older_real_success_with_a_code_change_since_runs() -> None:
    fake = scene()
    fake.jobs[PREVIOUS_RUN_ID] = [gate_skipped_job()]
    fake.add_run(48, OLDER_SHA, real_success_job())
    fake.compares[(OLDER_SHA, PREVIOUS_HEAD_SHA)] = comparison("plans/x.md", CODE_FILE)

    assert gate(fake) == Decision(False, "non-bookkeeping-change", CODE_FILE)


# --- (d) no real success on the merged-in main commit ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("base_job", "rule"),
    [
        (job(status="in_progress", conclusion=None), "merge-push-base-run-not-finished"),
        (job(conclusion="failure"), "merge-push-base-run-not-successful"),
        (gate_skipped_job(), "merge-push-base-run-unverified"),
        (legacy_success_job(), "merge-push-base-run-unverified"),
        (job(name="stress"), "merge-push-base-run-missing"),
    ],
)
def test_main_commit_without_a_real_success_runs(base_job: dict, rule: str) -> None:
    fake = scene()
    fake.jobs[PUSH_RUN_ID] = [base_job]

    assert gate(fake).rule == rule
    assert fake.calls("git/trees/") == []


def test_main_commit_without_a_push_run_runs() -> None:
    fake = scene()
    fake.runs = [run for run in fake.runs if run["event"] != "push"]

    assert gate(fake) == Decision(
        False, "merge-push-base-run-missing", f"no single matching job on {BASE_HEAD_SHA[:12]}"
    )


def test_push_run_of_another_branch_does_not_count() -> None:
    fake = scene()
    fake.runs[-1]["head_branch"] = "release"

    assert gate(fake).rule == "merge-push-base-run-missing"


# --- (e) to (g) the shape of the new head ---------------------------------------------------------------------------


def test_non_merge_push_keeps_the_original_decision() -> None:
    fake = scene()
    fake.commits[NEW_HEAD_SHA] = (TREE_IDS["merged"], [PREVIOUS_HEAD_SHA])

    assert gate(fake) == Decision(False, "non-bookkeeping-change", CODE_FILE)
    assert fake.calls("actions/") == []


def test_three_parent_merge_runs() -> None:
    fake = scene()
    fake.commits[NEW_HEAD_SHA] = (TREE_IDS["merged"], [PREVIOUS_HEAD_SHA, BASE_HEAD_SHA, OTHER_SHA])

    assert gate(fake) == Decision(False, "merge-push-not-two-parents", "3 parents")


def test_first_parent_that_is_not_the_previous_head_runs() -> None:
    fake = scene()
    fake.commits[NEW_HEAD_SHA] = (TREE_IDS["merged"], [BASE_HEAD_SHA, PREVIOUS_HEAD_SHA])

    assert gate(fake).rule == "merge-push-first-parent-not-previous-head"
    assert fake.calls("actions/") == []


def test_event_without_a_base_branch_runs() -> None:
    assert gate(scene(), context(base_ref="")).rule == "merge-push-incomplete-event"


# --- (h) API errors and truncation ----------------------------------------------------------------------------------


def test_http_error_on_a_tree_is_an_api_error_with_the_status_only() -> None:
    fake = scene()
    fake.errors["git/trees/"] = http_error(500)

    assert gate(fake) == Decision(False, "merge-push-api-error", "HTTPError 500")


def test_error_on_the_parents_read_keeps_the_original_decision() -> None:
    fake = scene()
    fake.errors[f"git/commits/{NEW_HEAD_SHA}"] = OSError("network down")

    assert gate(fake) == Decision(False, "non-bookkeeping-change", CODE_FILE)


def test_truncated_tree_runs() -> None:
    fake = scene()
    fake.tree_listings[TREE_IDS["merged_in"]]["truncated"] = True

    assert gate(fake) == Decision(False, "merge-push-tree-truncated", f"tree {TREE_IDS['merged_in'][:12]}")


def test_compare_without_a_merge_base_runs() -> None:
    fake = scene()
    answer = merge_base_answer(MERGE_BASE_SHA, TREE_IDS["base"], TREE_IDS["previous"])
    fake.merge_bases[(PREVIOUS_HEAD_SHA, BASE_HEAD_SHA)] = {**answer, "merge_base_commit": None}

    assert gate(fake).rule == "merge-push-no-merge-base"


@pytest.mark.parametrize(
    ("part", "body", "detail"),
    [
        ("git/blobs/", {"content": "!!!", "encoding": "base64"}, "Error"),
        ("git/blobs/", {"content": "eA==", "encoding": "utf-8"}, "ValueError"),
        ("git/trees/", {"tree": [{"path": "a", "type": "blob"}], "truncated": False}, "ValueError"),
        (
            f"compare/{PREVIOUS_HEAD_SHA}...{BASE_HEAD_SHA}",
            {"merge_base_commit": {"sha": MERGE_BASE_SHA}},
            "ValueError",
        ),
    ],
)
def test_answer_of_an_unexpected_shape_is_an_api_error(part: str, body: dict, detail: str) -> None:
    fake = scene(pr={**PR_CHANGES, ROWS: "import os\n"})
    fake.bodies[part] = json.dumps(body).encode()

    assert gate(fake) == Decision(False, "merge-push-api-error", detail)


# --- (i) to (k) the relation scan -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pr_rows", "base_engine"),
    [
        pytest.param("VALUE = 2\n", "from sandbox_executor.ledger import rows\n", id="base imports the pr module"),
        pytest.param(
            "from sandbox_executor.pipeline.engine import run\n", "import os\n", id="pr imports the base module"
        ),
        pytest.param("from ..pipeline import engine\n", "import os\n", id="relative import"),
        pytest.param("import sandbox_executor\n", "import os\n", id="a dotted prefix"),
    ],
)
def test_files_that_import_each_other_in_the_same_root_run(pr_rows: str, base_engine: str) -> None:
    fake = scene(pr={**PR_CHANGES, ROWS: pr_rows}, base={**BASE_CHANGES, ENGINE: base_engine})

    assert gate(fake) == Decision(False, "merge-push-related-change", f"{ROWS} and {ENGINE}")


def test_test_file_of_a_base_side_module_runs() -> None:
    test_file = "apps/sandbox-executor/tests/test_engine.py"
    fake = scene(pr={**PR_CHANGES, test_file: "VALUE = 1\n"})

    assert gate(fake) == Decision(False, "merge-push-related-change", f"{test_file} and {ENGINE}")


def test_test_under_apps_of_a_pr_script_runs() -> None:
    test_file = "apps/sandbox-executor/tests/test_ci_gate.py"
    fake = scene(base={**BASE_CHANGES, test_file: "from scripts.ci_x import VALUE\n"})

    assert gate(fake) == Decision(False, "merge-push-related-change", f"{CODE_FILE} and {test_file}")


def test_unrelated_python_files_in_the_related_roots_skip_after_reading_their_blobs() -> None:
    fake = scene(pr={**PR_CHANGES, ROWS: "import os\nimport sandbox_executor.ledger.other\n"})

    assert gate(fake).rule == "merge-push-clean-main-merge"
    assert len(fake.calls("git/blobs/")) == 3


@pytest.mark.parametrize(
    ("changes", "detail"),
    [
        ({"apps/sandbox-executor/tests/data.json": "{}\n"}, "apps/sandbox-executor/tests/data.json"),
        ({ROWS: "def (:\n"}, f"{ROWS} does not parse"),
        ({"apps/sandbox-executor/tests/conftest.py": "VALUE = 1\n"}, "apps/sandbox-executor/tests/conftest.py"),
    ],
)
def test_change_that_cannot_be_analysed_runs(changes: dict[str, str], detail: str) -> None:
    fake = scene(pr={**PR_CHANGES, **changes})

    assert gate(fake) == Decision(False, "merge-push-unanalysable-change", detail)


@pytest.mark.parametrize(("mode", "kind"), [("120000", "blob"), ("160000", "commit")])
def test_symlink_or_submodule_in_the_same_root_runs(mode: str, kind: str) -> None:
    fake = scene(pr={**PR_CHANGES, ROWS: "import os\n"})
    for tree_id in (TREE_IDS["previous"], TREE_IDS["merged"]):
        for entry in fake.tree_listings[tree_id]["tree"]:
            if entry["path"] == ROWS:
                entry.update(mode=mode, type=kind)

    assert gate(fake) == Decision(False, "merge-push-unanalysable-change", ROWS)


def test_more_files_than_the_blob_read_budget_runs() -> None:
    many = {f"apps/sandbox-executor/src/sandbox_executor/pipeline/m{index}.py": "VALUE = 1\n" for index in range(40)}
    fake = scene(pr={**PR_CHANGES, ROWS: "import os\n"}, base={**BASE_CHANGES, **many})

    assert gate(fake) == Decision(False, "merge-push-scan-too-large", f"more than {MAX_BLOB_READS} blob reads")
    assert len(fake.calls("git/blobs/")) == MAX_BLOB_READS


def test_local_file_with_the_same_blob_id_is_read_without_a_blob_request(tmp_path: Path) -> None:
    fake = scene(pr={**PR_CHANGES, ROWS: "import os\n"})
    for path, text in ((ROWS, "import os\n"), (CODE_FILE, PR_CHANGES[CODE_FILE]), (ENGINE, "stale local copy\n")):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")

    engine_blob = blob_id(b"import os\n")
    assert gate(fake).rule == "merge-push-clean-main-merge"
    assert fake.calls("git/blobs/") == [f"https://api.github.com/repos/{REPO}/git/blobs/{engine_blob}"]


# --- hygiene --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides", [{"job_name": "hygiene"}, {"workflow_file": "test-hygiene.yml"}], ids=["job", "workflow"]
)
def test_hygiene_is_never_skipped_and_asks_for_no_git_object(overrides: dict[str, str]) -> None:
    fake = scene()
    fake.jobs[PREVIOUS_RUN_ID] = [real_success_job("hygiene")]
    fake.jobs[PUSH_RUN_ID] = [real_success_job("hygiene")]

    decision = gate(fake, context(os_name="", **overrides))

    assert (decision.skip, decision.rule) == (False, "merge-push-hygiene-never-skipped")
    assert fake.calls("git/") == []


# --- the rule sets --------------------------------------------------------------------------------------------------


def test_one_skip_rule_and_names_disjoint_from_the_other_routes() -> None:
    names = MERGE_PUSH_RUN_RULES + MERGE_PUSH_SKIP_RULES
    assert MERGE_PUSH_SKIP_RULES == ("merge-push-clean-main-merge",)
    assert len(set(names)) == len(names)
    assert all(name.startswith("merge-push-") for name in names)
    assert set(names).isdisjoint(RUN_RULES + SKIP_RULES + MAIN_PUSH_RUN_RULES + MAIN_PUSH_SKIP_RULES + CHAIN_RULES)


def test_sweep_of_scenes_skips_only_under_the_skip_rule() -> None:
    scenes = [
        scene(),
        scene(merged={ENGINE: "x = 1\n"}),
        scene(base={**BASE_CHANGES, CODE_FILE: "VALUE = 2\n"}),
        scene(pr={**PR_CHANGES, ROWS: "from sandbox_executor.pipeline import engine\n"}),
        scene(pr={**PR_CHANGES, ROWS: "def (:\n"}),
    ]
    decisions = [gate(fake) for fake in scenes]

    known = set(MERGE_PUSH_RUN_RULES + MERGE_PUSH_SKIP_RULES + RUN_RULES + SKIP_RULES + CHAIN_RULES)
    assert all(decision.rule in known for decision in decisions)
    assert all(decision.skip == (decision.rule in MERGE_PUSH_SKIP_RULES + SKIP_RULES) for decision in decisions)
    assert [decision.skip for decision in decisions] == [True, False, False, False, False]


# --- the command ----------------------------------------------------------------------------------------------------


def test_main_prints_one_line_writes_skip_true_and_keeps_the_token_out_of_urls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {
        "action": "synchronize",
        "before": PREVIOUS_HEAD_SHA,
        "after": NEW_HEAD_SHA,
        "pull_request": {"number": 7, "head": {"sha": NEW_HEAD_SHA, "ref": BRANCH}, "base": {"ref": "main"}},
    }
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(payload), encoding="utf-8")
    environ = {
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(event_file),
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_TOKEN": TOKEN,
        "GITHUB_RUN_NUMBER": str(CURRENT_RUN),
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_WORKFLOW_REF": f"{REPO}/.github/workflows/{WORKFLOW}@refs/pull/7/merge",
        "GITHUB_OUTPUT": str(tmp_path / "output.txt"),
    }
    fake = scene()

    assert context_from(environ, load_event(str(event_file)), "unit", "ubuntu-latest") == context()
    assert main(["--job-name", "unit", "--os", "ubuntu-latest"], environ, fake) == 0

    assert (tmp_path / "output.txt").read_text(encoding="utf-8") == "skip=true\n"
    assert capsys.readouterr().out == (
        f"bookkeeping-gate: decision=SKIP rule=merge-push-clean-main-merge detail={skip_detail()}\n"
    )
    assert fake.auth == [f"Bearer {TOKEN}"] * len(fake.urls)
    assert all(TOKEN not in url for url in fake.urls)


# --- Python 3.9 and the standard library ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module",
    [
        "ci_merge_push_gate",
        "ci_merge_push_imports",
        "ci_bookkeeping_api",
        "ci_bookkeeping_chain",
        "ci_bookkeeping_gate",
        "ci_main_push_gate",
    ],
)
def test_gate_module_is_python_3_9_syntax_and_standard_library_only(module: str) -> None:
    syntax = ast.parse((ROOT / "scripts" / f"{module}.py").read_text(encoding="utf-8"), feature_version=(3, 9))

    imported = [alias.name for node in syntax.body if isinstance(node, ast.Import) for alias in node.names]
    imported += [node.module or "" for node in syntax.body if isinstance(node, ast.ImportFrom)]
    for name in imported:
        assert name.startswith("scripts.") or name.partition(".")[0] in sys.stdlib_module_names, name
    for node in syntax.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            bitor = [
                sub for sub in ast.walk(node.value) if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr)
            ]
            assert not bitor, f"{module}.py evaluates PEP 604 union at module level"


# --- pure helpers ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("uv.lock", True),
        ("pyproject.toml", True),
        ("Makefile", True),
        ("apps/sandbox-executor/pyproject.toml", True),
        (".github/workflows/test-unit.yml", True),
        ("apps/sandbox-executor/src/pyproject.toml", False),
        ("docs/uv.lock", False),
        ("scripts/ci_x.py", False),
    ],
)
def test_is_config_path(path: str, expected: bool) -> None:
    assert is_config_path(path) is expected


def test_config_coupling_needs_a_change_on_the_other_side() -> None:
    assert config_coupling(["uv.lock"], []) is None
    assert config_coupling([], ["uv.lock"]) is None
    assert config_coupling(["scripts/x.py"], ["uv.lock"]) == "uv.lock"


A = ("100644", "blob", "a" * 40)
B = ("100644", "blob", "b" * 40)
C = ("100644", "blob", "c" * 40)


@pytest.mark.parametrize(
    ("base", "previous", "merged_in", "expected"),
    [
        (A, A, B, B),
        (A, B, A, B),
        (A, B, B, B),
        (A, B, C, CONFLICT),
        (None, None, B, B),
        (None, B, None, B),
        (A, None, A, None),
        (A, A, None, None),
        (A, None, B, CONFLICT),
        (None, B, C, CONFLICT),
    ],
)
def test_expected_entry_is_the_file_level_three_way_merge(base, previous, merged_in, expected) -> None:
    assert expected_entry(base, previous, merged_in) == expected


def test_conflict_path_names_the_first_path_that_is_not_the_merge() -> None:
    clean = MergeTrees({"x": A, "y": A}, {"x": B, "y": A}, {"x": A, "y": C}, {"x": B, "y": C})
    assert conflict_path(clean) is None
    assert conflict_path(replace(clean, merged={"x": B, "y": C, "z": A})) == "z"
    assert conflict_path(replace(clean, merged_in={"x": C, "y": C})) == "x"


def test_changed_paths_lists_added_removed_and_modified_paths_sorted() -> None:
    assert changed_paths({"b": A, "a": A, "c": A}, {"a": B, "c": A, "d": A}) == ["a", "b", "d"]


@pytest.mark.parametrize(
    ("pr", "base", "expected"),
    [
        (["a/x.py"], ["a/x.py"], "a/x.py"),
        (["a/x.py"], ["a/y.py"], "a/"),
        (["a/x.py"], ["a/b/y.py"], None),
        (["x.py"], ["y.toml"], "./"),
        ([], ["a/y.py"], None),
    ],
)
def test_overlap_finds_a_shared_path_or_parent_directory(pr: list[str], base: list[str], expected: str | None) -> None:
    assert overlap(pr, base) == expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (ENGINE, ("engine", "sandbox_executor.pipeline.engine")),
        ("apps/sandbox-executor/src/sandbox_executor/pipeline/__init__.py", ("pipeline", "sandbox_executor.pipeline")),
        ("scripts/ci_x.py", ("ci_x", "scripts.ci_x")),
        ("apps/sandbox-executor/tests/ci_bookkeeping_fakes.py", ("ci_bookkeeping_fakes", "tests.ci_bookkeeping_fakes")),
    ],
)
def test_module_names_cover_the_src_layout_scripts_tests_and_packages(path: str, expected: tuple[str, ...]) -> None:
    assert module_names(path) == expected


def test_imports_of_resolves_relative_imports_and_rejects_bad_source() -> None:
    names = imports_of(ROWS, b"import os.path\nfrom . import store\nfrom ..pipeline.engine import run\n")
    assert names is not None
    assert {"os.path", "sandbox_executor.ledger.store", "sandbox_executor.pipeline.engine.run"} <= names
    assert imports_of(ROWS, b"def (:\n") is None
    assert imports_of(ROWS, b"x = 1\x00\n") is None


def test_is_test_pair_matches_test_prefix_in_either_order() -> None:
    assert is_test_pair("a/tests/test_engine.py", "b/src/engine.py")
    assert is_test_pair("b/src/engine.py", "a/tests/test_engine.py")
    assert not is_test_pair("a/tests/test_engines.py", "b/src/engine.py")


POST_SHA = "7" * 40
MID_POST_SHA = "6" * 40


def stacked_merge_scene(merge_job: dict[str, object] | None = None) -> FakeGitHub:
    fake = scene()
    fake.commits[PREVIOUS_HEAD_SHA] = (TREE_IDS["previous"], [OLDER_SHA])
    fake.commits[POST_SHA] = (TREE_IDS["merged"], [NEW_HEAD_SHA])
    fake.compares[(OLDER_SHA, PREVIOUS_HEAD_SHA)] = comparison("plans/I-1-intent/P-1.md")
    fake.compares[(NEW_HEAD_SHA, POST_SHA)] = comparison("executions/E-1.md")
    fake.compares[(OLDER_SHA, POST_SHA)] = comparison(CODE_FILE, "executions/E-1.md")
    fake.runs = [entry for entry in fake.runs if entry["event"] == "push"]
    fake.add_run(47, OLDER_SHA, real_success_job())
    fake.add_run(48, PREVIOUS_HEAD_SHA, cancelled_job())
    fake.add_run(49, NEW_HEAD_SHA, merge_job or cancelled_job())
    return fake


@pytest.mark.parametrize("before", [NEW_HEAD_SHA, OLDER_SHA], ids=["before-is-merge", "before-is-green-head"])
def test_stacked_bookkeeping_chain_over_cancelled_merge_skips(before: str) -> None:
    fake = stacked_merge_scene()

    decision = gate(fake, context(before=before, head_sha=POST_SHA))

    assert decision.skip is True
    assert decision.rule == "merge-push-clean-main-merge"
    assert f"real successes on {OLDER_SHA[:12]} and {BASE_HEAD_SHA[:12]}" in decision.detail


def test_stacked_bookkeeping_commit_after_gate_skipped_merge_skips() -> None:
    fake = stacked_merge_scene(merge_job=gate_skipped_job())
    fake.jobs[1048] = [gate_skipped_job()]

    decision = gate(fake, context(before=NEW_HEAD_SHA, head_sha=POST_SHA))

    assert decision.skip is True
    assert decision.rule == "merge-push-clean-main-merge"


def test_non_bookkeeping_change_after_merge_forces_full_run() -> None:
    fake = stacked_merge_scene()
    fake.compares[(NEW_HEAD_SHA, POST_SHA)] = comparison("executions/E-1.md", CODE_FILE)

    decision = gate(fake, context(before=NEW_HEAD_SHA, head_sha=POST_SHA))

    assert decision == Decision(False, "non-bookkeeping-change", CODE_FILE)


def test_non_bookkeeping_intermediate_commit_after_merge_forces_full_run() -> None:
    fake = stacked_merge_scene()
    fake.commits[MID_POST_SHA] = (TREE_IDS["merged"], [NEW_HEAD_SHA])
    fake.commits[POST_SHA] = (TREE_IDS["merged"], [MID_POST_SHA])
    fake.compares[(NEW_HEAD_SHA, MID_POST_SHA)] = comparison(CODE_FILE)
    fake.compares[(MID_POST_SHA, POST_SHA)] = comparison("executions/E-1.md")
    fake.compares[(NEW_HEAD_SHA, POST_SHA)] = comparison(CODE_FILE, "executions/E-1.md")

    decision = gate(fake, context(before=NEW_HEAD_SHA, head_sha=POST_SHA))

    assert decision == Decision(False, "non-bookkeeping-change", CODE_FILE)


def test_non_bookkeeping_change_before_merge_forces_full_run() -> None:
    fake = stacked_merge_scene()
    fake.compares[(OLDER_SHA, PREVIOUS_HEAD_SHA)] = comparison(CODE_FILE)

    decision = gate(fake, context(before=NEW_HEAD_SHA, head_sha=POST_SHA))

    assert decision == Decision(False, "non-bookkeeping-change", CODE_FILE)


@pytest.mark.parametrize(
    "overrides", [{"job_name": "hygiene"}, {"workflow_file": "test-hygiene.yml"}], ids=["job", "workflow"]
)
def test_hygiene_is_never_skipped_on_stacked_merge_bookkeeping_chain(overrides: dict[str, str]) -> None:
    fake = stacked_merge_scene()

    decision = gate(fake, context(before=NEW_HEAD_SHA, head_sha=POST_SHA, os_name="", **overrides))

    assert (decision.skip, decision.rule) == (False, "merge-push-hygiene-never-skipped")


def test_single_push_with_pre_merge_merge_and_post_merge_bookkeeping_skips() -> None:
    fake = stacked_merge_scene()
    fake.runs = [entry for entry in fake.runs if entry["head_sha"] not in (PREVIOUS_HEAD_SHA, NEW_HEAD_SHA)]

    decision = gate(fake, context(before=OLDER_SHA, head_sha=POST_SHA))

    assert decision.skip is True
    assert decision.rule == "merge-push-clean-main-merge"
    assert f"real successes on {OLDER_SHA[:12]} and {BASE_HEAD_SHA[:12]}" in decision.detail


def test_api_error_during_pre_merge_bookkeeping_chain_check_is_merge_push_api_error() -> None:
    fake = stacked_merge_scene()
    fake.errors[f"git/commits/{PREVIOUS_HEAD_SHA}"] = http_error(500)

    assert gate(fake, context(before=OLDER_SHA, head_sha=POST_SHA)) == Decision(
        False, "merge-push-api-error", "HTTPError 500"
    )
