# Execution Record: E-1794192393-7928976a-claude-agent-claude-opus-5-5

## Summary

Implementation of plan `P-1794192045-c23d4516-claude-agent-claude-opus-5-5`: split
`apps/sandbox-executor/src/sandbox_executor/flow.py` into the `sandbox_executor.flow` package (move only). In progress.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: Map definitions, dependency graph, patch lookups and baselines

- Source blob for the snapshot: `HEAD:apps/sandbox-executor/src/sandbox_executor/flow.py` =
  `aa8d5f19a171ce97e8dd5ecf598a57fe86f976c3` (6,159 lines). The branch was not rebased: the execution protocol forbids
  it, and the branch is the direct child of the plan commit.
- `ast` pass: 124 top-level `def`/`class` nodes (confirmed), plus 26 module-level assignments (including `logger` and
  the `CHECKPOINT_MIGRATION_REGISTRY[(0, 1)]` registration), so 150 statements. No `globals()` or `sys.modules` lookups.
- The manifest follows the intent's clusters, with these measured adjustments:
  - `_support_legacy_repo_dir` goes to `context.py`, because it decorates `FlowContext` and is used nowhere else.
    Leaving it in `checkpoint.py` would create a `context <-> checkpoint` cycle.
  - `CURRENT_CONTEXT_SCHEMA_VERSION` (used by `context` and `checkpoint`) and `CI_REGISTRATION_POLL_SECONDS` (used by
    `github/ci` and `review/loop`) go to `constants.py`. Every other constant has a single user and goes with it:
    `EXECUTOR_GIT_ENV` to `git_refs`, `FAILED_EXECUTION_STATUSES` to `stages/execute`, `POSTING_ERROR_CONTEXT_MAX_CHARS`
    and `_SEVERITY_RANK` to `review/records`, `TESTS_NOT_MEASURED_ERROR` to `stages/review`. The two constants with no
    user inside the engine go with their subject: `REVIEW_GIT_ENV` to `stages/review` and `HOST_REVIEW_REFUSAL` to
    `review/workspace`.
- SCC check: after these adjustments there is exactly one cycle,
  `stages/decomposition -> pipeline -> stages/execute -> stages/decomposition` (`_run_decomposed_plan` uses
  `PipelineEngine`). It is broken by late binding in `flow/__init__.py`, plus a `TYPE_CHECKING` import in
  `decomposition.py` so ruff `F821` stays clean without a `# noqa`.
- Patch lookup map: 45 distinct `"sandbox_executor.flow.<name>"` strings plus 14 `setattr`/`patch.object` names. The
  per-site map is in Step 4.
- Baseline from a temporary worktree of `HEAD` (`/tmp/flowsplit/base`): 3,131 collected node IDs, 337 of them in
  `test_flow.py` (280 test functions once parametrisation is folded). The 15 affected files gave 1774 passed in 6.48s
  (`-n auto --dist worksteal`).
- Environment note: the sandbox had no `uv` (installed with `pip install --user uv`), and `PYTHONPATH=/home/holon/src`
  points at the executor's own copy of `sandbox_executor`, which shadows the workspace. Every command below runs with
  `PYTHONPATH` unset.

### Step 2: Generate the `flow/` package and remove `flow.py`

- A scratch generator (`/tmp/flowsplit/generate.py` with `manifest.py`, outside the repository and not committed) copies
  each statement's exact source slice, with its directly preceding comments, into its target module in original order.
  The three-line `# ---` section banners are dropped, because each module docstring now names its section.
- Each module gets a docstring, `from __future__ import annotations`, the imports it loads (copied from the `flow.py`
  header, plus absolute `sandbox_executor.flow.<sub>` imports), and `logger = logging.getLogger(__name__)` where it
  logs. Then `ruff check --fix --select F401,I001,RUF022` and `ruff format` normalise the imports. No body was touched.
- 23 modules plus four `__init__.py` files. `flow/__init__.py` re-exports all 148 bound names and the five imported
  names reached as `flow.<name>` (`SKIP_PUSH_DISPATCH_ERROR`, `build_pr_review_consensus_report`, `parse_metrics`,
  `redact_agent_secrets`, `run_git`) through an explicit `__all__` (153 names). It then binds
  `stages.decomposition.PipelineEngine` late.
- `flow.py` was removed with `git rm`. The 19 `("sandbox_executor/flow.py", codes)` entries in
  `tests/test_complexity_gate.py` `BASELINE_SNAPSHOT` were re-keyed to the submodule carrying each suppression, with the
  same multiset of codes. The baseline counts are untouched (the file holds `184` and `90`, not the `187` and `91` the
  plan quoted).
- Checks: imports of `sandbox_executor.flow`, `sandbox_executor.cli`, `sandbox_executor.entrypoint.review_loop` and
  `sandbox_executor.review_target` succeed. `ruff check .` and `ruff format --check .` are clean, and
  `test_complexity_gate.py` passes (5 passed). A preview AST comparison found 0 missing and 0 differing statements.

### Step 3: AST move proof, committed snapshot and re-export tests

- `tests/flow/move_proof.py` holds `definition_digests` (sha256 of `ast.dump` without attributes, keyed by qualified
  name: top-level definitions, class members as `Class.member`, assignments as `<const>:target`), the package parser and
  the snapshot loader. `/tmp/flowsplit/make_snapshot.py` (scratch, not committed) ran that same function on
  `git cat-file -p aa8d5f19…` to write `tests/flow/fixtures/flow_py_definitions.json`: 180 keys, Python `3.13`.
- `tests/flow/test_move_proof.py` parses the package from disk and does not import it. It asserts that the minor Python
  version matches, that the snapshot has at least 124 top-level definitions, and that the missing, extra, duplicated and
  differing sets are empty. It also asserts that the plumbing (`logger`, `__all__`, the late binding) appears only in
  its allowed form and place.
- `tests/flow/test_package_exports.py` checks each snapshot definition and constant: it is in `flow.__all__`, and
  `flow.<name>` is the defining submodule's object. It also checks the five re-exported imports, and that
  `stages.decomposition.PipelineEngine is pipeline.PipelineEngine`.
- Result: 158 passed. Mutation check: changing `CI_REGISTRATION_TIMEOUT_SECONDS` to `181.0` failed with
  `definitions whose body changed: ['<const>:CI_REGISTRATION_TIMEOUT_SECONDS (github/ci.py)']`. The change was reverted.

### Step 4: Retarget every test patch to the submodule that looks the name up

- Lookup map, measured rather than guessed. A temporary recorder (`tests/_flowrec.py`, deleted, never committed)
  replaced each of the 318 patch sites (292 `"sandbox_executor.flow.<name>"` strings plus 26 `setattr`/`patch.object`
  calls, across 11 files) with one shared mock patched at every module that binds the name. A proxy recorded the calling
  module. The affected files then ran once: 1381 tests, with only the known source-reading test failing. Each recorded
  site was cross-checked against a static import map. The one mismatch, `run_ensemble` (called from `review.ensemble`
  but looked up in `review.loop`, which passes it on as a value), was corrected to the lookup site.
- Rewrite rules (scratch `/tmp/flowsplit/rewrite.py`):
  - One recorded site: plain `patch("sandbox_executor.flow.<sub>.<name>", ...)`.
  - Several recorded sites: the new `tests/flow/lookup_patch.py` `patch_lookup_sites(target, ..., **kwargs)` patches
    every target with one shared mock. This applies to 34 call sites over 9 names: `run_git`, `check_pr_mergeability`,
    `probe_ci_checks`, `check_and_resolve_stage_conflicts`, `_dispatch_role`, `_read_ledger_rows`,
    `_resolve_branch_ref`, `_fetch_branch_prefix_error` and `_warn_ledger_failure`.
  - Never called during the test (for example `assert_not_called` guards): every static lookup site, so the test keeps
    the same protection it had under the monolith.
  - Looked up only through the package by a lazy caller outside it (`review_target` for `_execution_effective_diff`,
    `cli.commands.review` for `run_review_loop_cli`, `entrypoint.planner` for `check_and_resolve_stage_conflicts`): the
    package target stays, because that caller really does read the package attribute at call time (13 sites).
  - stdlib attributes (`shutil.which`, `time.sleep`, `time.monotonic`, `time.time`, `subprocess.run`): anchored on the
    flow submodule whose code calls them.
- Deviation from the Step 4 discard rule: 9 of about 52 distinct patched names (about 17%) need more than one site, just
  over the plan's 15% tripwire. I re-ran Step 1 point 6 as the rule asks: the static map and the recording agree, so the
  map is right. These names are shared across clusters by design (for example `run_git` is used in 5 modules).
- The one assertion-free body change: `test_only_the_review_stage_constructs_a_halt` read `flow_module.__file__`, which
  is now `__init__.py`. It now reads every module of the package joined together. Its assertions are unchanged.
- Guard: `tests/flow/test_patch_targets.py` fails on any `"sandbox_executor.flow.<name>"` string whose first segment is
  not a submodule, unless the name is imported lazily from the package by `src` code (computed from the AST). It also
  fails on any `setattr`/`patch.object` of the `flow` or `flow_module` object. Mutation check: a planted
  `"sandbox_executor.flow.run_git"` string failed the guard.
- Result: the 15 affected files gave 1774 passed in 6.40s. The per-test outcomes are identical to the Step 1 baseline
  (diffed by node ID). The only difference in the `-rA` output is the log record name
  `sandbox_executor.flow.ledger_reconcile` in place of `sandbox_executor.flow`.

### Step 5: Split `tests/test_flow.py` into `tests/flow/`

- The scratch generator `/tmp/flowsplit/gen_tests.py` moved whole classes as exact source slices (with their preceding
  comments). Then `ruff --fix F401,I001` and `ruff format` pruned the copied import header.
- `tests/flow/conftest.py` holds the two autouse fixtures (`_sandboxed_clean_reviewers`, `_no_real_prettier`), the
  session `_flow_repos_template` and `flow_repos`. `tests/flow/helpers.py` holds the 29 non-fixture helpers and
  constants (`FakeRoleAgents`, `BudgetRoleAgents`, `_dispatch_context`, `_git`, `_verified_diff`,
  `_review_loop_gh_stubs` and the rest), with their names unchanged.
- Class placement, confirmed by reading each class's docstring and entry points. Where it differs from the plan's
  tentative mapping, the reason is given:
  - `test_context.py`: `TestFlowDataModels`
  - `test_checkpoint.py`: `TestFlowResumptionAndErrorHandling`
  - `test_ledger_reconcile.py`: `TestLedgerReconciliation`
  - `test_stage_conflicts.py`: `TestProactiveConflictAndReviewLoop`
  - `test_dispatch.py`: `TestFlowRoleDispatch` and `TestFlowDispatchMergeInteractions`
  - `test_git_refs.py`: `TestStageTargetsIgnoreOperatorHead`
  - `test_pipeline.py`: `TestFlowStages`, `TestFlowCLI`, `TestSkipPushRejectedOnEveryNonDryRun`,
    `TestFlowWorktreeHermetic` and `TestStageBudgets`. The last covers ceilings across all stages, run by the engine, so
    it is not a plan-stage test.
  - `github/test_pr.py`: `TestReviewMergeabilityGuards` and `TestMergeabilityRefResolution`
  - `github/test_ci.py`: `TestUnmeasuredTestsStopEarly`, the bounded wait for CI registration (the plan said
    `stages/test_execute.py`)
  - `github/test_review_post.py`: `TestSemanticPRAndConsensusReview`
  - `review/test_loop.py`: `TestReviewLoopStage` and `TestEngineOwnedReviewLoop`
  - `review/test_records.py`: `TestStage4LedgerProvenance`, the Stage 4 `ACT-*` rows (the plan said
    `test_ledger_reconcile.py`)
  - `review/test_ensemble.py`: `TestReviewDataSeam`
  - `stages/test_review.py`: `TestStage4TestEvidence`, a Stage 4 rule (the plan said `stages/test_execute.py`)
  - `stages/test_execute.py`: `TestStage3PartialResumeDispatch`
  - `stages/test_decomposition.py`: `TestEntropyBudgetHandoffInFlow`, which is mostly about decomposition and child
    flows (the plan said `stages/test_plan.py`)
  - `stages/test_calibrate.py`: `TestStage5PromotedArtifactsCommit`
- `tests/test_ledger_append_only.py` now imports `FakeRoleAgents`, `_dispatch_context` and `_git` from
  `tests.flow.helpers`. `tests/test_flow.py` was removed with `git rm`.
- The autouse fake-reviewer fixture now also applies to the Step 3 tests. `test_package_exports.py` therefore compares
  the namespaces captured at import time, before any fixture patches a binding.
- Parity: all 337 `test_flow.py` node IDs (280 test functions) are present under `tests/flow/`, matched by
  `Class::test[param]`. 161 node IDs are new (the proof, export and guard tests). Node IDs in other files are unchanged.
  Full collection grew from 3,131 to 3,292.
- Outcomes and timing (one run each, `-n auto --dist worksteal`): `tests/flow` plus `test_ledger_append_only.py` gave
  538 passed in 5.33s. The base worktree's `test_flow.py` plus `test_ledger_append_only.py` gave 377 passed in 5.55s.
  All 377 shared node IDs have identical outcomes, so the session fixture scope is intact.

### Step 6: Per-module size ratchet in the lint task

- `apps/sandbox-executor/flow_module_budgets.json` records the line count of all 27 `.py` files under `flow/` (23
  modules plus four `__init__.py`), measured after the final ruff format, together with a `description` of the rule. It
  sits outside the package, so it never ships in the wheel.
- `scripts/check_flow_module_budgets.py` is stdlib only, with `main(argv) -> int`, in the style of
  `scripts/check_ci_runtime_budget.py`. It prints one `::error::` line per violation and exits 1 when a module is over
  budget, a module has no entry, or an entry names a missing module. There is no update flag.
- The root `pyproject.toml` `lint` task now ends with `&& python scripts/check_flow_module_budgets.py`, and `uv.lock` is
  unchanged.
- `tests/flow/test_module_budgets.py` (5 tests) uses the `from scripts.<module> import main` pattern. It checks that the
  live tree passes, that a module within budget passes, and that each of the three failure kinds fails, so CI's hygiene
  job (ruff plus pytest) enforces the ratchet too.
- Result: `uv run task lint` passed (`Flow module budgets passed: 27 modules within their recorded line counts`), and
  the budget tests gave 5 passed in 0.02s.

### Step 7: Module map in `docs/architecture.md`

- Added `### Flow engine module map` right after `### Minimal module map (conceptual)`. It holds a table of all 23
  modules plus `flow/__init__.py`, one line each, a note on the three sub-package `__init__.py` files, and three rules:
  the import direction with the one late-bound `PipelineEngine` edge, tests patching the submodule rather than the
  package (`patch_lookup_sites`, the guard test), and the size ratchet (`flow_module_budgets.json`,
  `scripts/check_flow_module_budgets.py`). It also points to the move-proof test and its snapshot.
- `### Complexity Gate and Ratchet Baseline` gained one sentence: the snapshot entries now name
  `sandbox_executor/flow/<module>.py`. No numbers changed. The doc's `187` and `91` already differ from the test's `184`
  and `90`; that predates this change and is out of scope, so it is left for a follow-up.
- Other mentions of `flow.py` or `test_flow.py` outside the scope, left for a follow-up:
  `apps/sandbox-executor/README.md:357` (`uv run pytest apps/sandbox-executor/tests/test_flow.py`) and docstrings in
  `tests/test_turns.py` and `tests/test_unique_stage_ids.py`.
- `uv run task lint-docs` passed after `npx prettier --write` on the doc and on these notes.
