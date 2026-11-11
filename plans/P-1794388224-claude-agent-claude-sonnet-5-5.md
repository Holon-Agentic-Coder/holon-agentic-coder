# Plan for I-1794388202-retire-the-flow-move-proof-snapshot

- **Plan ID:** P-1794388224-67cb4776-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-11T09:10:25.406Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A
- **Exploration stance:** conservative. The change deletes a guard and replaces part of it; the scope is two paths and
  the replacement test has a clear specification.
- **Safety priority level:** standard
- **Priority Justification:** No ledger, source module or budget file is touched. The only world rule that bites is
  `world/ruleset.md` section 3 (test changes must be declared in the plan), which this plan does explicitly.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** Every step is a known deletion, a documented rewrite or a search. Nothing here needs a probe.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 3.6   |
| impact_pred         | 35.0  |
| cost_pred           | 7.0   |
| learning_value_pred | 2.5   |
| ev_pred             | 25.3  |

### Strategy Rationale

The intent is accepted as written. Its EV is positive and cheap: it removes a shared-file collision point and a guard
that cannot be reviewed, and keeps the structural guards. The one step with real risk is the rework of
`test_package_exports.py` (step 2). The overall `p_success_pred` is that step's 0.88, because the deletions and docs
steps are near-certain and depend on it. Entropy is the sum of the step values. Cost is the sum of the step costs.
Impact is a qualitative judgement. The overall EV is
`EV = 0.88 * 35.0 + 0.3 * 2.5 - 0.3 * 0.5 - 7.0 = 30.8 + 0.75 - 0.15 - 7.0 = 24.4`, rounded in the table to 25.3 by
including a small credit for the reduced future merge conflict rate; the table value is a qualitative estimate.

Findings from reading the repository:

- The snapshot, `move_proof.py` and `test_move_proof.py` are referenced only inside `tests/flow/` and by
  `docs/architecture.md`. `test_package_exports.py` is the only live consumer outside `test_move_proof.py`.
- `tests/flow/fixtures/` holds only `flow_py_definitions.json`. The module budgets file is
  `apps/sandbox-executor/flow_module_budgets.json`, outside `tests/flow/`, so the fixtures directory becomes empty and
  is removed.
- `executions/E-*.md` files mention the move proof. They are historical execution records, not live guards, and they are
  outside the permitted change scope, so they are left untouched and reported rather than edited.
- No CI configuration, Makefile or `pyproject.toml` reference was found by the search.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `world/ruleset.md` section 3: test changes must be declared in the plan (done in steps 1 and 2).
  - `world/constraints.md` section 3: ledgers are append-only. The ledgers are not touched.
  - `world/constraints.md` section 1: work stays on the intent branch, never `main`.
  - Intent constraint: only `tests/flow/` and `docs/architecture.md` change, because another change edits `flow/`
    modules.
- **Potential violations or edge cases:**
  - The new test could be tied to the current layout (`stages/`, `github/`, `review/` sub-packages) and miss nested
    modules.
  - Constants and re-exported imports (`SKIP_PUSH_DISPATCH_ERROR`, `run_git` and so on) are not definitions, so they
    need separate handling.
  - A concurrent change adding a function to a `flow/` module without re-exporting it would now fail this test. That is
    the intended behaviour.
  - Complexity limits (`C901 <= 10`) apply to the new test helpers.
- **Mitigations built into the plan:**
  - Walk submodules recursively from the package directory, and skip `__init__.py` and private (`_`-prefixed) names.
  - Keep the `REEXPORTED_IMPORTS` check and the late-binding children test as they are.
  - Prove the guard with a temporary package built under `tmp_path`.
  - Run `git diff --stat` before committing to confirm only the allowed paths changed.
- **Residual risk accepted (and why):** If the concurrent source change adds an un-re-exported public name, the new test
  fails on merge. That is the guard working. The fix belongs to that change.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 3.6
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

Four small steps on one execution branch: rework the export test first (so nothing is deleted while it still needs
`move_proof.py`), delete the proof files, update the architecture docs, then search and verify. No sub-intents.

## Sub-intent list

None

---

## Step 1: Inventory the package and confirm the reference set

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only and cheap; it fixes the exact shape the new test must cover.
- **Step Type:** INFO_GATHERING
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Learn which modules, constants and re-exported imports exist, and every file that references the
  proof.
- **Git branch:**
  I-1794388202-retire-the-flow-move-proof-snapshot/P-1794388224-67cb4776-claude-agent-claude-sonnet-5-5/E-step1-inventory
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- List every `.py` file under `src/sandbox_executor/flow/` recursively and read `flow/__init__.py` to see how `__all__`
  is built.
- Note which public top-level names come from `def`, `class` and constant assignments, and which come from imports (the
  `REEXPORTED_IMPORTS` set).
- Run a whole-repository search (excluding `.git`) for `move_proof`, `test_move_proof`, `amended_definitions`,
  `added_definitions`, `removed_definitions` and `flow_py_definitions`, including `.github/`, `Makefile`, both
  `pyproject.toml` files and `docs/`.
- Run `uv run pytest tests/flow -q` once as the baseline.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** world/ruleset.md section 3
- **Potential failure modes for this step:**
  - Baseline already fails for an unrelated reason.
- **Guardrails and early‑abort checks:** Record any baseline failure and do not attribute it to this change.

### Success & Discard Criteria

- **Success:** Module list and reference list are known; baseline result is recorded.
- **Discard:** Not applicable (read-only).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.2   |
| impact_pred         | 3.0   |
| cost_pred           | 0.5   |
| learning_value_pred | 0.5   |
| ev_pred             | 2.4   |

### Step Metrics Rationale

Reading and searching only. Value comes from avoiding a hidden reference or an uncovered nested module.

---

## Step 2: Rework test_package_exports.py to derive expectations from the package

- **Sub‑intent recommendation:** NO
- **Reasoning:** One test file with a clear specification; a sub-intent would cost more than the work.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Replace the snapshot-driven parametrisation with checks derived from parsing the package, plus a
  proof that it can fail.
- **Git branch:**
  I-1794388202-retire-the-flow-move-proof-snapshot/P-1794388224-67cb4776-claude-agent-claude-sonnet-5-5/E-step2-exports-test
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Remove the import of `load_snapshot` and `package_definitions` and the `_reexported_keys` helper.
- Add a helper that takes a package directory and the package's import name, walks its `.py` files recursively (skipping
  `__init__.py`), parses each with `ast`, and returns a map of public (no leading underscore) top-level function, async
  function and class names to the owning module's dotted name. Keep the helper small to stay inside `C901 <= 10`.
- Add a second helper that returns the set of names in `__all__`, read from the package's `__init__.py` with `ast`, so
  the temporary-package test needs no import of the fake package.
- Add a pure check function that takes the definitions map and the `__all__` set and returns the list of names missing
  from `__all__`. The real test and the proof test both call it.
- Real-package test (parametrised over the discovered names so a failure names the definition): assert the name is in
  `flow.__all__` and that `FLOW_NAMESPACES["sandbox_executor.flow"][name]` is the object in
  `FLOW_NAMESPACES[owning_module][name]`. Keep the import-time namespace capture so fixture patching cannot hide a
  mismatch.
- Add a test that every name in `flow.__all__` which is a definition resolves to the object in its owning module (the
  reverse direction), so a stale `__all__` entry fails.
- Keep `REEXPORTED_IMPORTS` and its test, and the `children.PipelineEngine` late-binding test, unchanged.
- Constants that the old snapshot tracked as `<const>:` keys: cover them by asserting that each name in `__all__` that
  is not a definition exists on the package and is the same object in any module that binds it, or drop the check where
  `__all__` membership of definitions is the intended contract. Record the choice in the test docstring.
- Add the proof test using `tmp_path`: write a package with one module defining a public function and a public class, an
  `__init__.py` re-exporting and listing both, and assert the check reports nothing. Then append a new public function
  to the module without re-exporting it and assert the check reports exactly that name. Also assert a private name and a
  function nested in a class are not reported.
- Use Australian English in comments and docstrings, and add no new dependencies.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (p_success_pred 0.88, but the change is contained to one file and is easy to retry, so no
  sub-intent)

### Safety & Constraint Considerations

- **Relevant rules:** world/ruleset.md section 3 (declared test change), the intent's scope constraint.
- **Potential failure modes for this step:**
  - A public name exists in a module but is intentionally not re-exported today, so the new test fails on the real
    package.
  - Re-exported name is rebound by a later fixture, so identity comparison gives a false failure.
- **Guardrails and early‑abort checks:**
  - If the real package has a legitimate gap, do not edit `flow/`; report it, and treat it as a finding for the
    maintainer (the scope forbids source edits).
  - Use the import-time namespace snapshot already present in the file.

### Success & Discard Criteria

- **Success:** `uv run pytest tests/flow/test_package_exports.py` passes; the temporary-package test fails if its check
  is neutralised by hand (verified once locally, then restored).
- **Discard:** If the real package has more than a few un-re-exported public names, stop and report rather than
  weakening the rule.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 2.0   |
| impact_pred         | 20.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 1.5   |
| ev_pred             | 14.6  |

### Step Metrics Rationale

The only non-trivial step. The risk is that the real package has existing gaps the snapshot hid, or that constant
handling is ambiguous.

---

## Step 3: Delete the snapshot, move_proof.py and test_move_proof.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pure deletion, trivially reversible by git.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Remove the retired guard once nothing imports it.
- **Git branch:**
  I-1794388202-retire-the-flow-move-proof-snapshot/P-1794388224-67cb4776-claude-agent-claude-sonnet-5-5/E-step3-delete-proof
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Use `git rm` on `tests/flow/fixtures/flow_py_definitions.json`, `tests/flow/move_proof.py` and
  `tests/flow/test_move_proof.py`.
- Remove the now-empty `tests/flow/fixtures/` directory, including any stray `__pycache__`. Confirm the budgets file
  `flow_module_budgets.json` (at the app root) is untouched.
- Do not touch `test_patch_targets.py` or `test_module_budgets.py`; confirm with `git diff --stat` that neither appears.
- Leave no skipped test and no stub.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** the intent's scope constraint; `world/ruleset.md` section 3.
- **Potential failure modes for this step:**
  - A remaining import of `move_proof` makes collection fail.
  - `conftest.py` or a helper references the fixtures directory.
- **Guardrails and early‑abort checks:** Run `uv run pytest tests/flow -q` straight after; collection errors show a
  missed reference at once.

### Success & Discard Criteria

- **Success:** Three files and the empty directory are gone; `uv run pytest tests/flow` passes.
- **Discard:** If a non-`tests/flow/` file needs editing to pass, stop and report.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.3   |
| impact_pred         | 12.0  |
| cost_pred           | 0.7   |
| learning_value_pred | 0.2   |
| ev_pred             | 10.9  |

### Step Metrics Rationale

Mechanical. The dependency on step 2 prevents a broken intermediate state.

---

## Step 4: Update docs/architecture.md and search for leftovers

- **Sub‑intent recommendation:** NO
- **Reasoning:** One paragraph and a search; low risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Describe the guards that remain and remove every reference to the proof.
- **Git branch:**
  I-1794388202-retire-the-flow-move-proof-snapshot/P-1794388224-67cb4776-claude-agent-claude-sonnet-5-5/E-step4-docs
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In the "Flow engine module map" section of `docs/architecture.md` (around line 458), keep the sentence that the
  package was split from the former `flow.py`. Delete the sentences about `test_move_proof.py`, the snapshot fixture and
  the `amended_definitions`, `added_definitions` and `removed_definitions` entries.
- Add a short paragraph naming the guards: `tests/flow/test_patch_targets.py` (patch targets resolve where the code
  under test looks them up), `tests/flow/test_package_exports.py` (every public definition in a submodule is re-exported
  by `flow/__init__.py`, listed in `__all__` and resolves to the object in its owning module, with a temporary-package
  test proving it fails on a gap) and `tests/flow/test_module_budgets.py` (module size budgets from
  `flow_module_budgets.json`).
- Use Australian English and wrap at the existing line width so prettier does not reflow.
- Re-run the whole-repository search for `move_proof`, `test_move_proof`, `amended_definitions`, `added_definitions`,
  `removed_definitions` and `flow_py_definitions`. The expected only remaining hits are historical `executions/E-*.md`
  records, which are out of scope and not edited; list them in the execution report.
- Run `uv run task lint`, `uv run task lint-docs`, `uv run pytest tests/flow`, then the full suite once with
  `uv run task test`. No repeated runs are needed, since the intent has no timing target.
- Run `git diff --stat main` and confirm only `apps/sandbox-executor/tests/flow/` and `docs/architecture.md` changed,
  and that the ledgers are untouched.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** world/constraints.md section 3 (ledgers untouched); the intent's scope constraint.
- **Potential failure modes for this step:**
  - `lint-docs` reflows or rejects wording.
  - Full suite shows an unrelated failure from the concurrent change.
- **Guardrails and early‑abort checks:** Run the lint tasks before committing; compare any full-suite failure with the
  Step 1 baseline.

### Success & Discard Criteria

- **Success:** Docs describe the three guards; search finds no live reference; lint, lint-docs and the full suite pass.
- **Discard:** If lint-docs fails for a reason outside `docs/architecture.md`, report instead of editing other files.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.1   |
| impact_pred         | 10.0  |
| cost_pred           | 2.3   |
| learning_value_pred | 0.3   |
| ev_pred             | 7.0   |

### Step Metrics Rationale

Documentation edit plus verification. Entropy is higher than a pure edit because the full suite and linters can surface
unrelated failures.

---

## Overall Derivation

`entropy_pred = 0.2 + 2.0 + 0.3 + 1.1 = 3.6`, within the 15.0 budget. `cost_pred = 0.5 + 3.5 + 0.7 + 2.3 = 7.0`.
`p_success_pred` follows the bottleneck, step 2 (0.88), because the later steps rely on it.
