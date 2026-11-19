# Plan for I-1794192030-split-flow-py-into-flow-package

- **Plan ID:** P-1794192045-c23d4516-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-09T02:40:46.292Z

## Planner Autonomy Summary

- **Intent handling:** REFRAME (narrow: the goal is unchanged, but the touch set is widened and made explicit)
- **Reframed intent (if applicable):** Same goals 1–8. The scope grows by the files the move unavoidably touches, which
  the intent's `scope` list leaves out: `tests/test_complexity_gate.py` (its `BASELINE_SNAPSHOT` is keyed by
  `sandbox_executor/flow.py`), the other test files that patch `sandbox_executor.flow.<name>` (`test_review_loop.py`,
  `test_review_dispatch.py`, `test_ledger_append_only.py`, `test_cli.py`, `test_calibration.py`, `test_preflight.py`,
  `test_review_target.py`, `test_planner.py`, `test_prompt_templates.py`, `review_fakes.py`), the root `pyproject.toml`
  `lint` task line, one new `scripts/check_flow_module_budgets.py`, and one new budget file. Without these, goals 3 and
  6 and the existing complexity ratchet cannot pass. They are declared here as the world ruleset §3 requires for test
  edits.
- **Exploration stance:** balanced. The move itself is mechanical and should be exploited through a generator script.
  One exploratory step at the start checks the import-cycle and patch-lookup assumptions before any file is written,
  because those two are where a move-only split usually breaks.
- **Safety priority level:** elevated
- **Priority Justification:** `holon-config/world/constraints.md` §4 classes refactoring a core module as deep-model
  work, and flow.py is the flow engine that drives every stage. `constraints.md` §3 (append-only ledgers) applies
  because `ledger_reconcile.py` owns the ledger conflict union logic, which has to move byte-for-byte. Containment stays
  at the container tier (`safety.md`, entropy 10–30). Merging stays with the human maintainer (`safety.md` §5).

## Exploration

- **Proportion of steps that are exploratory:** 0.125 (1 of 8 steps; Step 1 is BALANCED)
- **Justification:** The cluster layout is already set by the intent, so the uncertainty is in the details: the cycle
  `stages/execute -> stages/decomposition -> pipeline -> stages/execute` (found during planning: `_run_decomposed_plan`
  calls `PipelineEngine(child).run()` at flow.py:2834, and `run_execute_stage` calls `_run_decomposed_plan` at
  flow.py:3105), names patched in tests but looked up in more than one submodule, and logger-name side effects. A cheap
  dependency-graph pass up front turns these into known facts before the expensive move.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.70  |
| entropy_pred        | 13.7  |
| impact_pred         | 85.0  |
| cost_pred           | 31.0  |
| learning_value_pred | 6.0   |
| ev_pred             | 27.39 |

## Plan Description & Strategy

The split is done by a **re-runnable generator**, not by hand. A throwaway script (kept outside the repository, in the
execution scratch area, and never committed) reads flow.py with `ast`. Using a declarative manifest of
`definition name -> target module`, it copies each top-level definition's exact source text: decorators, the `def` line
with its existing `# noqa` comment, and any directly preceding comment block. Module-level constants go to their single
using module, or to `flow/constants.py` when two or more modules use them. Each module then gets the import lines it
needs, and `ruff check --fix --select F401,I001` prunes them. Because bodies are copied as text, AST equality holds by
construction. The committed AST proof test then checks it independently. If main changes flow.py during execution (44
commits since 2026-10-01), the executor rebases and runs the generator again instead of merging by hand.

Facts measured during planning, which the executor re-confirms in Step 1:

- flow.py: 6,159 lines, 124 top-level `def`/`class` nodes, about 30 module-level constants, and 6 function-local lazy
  imports from `sandbox_executor.cli` and `sandbox_executor.review_ensemble` (these stay inside their bodies,
  unchanged).
- Callers in `src`: `cli/__init__.py`, `cli/commands/review.py`, `entrypoint/review_loop.py`, `entrypoint/executor.py`,
  `entrypoint/planner.py`, `entrypoint/intent_creator.py` and `review_target.py` (which imports the private
  `_resolve_review_commit`, `_fetch_refspecs_error`, `_has_origin_remote`, `_resolve_branch_ref`,
  `_execution_effective_diff` and `_execution_parent_plan_branch`). None of them is edited.
- Tests: 292 `"sandbox_executor.flow.<name>"` patch strings over 44 distinct names, plus 26
  `monkeypatch.setattr(flow, ...)` and `patch.object(flow, ...)` calls, across 11 files. `test_flow.py` has 280 tests in
  24 classes, 6,483 lines. `tests/test_ledger_append_only.py` imports `FakeRoleAgents`, `_dispatch_context` and `_git`
  from `tests.test_flow`.
- `tests/test_complexity_gate.py` `BASELINE_SNAPSHOT` lists flow.py's inline complexity `# noqa` entries as
  `("sandbox_executor/flow.py", codes)`. These must be re-keyed to the new paths with the same multiset of codes and the
  same counts (`187` and `91`). The suppressions themselves move verbatim, and none is added.
- `caplog.set_level(..., logger="sandbox_executor.flow")` appears 4 times. With `logging.getLogger(__name__)` in each
  submodule, the child loggers (`sandbox_executor.flow.<sub>`) inherit the level and propagate to that parent, so these
  tests keep working. Record names change from `sandbox_executor.flow` to the submodule name. That is the only
  observable difference, it is accepted, and it is documented in the PR. A fixed parent name would hide which module
  logged.
- CI hygiene (`.github/workflows/test-hygiene.yml`) runs ruff directly, not `task lint`. So the size ratchet is wired
  into `task lint` (as goal 6 asks) and also wrapped in a pytest, so CI enforces it too.

Cycle resolution (decided in advance, confirmed in Step 1). `stages/decomposition.py` must not import `pipeline` at
module level. `flow/__init__.py` imports every submodule in dependency order, then binds
`stages.decomposition.PipelineEngine = pipeline.PipelineEngine` after the pipeline import, with a one-line comment
saying why. Function bodies stay untouched, the bare-name `PipelineEngine` lookup inside `_run_decomposed_plan` still
resolves, and a test checks the binding. Every other edge is kept acyclic, from leaves to root:
`constants -> context -> checkpoint -> ledger_reconcile -> stage_conflicts -> dispatch -> git_refs -> intent_questions -> github/* -> review/workspace -> review/records -> review/ensemble -> review/resolver -> review/loop -> stages/* -> pipeline`.
If Step 1 finds another back-edge, the same late-binding pattern in `__init__.py` is the only allowed fix. Any edge that
needs a body edit stops execution (see Step 1 discard criteria).

Steps run in order on one execution branch. The plan fits the 15.0 budget, so it is not decomposed (see the strategy
rationale).

---

## Step 1: Map the definitions, dependency graph, patch lookups and baselines before moving anything

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is a cheap, read-only analysis whose output (a manifest and a lookup map) feeds Steps 2–5 on the
  same branch. A separate branch would only add pipeline overhead.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Assigning definitions per the intent's clusters produces exactly one module-level import
  cycle (`decomposition -> pipeline -> stages/execute -> decomposition`), and the cycle can be resolved without editing
  any function body. Every patched name maps to a finite, enumerable set of submodules that look it up.
- **Learning target:** The exact module import graph and any further back-edges. The list of patched names with more
  than one lookup site, so one test may need several patches. Constants shared by two or more modules. Post-split line
  counts per module.
- **Maximum acceptable cost for this learning:** about 10% of the plan's cost (≈3 units). If the graph shows two or more
  cycles that late binding cannot break, stop and re-plan instead of exploring further.

### Intent & Git Integration

- **Step Intent:** Turn the intent's cluster list into a verified, machine-readable manifest, and record the pre-change
  baselines.
- **Git branch:**
  `I-1794192030-split-flow-py-into-flow-package/P-1794192045-claude-agent-claude-opus-5-5/E-{timestamp}-split/_` (one
  execution branch shared by all steps)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Rebase the execution branch on the plan branch, and that on `main`, per `constraints.md` §1. Record
   `git rev-parse HEAD:apps/sandbox-executor/src/sandbox_executor/flow.py` (the blob SHA) as the provenance of the
   snapshot.
2. Parse flow.py with `ast`. List every top-level `FunctionDef`, `AsyncFunctionDef`, `ClassDef`, `Assign` and
   `AnnAssign`, with line spans that include decorators and preceding comment blocks. Assert 124 definitions; if the
   count differs, main has moved, so update the manifest.
3. Build the manifest (a plain dict in the scratch generator) from the intent's cluster ranges. Resolve the "X through
   Y" ranges against source order, for example `ConflictResolutionError` through `_resolve_conflicting_ledger` at lines
   581–781, and `_sub_intent_depth` through `_run_decomposed_plan` at lines 2653–2877, which includes `_PR_URL_RE`,
   `format_decomposition_summary`, `_child_context` and `_child_intent_branch`. `_SUMMARY_STATUS_WORDS` stays with
   `StageResult` in `context.py`, since `StageResult` uses it.
4. For every definition, collect the free global names it loads (walk `ast.Name` nodes in `Load` context, excluding
   locals and parameters). Map each name to its home: a stdlib module, a sandbox_executor import, or another flow
   submodule. Derive the submodule import graph and run a strongly-connected-components check. Expect exactly the
   decomposition↔pipeline↔execute cycle.
5. Place constants: a constant used by exactly one module goes to that module. One used by two or more goes to
   `flow/constants.py`. Likely candidates are `EXECUTOR_GIT_ENV`, `REVIEW_GIT_ENV`, `FAILED_EXECUTION_STATUSES`,
   `TESTS_NOT_MEASURED_ERROR`, `HOST_REVIEW_REFUSAL`, `_SEVERITY_RANK` and the `CI_REGISTRATION_*` values, but measure
   rather than assume.
6. Build the patch lookup map. For each of the 44 patched names and the 26 `setattr(flow, ...)` names, list the
   submodules whose code loads that name as a global. Mark names with more than one lookup site, for example `run_git`,
   `_dispatch_role`, `shutil.which` and `probe_ci_checks`.
7. Record baselines: `uv run pytest --collect-only -q apps/sandbox-executor/tests` node IDs (the total, and the 280 from
   test_flow.py), and the current pass state of the affected test files via `uv run pytest <paths>`.
8. Predict line counts per module. Flag any module over about 400 lines for the PR description; move-only cannot shrink
   them. `review/loop.py` is expected at about 1,000 lines, because `_run_review_loop_iterations` alone spans
   flow.py:4309–5086.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (every later step consumes the manifest; p_success 0.95, so no sub-intent is needed)

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §1 (rebase discipline), `ruleset.md` §3 (test changes declared), `safety.md` §2
  (sandboxed execution)
- **Potential failure modes for this step:** misreading a "through" range boundary; missing dynamic lookups such as
  `getattr(module, name)` or `globals()` (none found during planning, but re-grep).
- **Guardrails and early‑abort checks:** the manifest must cover all 124 definitions exactly once (asserted). Abort if
  the SCC check finds a cycle that cannot be broken by late binding in `__init__.py`.

### Success & Discard Criteria

- **Success:** The manifest covers 124/124 definitions and all constants. Exactly one known cycle, with the late-binding
  fix confirmed feasible. A patch lookup map for all 70 patched names. Baselines recorded.
- **Discard:** two or more cycles that need body edits, or step cost above 1.5x prediction. Then re-plan, possibly with
  a reframed cluster layout proposed back to the maintainer.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.8   |
| impact_pred         | 15.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 14.01 |

### Step Metrics Rationale

`EV = 0.95 * 15.0 + 0.5 * 6.0 - 0.3 * 0.8 - 3.0 = 14.25 + 3.00 - 0.24 - 3.00 = 14.01`. The step is read-only, so entropy
is minimal. Learning value is high because it turns the cycle and patch-lookup risks, the two main failure sources for
the whole plan, into facts.

---

## Step 2: Generate the flow/ package mechanically and remove flow.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is the atomic core of the intent. Deleting flow.py, creating the package, re-exporting and
  re-keying the complexity ratchet must land together, or the tree does not import. Splitting it into a sub-intent would
  leave a broken intermediate state.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Replace `sandbox_executor/flow.py` with the `sandbox_executor/flow/` package laid out per the intent,
  with byte-identical definition source and unchanged callers.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run the scratch generator, which lives outside the repo and is never committed. For each target module it writes: (a)
   a module docstring in Australian English, as `ruleset.md` §2 requires; (b) `from __future__ import annotations`; (c)
   the stdlib and sandbox_executor import lines copied from flow.py's header, plus explicit absolute intra-package
   imports such as `from sandbox_executor.flow.context import FlowContext, StageResult` for every name the module loads;
   (d) `logger = logging.getLogger(__name__)` where the module logs; (e) its constants; (f) its definitions in original
   source order, copied as exact source slices.
2. Create package directories with `__init__.py` docstrings: `flow/`, `flow/github/`, `flow/review/`, `flow/stages/`.
   Modules: `constants`, `context`, `checkpoint`, `ledger_reconcile`, `stage_conflicts`, `intent_questions`, `dispatch`,
   `git_refs`, `pipeline`, `github/{pr,ci,review_post}`, `review/{workspace,records,ensemble,resolver,loop}` and
   `stages/{intent,plan,decomposition,execute,review,calibrate}`.
3. Write `flow/__init__.py`. Import the submodules in leaf-to-root order and re-export every name that was bound at
   flow.py module level: all 124 definitions, all constants, and every imported name that tests or callers access as
   `flow.<name>`, such as `run_git`, `build_review_request`, `run_ensemble` and `run_calibrate`. List them in an
   explicit `__all__` so ruff F401 accepts the re-exports without `# noqa`. Then late-bind `PipelineEngine` into
   `stages.decomposition`, with a one-line comment explaining the cycle. Add no new imports from entrypoints beyond
   those flow.py already had, the `entrypoint.executor` and `entrypoint.planner` imports.
4. Delete flow.py with `git rm`, in the same commit.
5. Run `uv run ruff check --fix --select F401,I001` on the new package only, then `uv run ruff format`, then a full
   `uv run ruff check .`. Check that formatting changed no definition body; Step 3's proof test confirms this.
6. Re-key `tests/test_complexity_gate.py` `BASELINE_SNAPSHOT`. Replace each `("sandbox_executor/flow.py", codes)` entry
   with the new module path of the function carrying those codes, keeping the multiset of codes and both baseline
   constants (187, 91) unchanged.
7. Smoke check: `uv run python -c` imports of `sandbox_executor.flow`, `sandbox_executor.cli`,
   `sandbox_executor.entrypoint.review_loop` and `sandbox_executor.review_target`. Then
   `uv run pytest apps/sandbox-executor/tests/test_complexity_gate.py apps/sandbox-executor/tests/test_cli.py`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (p_success 0.80 > 0.6, so it stays in this plan)

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 1 (move only); `ruleset.md` §2 (docstrings, no wildcard imports); planner rules (no
  new `# noqa`, no new entrypoint imports, ruff limits); `constraints.md` §3 (`ledger_reconcile` moves byte-for-byte)
- **Potential failure modes for this step:** circular import at package import time; ruff `I001` reordering an import
  that matters for side effects; a missing re-export breaking a lazy caller only at runtime, for example
  `review_target.py`'s function-local imports; ruff format reflowing a body because of an import-length change (it
  should not, since bodies are untouched).
- **Guardrails and early‑abort checks:** import smoke checks right after generation. A re-export completeness assertion
  (Step 3) compares the package namespace with the old module namespace. Abort and regenerate rather than hand-patch any
  module.

### Success & Discard Criteria

- **Success:** flow.py is gone, all 23 modules exist, `import sandbox_executor.flow` and every src caller import
  succeed, `ruff check .` and `ruff format --check .` are clean, and the complexity gate passes at 187/91.
- **Discard:** a cycle that needs a body edit, or more than 3 regeneration rounds without a clean import. Then stop and
  re-plan the cluster boundary with the maintainer.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 3.5   |
| impact_pred         | 40.0  |
| cost_pred           | 8.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 25.45 |

### Step Metrics Rationale

`EV = 0.80 * 40.0 + 0.5 * 5.0 - 0.3 * 3.5 - 8.0 = 32.00 + 2.50 - 1.05 - 8.00 = 25.45`. Entropy comes mainly from SSA (23
new files and a 6,159-line deletion) and CL (flow.py is the hottest file in the repo). IRR is low: it is a pure move, so
a revert is trivial. Generation makes the result deterministic, which keeps p_success high despite the size.

---

## Step 3: Add the AST move-proof test, the committed definition snapshot and the re-export completeness test

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is small and deterministic, and it is the mechanical evidence that Step 2 was move-only. It must
  sit on the same branch as the move it proves.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove that every function and class body is unchanged, and that no definition is missing or added.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Use the committed-snapshot option (goal 4 allows it), not a git lookup of the merge base at test time. The test suite
   is hermetic (`docs/hermetic_testing.md`), shallow CI clones may not hold the merge base, and the snapshot stays valid
   after merge.
2. Snapshot file: `apps/sandbox-executor/tests/flow/fixtures/flow_py_definitions.json`. It records the source blob SHA
   from Step 1, the Python version, and a map of qualified name to `sha256(ast.dump(node))`. Keys cover each top-level
   function and class (`FlowContext`), each nested method (`FlowContext.<method>`, `PipelineEngine.run`) and each
   module-level constant (`<const>:NAME`). `ast.dump` runs without attributes, so line numbers do not matter, and the
   key set is the full qualified-name set. Generate the snapshot from flow.py at the Step 1 blob, before its deletion,
   with the same helper function the test uses, so producer and checker cannot drift.
3. Test module `apps/sandbox-executor/tests/flow/test_move_proof.py`:
   - It parses every `.py` file under `src/sandbox_executor/flow/` from disk. It does not import them, so the test works
     even if an import breaks.
   - It collects the same qualified-name map, excluding the new package plumbing: `__all__`, the `logger` assignments,
     and the late-binding line in `__init__.py`, which are listed explicitly in an allow-list constant.
   - It asserts that the missing set (in the snapshot, not in the package) is empty, the extra set is empty, the
     differing-digest set is empty, and no qualified name is defined twice across modules.
   - On failure it prints the offending qualified names and their module paths.
4. Re-export completeness test, in the same module or `tests/flow/test_package_exports.py`. For every key in the
   snapshot's top-level and constant sets, plus the recorded list of imported names reached through `flow.<name>`,
   assert that `hasattr(sandbox_executor.flow, name)` holds and that the object is the submodule's object.
5. Cycle-binding test: assert that `sandbox_executor.flow.stages.decomposition.PipelineEngine` is
   `sandbox_executor.flow.pipeline.PipelineEngine`.
6. Run `uv run pytest apps/sandbox-executor/tests/flow/test_move_proof.py`.

### Dependencies & Criticality

- **Depends on:** Step 2 (and the Step 1 blob SHA)
- **Is Bottleneck:** YES (acceptance criterion 2; p_success 0.92)

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 4; `ruleset.md` §3 (pytest, tests in `tests/`); hermetic test policy
- **Potential failure modes for this step:** a snapshot generated after ruff touched flow.py (it must come from the
  original blob); the `ast.dump` output format differing between Python patch versions (pinned to `==3.13.*`; the
  snapshot records the version, and the test fails loudly if the minor version differs).
- **Guardrails and early‑abort checks:** generate the snapshot from `git show <blob>`, not the working tree.
  Sanity-check that the snapshot has at least 124 top-level keys.

### Success & Discard Criteria

- **Success:** the proof test passes with 0 missing, 0 extra and 0 differing. The export and binding tests pass.
- **Discard:** any non-empty diff that Step 2 regeneration cannot remove means a body changed. Never "fix" the snapshot;
  fix the move.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.2   |
| impact_pred         | 15.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 12.44 |

### Step Metrics Rationale

`EV = 0.92 * 15.0 + 0.5 * 4.0 - 0.3 * 1.2 - 3.0 = 13.80 + 2.00 - 0.36 - 3.00 = 12.44`. The step adds about 3 new files
and is fully reversible. The proof test is reusable for any future move-only refactor, which adds some learning value.

---

## Step 4: Retarget every test patch to the submodule where the name is looked up

- **Sub‑intent recommendation:** NO
- **Reasoning:** The tests cannot pass after Step 2 without this, so it has to be on the same branch. It is the riskiest
  test-side step, but it is driven by the Step 1 lookup map, not by guesswork.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make every `mock.patch` and `monkeypatch.setattr` intercept the lookup that the code under test
  actually performs.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Declared test edits (`ruleset.md` §3): `test_flow.py` (203 references; done before the Step 5 split, so the split
   moves already-correct tests), `test_review_loop.py` (51), `test_review_dispatch.py` (27),
   `test_ledger_append_only.py` (11), `test_cli.py` (5), `test_calibration.py` (4), `test_preflight.py` (4),
   `test_review_target.py` (4), `review_fakes.py` (4), `test_planner.py` (1) and `test_prompt_templates.py` (1). Only
   patch target strings and `setattr` target objects change. Assertions and test bodies stay as they are.
2. Single-site names: rewrite `"sandbox_executor.flow.X"` to `"sandbox_executor.flow.<sub>.X"`, using the Step 1 map.
   For example, `probe_ci_checks` as looked up by the review loop becomes
   `"sandbox_executor.flow.review.loop.probe_ci_checks"`, and `run_review_stage` as looked up by `PipelineEngine`
   becomes `"sandbox_executor.flow.pipeline.run_review_stage"`.
3. Attribute-of-stdlib patches (`shutil.which`, `time.sleep`, `time.monotonic`, `subprocess.run`) patch the shared
   stdlib module object, so any submodule that imports that stdlib module is a valid anchor. Retarget each one to the
   submodule that the code under test lives in, to make the intent visible.
4. Multi-site names, such as `run_git` used by `git_refs`, `stage_conflicts`, the stages and `github/pr`, and
   `_dispatch_role` used by several stages: for each test, find which submodule's code path the test runs, from the test
   name and the function it calls. Patch that submodule. Where a test drives two submodules that both look the name up,
   stack one patch per site and share the same mock object, so call-count assertions keep their meaning. Never patch
   only `sandbox_executor.flow.X`.
5. `monkeypatch.setattr(flow, "name", ...)` (`review_fakes.py`, `test_review_target.py`, `test_ledger_append_only.py`):
   change the target object to the submodule, for example `flow.review.ensemble`. Direct calls such as
   `flow._write_resolved_ledger(...)` keep working through the re-exports and are not changed.
6. Guard: add a test that scans all test files for the regex `sandbox_executor\.flow\.(?!(context|checkpoint|...)\.)`
   and asserts there are no remaining package-level patch strings, apart from an allow-list of attribute accesses that
   are not patches. This stops future tests silently patching the re-export.
7. Run each touched file with `uv run pytest <file>`, in a single pass per file. Iterate only on failures.

### Dependencies & Criticality

- **Depends on:** Steps 1 and 2
- **Is Bottleneck:** YES (p_success 0.78 > 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 3; `ruleset.md` §3 (sandbox may not modify assertions; edits declared here); hermetic
  test fixtures (`tests/gh_write_guard.py`, `hermetic_fixtures.py`) unchanged
- **Potential failure modes for this step:** a patch that "passes" because the test no longer exercises the patched
  path, which is a false green (for example a real `gh` call that a hermetic guard does not stop); missing one of the
  multi-site lookups, so the real function runs.
- **Guardrails and early‑abort checks:** the hermetic guards (`gh_write_guard`, the sandbox hermetic guard) stay active
  and catch real external calls. Compare per-test pass/fail with the Step 1 baseline, and require identical outcomes.
  Spot-check the mock `call_count` assertions that already exist.

### Success & Discard Criteria

- **Success:** all 11 files pass with the same per-test outcomes as the baseline, and the guard test shows 0 remaining
  package-level patch targets.
- **Discard:** if more than 15% of the patched names need multi-site stacking, the map is wrong. Re-run Step 1 point 6
  before going on.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.78  |
| entropy_pred        | 3.0   |
| impact_pred         | 20.0  |
| cost_pred           | 6.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 11.70 |

### Step Metrics Rationale

`EV = 0.78 * 20.0 + 0.5 * 6.0 - 0.3 * 3.0 - 6.0 = 15.60 + 3.00 - 0.90 - 6.00 = 11.70`. 318 edit sites across 11 files
give high SSA, and the step has a real silent-false-green failure mode. Learning value is high, because the lookup map
and the guard pattern apply to any later module split.

---

## Step 5: Split tests/test_flow.py into tests/flow/ mirroring the package layout

- **Sub‑intent recommendation:** NO
- **Reasoning:** The split is mechanical (whole classes move) and depends on the retargeted patches from Step 4. It is
  verified by node-ID parity, so there is no need for its own branch.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Replace the 6,483-line `test_flow.py` with focused per-module test files, with a test count equal to
  or higher than before.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `tests/flow/__init__.py`, needed because `tests` is a package and `test_ledger_append_only.py` imports by
   package path. Also create `tests/flow/conftest.py` for the two autouse fixtures (`_sandboxed_clean_reviewers`,
   `_no_real_prettier`), the session `_flow_repos_template` and `flow_repos` fixtures, and `_verified_diff` and
   `_review_loop_gh_stubs`. Add `tests/flow/helpers.py` for the shared non-fixture helpers (`FakeRoleAgents`,
   `_dispatch_context`, `_git`, `_configure_identity`, `_append_jsonl`, `_ledger_rows`, `_budget_context` and the rest).
   Autouse fixtures in `tests/flow/conftest.py` apply only under `tests/flow/`, which matches today's scope, where they
   applied only inside test_flow.py.
2. Move whole test classes, never splitting a class, to the file named after the module whose behaviour they mainly
   assert. Planned mapping, to be confirmed by reading each class:
   - `test_context.py` gets `TestFlowDataModels`.
   - `test_checkpoint.py` gets `TestFlowResumptionAndErrorHandling`.
   - `test_ledger_reconcile.py` gets `TestLedgerReconciliation` and `TestStage4LedgerProvenance`.
   - `test_stage_conflicts.py` gets `TestProactiveConflictAndReviewLoop`.
   - `github/test_pr.py` gets `TestReviewMergeabilityGuards` and `TestMergeabilityRefResolution`.
   - `github/test_review_post.py` gets `TestSemanticPRAndConsensusReview`.
   - `test_dispatch.py` gets `TestFlowRoleDispatch` and `TestFlowDispatchMergeInteractions`.
   - `test_git_refs.py` gets `TestStageTargetsIgnoreOperatorHead`.
   - `stages/test_plan.py` gets `TestStageBudgets` and `TestEntropyBudgetHandoffInFlow`.
   - `stages/test_execute.py` gets `TestStage4TestEvidence`, `TestUnmeasuredTestsStopEarly` and
     `TestStage3PartialResumeDispatch`.
   - `review/test_loop.py` gets `TestReviewLoopStage` and `TestEngineOwnedReviewLoop`.
   - `review/test_ensemble.py` gets `TestReviewDataSeam`.
   - `stages/test_calibrate.py` gets `TestStage5PromotedArtifactsCommit`.
   - `test_pipeline.py` gets `TestFlowStages`, `TestFlowCLI`, `TestSkipPushRejectedOnEveryNonDryRun` and
     `TestFlowWorktreeHermetic`. Module-level test functions go with their nearest class subject. If a class's file
     would exceed about 1,500 lines, that is acceptable: whole-class moves keep the split mechanical.
3. Give the test subdirectories (`tests/flow/github/`, `tests/flow/review/`, `tests/flow/stages/`) `__init__.py` files,
   and make sure basenames stay unique or package-qualified, to avoid pytest module-name clashes with
   `tests/test_review_loop.py` and similar.
4. Update `tests/test_ledger_append_only.py` to import `FakeRoleAgents`, `_dispatch_context` and `_git` from
   `tests.flow.helpers`. That is the only change to that import.
5. Delete `tests/test_flow.py` with `git rm`.
6. Parity check. Collect node IDs before (Step 1 baseline) and after. Strip the file path, keeping `Class::test` and the
   parametrisation. Assert that the multisets are equal, or that the new set is a superset because of the Step 3 and
   Step 4 tests. Assert that the total collected under `tests/flow/` is at least 280, plus the new proof and guard
   tests.
7. Run `uv run pytest apps/sandbox-executor/tests/flow apps/sandbox-executor/tests/test_ledger_append_only.py`.

### Dependencies & Criticality

- **Depends on:** Step 4
- **Is Bottleneck:** NO (a failure here can fall back to keeping test_flow.py as is; the intent would then be partly
  met)

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 5; `ruleset.md` §3 (test placement mirrors source; declared test edits)
- **Potential failure modes for this step:** losing the session fixture scope (`_flow_repos_template`) and slowing the
  suite; autouse fixtures no longer applying to moved tests; duplicate test module basenames.
- **Guardrails and early‑abort checks:** node-ID parity is a hard gate. Compare the pytest-reported time of the
  `tests/flow` run against the Step 1 test_flow.py time once; a slowdown over 20% means fixture scope was lost.

### Success & Discard Criteria

- **Success:** test_flow.py is gone, at least 280 tests from it are collected under `tests/flow/` with identical
  outcomes, and `test_ledger_append_only.py` passes.
- **Discard:** if parity cannot be reached within 2 iterations, revert to keeping a single `tests/flow/test_flow.py` and
  report goal 5 as partly met.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 2.5   |
| impact_pred         | 12.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 5.95  |

### Step Metrics Rationale

`EV = 0.85 * 12.0 + 0.5 * 3.0 - 0.3 * 2.5 - 5.0 = 10.20 + 1.50 - 0.75 - 5.00 = 5.95`. The step creates about 20 test
files, but moving whole classes is low-risk. Impact is moderate: test-side collisions also drop for future concurrent
flows.

---

## Step 6: Add the per-module size ratchet to the lint task

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is a small, self-contained script, a JSON file and a one-line task change. It needs the final
  post-split sizes, so it belongs at the end of the same branch.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Stop any flow/ module from growing past its post-split line count.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add the budget file `apps/sandbox-executor/flow_module_budgets.json`. It maps each path relative to
   `src/sandbox_executor/flow/` to its line count, recorded after Step 2's ruff format, plus a short `description` field
   that explains the ratchet rule. Put it outside the package so it never ships in the wheel.
2. Add the script `scripts/check_flow_module_budgets.py`, in the same style as `scripts/check_ci_runtime_budget.py`:
   stdlib only, a `main(argv) -> int` and a module docstring. It fails, exiting non-zero with one line per violation, in
   three cases: (a) a module's line count exceeds its budget; (b) a `.py` file under flow/ has no budget entry, so a new
   module needs an explicit entry recorded at its creation size; (c) a budget entry names a missing file, so deleting a
   module forces removal of its budget. It has no `--update` flag; lowering budgets is a deliberate manual edit.
3. Wire it into the root `pyproject.toml` `lint` task by appending `&& python scripts/check_flow_module_budgets.py`.
   `uv run task lint` runs it inside the uv environment.
4. Add `apps/sandbox-executor/tests/flow/test_module_budgets.py`, which follows `tests/test_ci_timing_gate.py`'s
   `from scripts.<module> import main` pattern. It asserts that the live tree passes, and runs one synthetic tmp-dir
   case for each failure kind. `test-hygiene.yml` runs ruff, not `task lint`, so this wrapper is what makes CI enforce
   the ratchet without touching workflow files.
5. Run `uv run task lint` and `uv run pytest apps/sandbox-executor/tests/flow/test_module_budgets.py`.

### Dependencies & Criticality

- **Depends on:** Step 2 (final module sizes)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 6; no new dependencies; `uv run` only; planner code-placement rules (one focused
  module)
- **Potential failure modes for this step:** budgets recorded before ruff format, so they are off by a few lines; the
  `lint` task change touching `uv.lock` (it does not, since the change is only in the taskipy section).
- **Guardrails and early‑abort checks:** generate the budgets after the last formatting pass, and re-run once after Step
  8's formatting to confirm there are no violations.

### Success & Discard Criteria

- **Success:** `uv run task lint` passes with the ratchet at its baseline, and the synthetic over-budget, missing-entry
  and stale-entry cases fail as expected.
- **Discard:** none expected. If the lint-task wiring conflicts with a concurrent change to `pyproject.toml` on main,
  rebase and re-apply the one-line change.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.3   |
| impact_pred         | 10.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 7.61  |

### Step Metrics Rationale

`EV = 0.90 * 10.0 + 0.5 * 3.0 - 0.3 * 1.3 - 2.5 = 9.00 + 1.50 - 0.39 - 2.50 = 7.61`. The step adds 3 files and changes
one config line. It touches `pyproject.toml`, which is shared config, and that adds a little CL.

---

## Step 7: Document the module map in docs/architecture.md

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is a short documentation edit with no risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Give reviewers and future planners a one-line responsibility per flow module, so touch sets can be
  chosen without loading the code.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Under `### Minimal module map (conceptual)` (docs/architecture.md:433), or as a new `### Flow engine module map`
   right after it, add a table or list of all 23 modules plus the three sub-package `__init__.py` files and
   `flow/__init__.py`, with one line each in Australian English. Examples: `review/loop.py` runs the review-resolve
   iteration loop and the CLI entry; `ledger_reconcile.py` unions append-only ledger conflicts under lock.
2. Document three things: the import direction (leaves to `pipeline`); the one late-binding edge (`PipelineEngine` into
   `stages.decomposition`) and why it exists; and the rule that tests patch the submodule, not the package. Point to the
   size ratchet (`flow_module_budgets.json`, `scripts/check_flow_module_budgets.py`) and the move-proof test.
3. In `### Complexity Gate and Ratchet Baseline`, update no numbers (they stay 187 and 91). Add one sentence saying that
   the snapshot paths now name flow/ submodules.
4. Run `uv run task lint-docs`. If prettier reflows, run `npx prettier --write docs/architecture.md`, through the
   existing `format-docs` task, scoped to this file.
5. Other docs that mention `flow.py` by path are out of the intent's scope. List them in the PR description as a
   follow-up, and do not edit them.

### Dependencies & Criticality

- **Depends on:** Steps 2 and 6
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 7; Australian English; `lint-docs` must pass; markdown hygiene (formulas in code
  spans)
- **Potential failure modes for this step:** prettier oscillation caused by underscores in module names outside code
  spans.
- **Guardrails and early‑abort checks:** wrap every path and identifier in backticks.

### Success & Discard Criteria

- **Success:** the module map is present, every module has its line, and `uv run task lint-docs` passes.
- **Discard:** none.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.6   |
| impact_pred         | 4.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 3.12  |

### Step Metrics Rationale

`EV = 0.95 * 4.0 + 0.5 * 1.0 - 0.3 * 0.6 - 1.0 = 3.80 + 0.50 - 0.18 - 1.00 = 3.12`. One file, fully reversible.

---

## Step 8: Validate the whole change and prepare the PR for the human maintainer

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is the final gate on the same branch; it checks every acceptance criterion at once.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Show that all acceptance criteria hold, and hand the PR to the maintainer, who does the merge.
- **Git branch:** same execution branch. The PR goes to the parent plan and intent branch, per git flow. Only the human
  merges the root intent to `main`.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Rebase onto the parent branch. If main changed flow.py since Step 1, the rebase conflicts on the deleted file. In
   that case, take main's flow.py, re-run Steps 1–3 with the generator and a regenerated snapshot from the new blob,
   re-apply Step 4 for any new patch strings, and re-record the Step 6 budgets. Never hand-merge function bodies into
   submodules.
2. Affected tests first, in one pass:
   `uv run pytest apps/sandbox-executor/tests/flow apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_review_dispatch.py apps/sandbox-executor/tests/test_ledger_append_only.py apps/sandbox-executor/tests/test_cli.py apps/sandbox-executor/tests/test_calibration.py apps/sandbox-executor/tests/test_preflight.py apps/sandbox-executor/tests/test_review_target.py apps/sandbox-executor/tests/test_planner.py apps/sandbox-executor/tests/test_prompt_templates.py apps/sandbox-executor/tests/test_complexity_gate.py apps/sandbox-executor/tests/test_turns.py`.
3. Then the full suite once with `uv run task test`. Record the pass and fail counts and the pytest-reported time. No
   repeated runs: the intent sets no timing target.
4. `uv run task lint` (ruff check, ruff format check, lock check and the new ratchet) and `uv run task lint-docs`.
5. Check the acceptance criteria explicitly:
   - `apps/sandbox-executor/src/sandbox_executor/flow.py` does not exist, and `flow/` is a package.
   - Every `from sandbox_executor.flow import ...` in src and tests resolves. An import-all smoke check over the 87
     test-side and 13 src-side import statements is covered by the full suite.
   - The proof test reports 0, 0 and 0.
   - The collected count is at least the baseline.
   - The ledgers in `holon-knowledge/ledger/` are untouched (`git diff --stat` on that path is empty).
6. PR description: the module map, the proof-test result, the before and after test counts, the module sizes (flagging
   `review/loop.py` and the other modules over 400 lines as candidates for future behaviour-preserving splits), the
   logger-name change note, the late-binding note, and the out-of-scope doc follow-ups. End it with the required
   attribution line. Do not merge.

### Dependencies & Criticality

- **Depends on:** Steps 1–7
- **Is Bottleneck:** YES (acceptance gate; p_success 0.88)

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §1 (rebase, no commits to `main`), §3 (ledgers untouched); `safety.md` §5 (human
  merge); `uv run pytest` only
- **Potential failure modes for this step:** an order-dependent failure under `-n auto --dist worksteal`, from a moved
  session fixture; a concurrent main change to flow.py during review.
- **Guardrails and early‑abort checks:** if the full suite shows failures in files outside the touch set, check them
  against a `main` baseline run before attributing them to this change.

### Success & Discard Criteria

- **Success:** all five acceptance criteria are met and CI is green. The PR is open for the maintainer.
- **Discard:** if the full suite fails on more than 3 tests that the affected-test pass did not reveal, stop and
  re-examine the patch lookup map (Step 4) before any further fixes.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 0.8   |
| impact_pred         | 10.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 7.56  |

### Step Metrics Rationale

`EV = 0.88 * 10.0 + 0.5 * 3.0 - 0.3 * 0.8 - 2.5 = 8.80 + 1.50 - 0.24 - 2.50 = 7.56`. The step only validates. Its
entropy comes from the chance that a rebase on the hot file forces regeneration.

---

### Strategy Rationale

**Assessment.** The intent is accepted with a narrow reframe that widens the touch set. EV is clearly positive. flow.py
has had 44 commits since 2026-10-01 and forces flows to run one at a time, so splitting it pays back on every later
flow. A move-only split is unusually verifiable: the AST snapshot turns "no behaviour change" into a mechanical check.
The reframe only declares files the intent's `scope` misses but its goals require. These are the complexity-gate
snapshot keyed by `sandbox_executor/flow.py`, 10 more test files carrying 115 patch and `setattr` sites, the `lint` task
line, the ratchet script and its budget file. Leaving them out would make acceptance criteria 3 and 4 impossible.

**Key strategic choice: generate, don't hand-edit.** Every definition is copied as an exact source slice by a scratch
generator that is never committed. This makes the move deterministic and re-runnable after a rebase, which is the main
defence against the hot-file conflict risk. The committed proof test is independent of the generator: it compares
`ast.dump` digests from a snapshot of the original blob.

**Why not decompose.** Summed step entropy is `0.8 + 3.5 + 1.2 + 3.0 + 2.5 + 1.3 + 0.6 + 0.8 = 13.7`, under the 15.0
budget. The core change is also atomic: deleting flow.py, the re-exports, the patch retargeting and the complexity
snapshot re-key must land together, or the tree does not import or test. Child intents would each need a green
intermediate state, which only exists if a temporary `flow.py` shim is added and later removed. That means more total
churn, about 3x the pipeline cost, and more rebase exposure on the hottest file. Steps 5–7 could technically be
children, but each is cheap and needs the final sizes and layout.

**Aggregation.**

- `entropy_pred` is the sum of the step entropies, 13.7.
- `cost_pred` is the sum of the step costs, `3.0 + 8.0 + 3.0 + 6.0 + 5.0 + 2.5 + 1.0 + 2.5 = 31.0`.
- `impact_pred` (85) is a qualitative judgement of the end state. The parallel flows it unlocks are worth more than the
  sum of the step impacts, which count only local deliverables.
- `learning_value_pred` (6) is the maximum step value (Steps 1 and 4), since those insights carry over to later splits.
- `p_success_pred` (0.70) is not the naive product (`≈ 0.36`), because the steps are retryable and regeneration makes
  Steps 2–3 idempotent. It is set by the bottleneck pair, Step 4 at 0.78 and Step 2 at 0.80, which are correlated (both
  depend on the Step 1 map), discounted for rebase risk on the hot file.
- `EV = 0.70 * 85.0 + 0.5 * 6.0 - 0.3 * 13.7 - 31.0 = 59.50 + 3.00 - 4.11 - 31.00 = 27.39`.

**Strong assumptions.**

- Python 3.13 `ast.dump` output is stable within the pinned minor version.
- No code uses dynamic `globals()`, `sys.modules[__name__]` or source-file lookups of flow.py. A grep during planning
  found none, and Step 1 re-checks.
- Changing the logger record names to submodule names is acceptable as "no behaviour change", because the logger
  hierarchy, levels and propagation are preserved.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1: prefix-based branches, rebase before execution and merge, no commits to
    `main`.
  - `constraints.md` §2 and `docs/safety.md` sandboxing: entropy 13.7 means the container sandbox tier, with no network
    access needed.
  - `constraints.md` §3: the three append-only ledgers are untouched; `ledger_reconcile.py` logic moves byte-for-byte,
    as the proof test shows.
  - `constraints.md` §4: core-module refactoring needs a deep model.
  - `holon-config/world/ruleset.md` §2: docstrings on new modules, no wildcard imports, typed signatures (unchanged
    bodies keep their annotations).
  - `ruleset.md` §3: test edits are declared explicitly in Steps 3–6, and assertions are not modified.
  - `docs/safety.md` §5: the human maintainer merges the root intent.
  - Planner rules: no new `# noqa` (moved suppressions keep the ratchet at 187/91), no new entrypoint imports into the
    flow engine, and one focused module per cluster.
- **Potential violations or edge cases:**
  - Re-export via `__all__` versus a ruff F401 noqa: `__all__` avoids any new suppression.
  - The late-binding line in `flow/__init__.py` is new plumbing, not a body change, and is excluded from the proof by an
    explicit allow-list.
  - Modules over about 400 lines (`review/loop.py` at about 1,000, and others) cannot shrink under move-only. This is
    accepted and flagged for follow-up.
  - The scope expands beyond the intent's `scope` list, as declared above.
  - Logger record names change.
- **Mitigations built into the plan:**
  - The deterministic, re-runnable generator.
  - The committed AST digest snapshot from the original blob.
  - The re-export completeness and cycle-binding tests.
  - The guard test against package-level patch targets.
  - Node-ID parity for the test split.
  - The ratchet wired into both `task lint` and pytest, so CI enforces it.
  - Ledger diff check before the PR.
- **Residual risk accepted (and why):** a patch retargeted to the wrong lookup site could pass silently. The hermetic
  guards, the baseline outcome comparison and the existing call-count assertions reduce this, but cannot rule it out, so
  it is accepted. The alternative, keeping the monolith, has a known recurring cost on every flow. There is also a risk
  of concurrent main changes to flow.py during review; it is handled by regeneration, not by hand-merging.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 13.7
- **Budget Compliance:** The strategy fits within budget, at 13.7 of 15.0, so there is no sub-intent list.

## Sub-intent list

- None (the plan fits the entropy budget and the core move is atomic)
