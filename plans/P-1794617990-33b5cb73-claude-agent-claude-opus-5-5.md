# Plan for I-1794617704-touchset/P-1794617720-cbecf7aa-claude-agent-claude-opus-5-5--1-add-the-planner-touch-set-estimate-a-touch-set-p

- **Plan ID:** P-1794617990-33b5cb73-claude-agent-claude-opus-5-5
- **Parent Intent ID:** I-1794617704-touchset/P-1794617720-cbecf7aa-claude-agent-claude-opus-5-5/\_
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-14T00:59:51.669Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** None. One scope clarification only: glob-against-glob overlap (needed only by
  admission) is left to the sibling Child 2 intent, which owns `flow/admission.py`; this child ships parsing,
  normalisation, path matching and precision and recall scoring.
- **Exploration stance:** conservative. The parent plan (P-1794617720-cbecf7aa) already froze the format and design;
  this child is plain exploitation of existing seams (`is_metadata_path`, `_plan_decision_fields`, the calibration
  report dict), with the risk concentrated in two import-cycle and export-ratchet traps identified below.
- **Safety priority level:** standard
- **Priority Justification:** The child adds a new optional field to new `plans.jsonl` rows and new read-only report
  fields; it adds no gate, never blocks a flow and never rewrites ledger rows (`holon-config/world/constraints.md`
  section 3 is satisfied by appending new fields on new rows only). No `gh` or network access is involved.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** All unknowns worth learning (in-flight discovery) belong to Child 2. Here the value is in shipping
  the measurement quickly so the next flows start producing precision and recall data.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 8.25  |
| impact_pred         | 70.0  |
| cost_pred           | 13.0  |
| learning_value_pred | 4.0   |
| ev_pred             | 42.53 |

### Strategy Rationale

The intent is the first child of a split parent plan whose own entropy (17.0) exceeded 15.0. This child covers parent
Steps 1 to 4 plus its share of Steps 8 and 9. Re-reading the code surfaced three facts the parent plan did not price in,
and the steps below are shaped around them:

1. **Import cycle risk.** `sandbox_executor/flow/__init__.py` imports `parse_metrics` from `entrypoint/planner.py` at
   module level, and `flow/stages/calibrate.py` and `flow/stages/review.py` import `sandbox_executor.calibration` at
   module level. A top-level import of `sandbox_executor.flow.touch_set` from either `entrypoint/planner.py` or
   `calibration.py` would therefore execute `flow/__init__.py` mid-import and fail. Both call sites import lazily inside
   a small helper, the same pattern `planner.py` already uses for `check_and_resolve_stage_conflicts` (line 315).
   `flow/touch_set.py` itself may import `is_metadata_path` from `calibration.py` at top level (no cycle: calibration
   imports nothing from `flow`).
2. **Export ratchet.** `tests/flow/test_package_exports.py` fails when a new public function or class in a `flow`
   submodule is not re-exported by `flow/__init__.py`. The new public names must be imported and exported there, and the
   `__init__.py` line budget (425) raised by exactly the lines added.
3. **Complexity ratchet.** `planner.py main()`, `calibration.parse_actual_metrics` and `generate_calibration` already
   carry `noqa` complexity markers, so every new branch lives in a new small helper; the existing functions gain only a
   call line or a dict entry.

Aggregation: entropy is the sum of step entropies `1.5 + 2.0 + 0.5 + 2.0 + 1.5 + 0.75 = 8.25`; cost is the sum of step
costs `3.0 + 3.0 + 1.0 + 3.0 + 2.5 + 0.5 = 13.0`. Impact is a qualitative judgement for the delivered measurement loop
(70.0: it is the prerequisite for the Stage 3 admission gate and starts producing calibration data immediately), above
the largest single step (65.0). Learning value is a judgement slightly above the largest step (3.0) because the first
real precision and recall numbers will show how well planners estimate scope. `p_success` is set by the two bottleneck
steps (Step 2 at 0.85, Step 4 at 0.82); their raw product `0.85 * 0.82 = 0.70` is raised to 0.80 because the failure
modes are known in advance (cycle, ratchet, report snapshot tests) and the review loop repairs them cheaply.

`EV = 0.80 * 70.0 + 0.5 * 4.0 - 0.3 * 8.25 - 13.0 = 56.0 + 2.0 - 2.475 - 13.0 = 42.525` (rounded to 42.53).

Alternatives rejected:

- Putting the parser outside `flow/` to dodge the cycle: rejected, the intent names `flow/touch_set.py` and the parent
  design makes `flow` the owner for admission and enforcement in Child 2.
- Scoring precision per file instead of per declared entry: rejected; a directory or glob entry legitimately covers many
  files, so precision is "declared entries that matched at least one changed file over declared entries" and recall is
  "changed non-metadata files covered by some entry over changed non-metadata files", as in the parent design.
- Failing the plan when `## Touch Set` is missing: rejected; older planner images and models must keep working, so the
  row records `touch_set: null` and calibration reports `n/a`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` section 3 (Ledger Immutability): `touch_set` appears only on newly appended
    `plans.jsonl` rows; no historic row is rewritten; still exactly three ledgers.
  - `holon-config/world/constraints.md` section 1: commits only on the execution branch; merging stays with the parent
    flow and the human maintainer.
  - `holon-config/world/ruleset.md` sections 2 and 3: typed, docstringed public functions; tests declared in this plan,
    run with pytest only.
  - `docs/safety.md` "Human review is the final gate" and "Failure containment": the new code is advisory and never
    blocks a stage; parsing errors degrade to `null` and `n/a`.
  - Repository limits: ruff `C901 <= 10`, `PLR0912 <= 12`, `PLR0913 <= 5`, `PLR0915 <= 50`, no new inline `noqa`;
    `flow_module_budgets.json`; no new imports from entrypoints into the flow engine; no new dependencies.
- **Potential violations or edge cases:**
  - Circular import between `flow/__init__.py`, `entrypoint/planner.py` and `calibration.py` (see rationale).
  - `test_package_exports.py` ratchet and the `__init__.py` line budget.
  - Prompt template contract tests (`tests/test_prompt_templates.py`) and calibration report tests
    (`tests/test_calibration.py`) pinning text.
  - The planner prompt already says "The plan lists its touch set at module granularity" without saying where; the new
    section must agree with it rather than add a second, conflicting instruction.
- **Mitigations built into the plan:**
  - Lazy imports in two helpers; a test imports `sandbox_executor.entrypoint.planner` and `sandbox_executor.calibration`
    in a fresh interpreter (subprocess with `-c`) to prove no cycle.
  - Re-exports and an exact budget raise with one sentence appended to the budgets description.
  - Affected tests are run before the full suite.
- **Residual risk accepted (and why):** planners will under-declare at first (low recall). That is the very signal this
  child exists to measure; nothing is blocked by it until Child 2 lands.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 8.25
- **Budget Compliance:** The strategy fits within budget (8.25 at or below 15.0); it is executed as one plan with no
  sub-intent list (this intent is already at sub-intent depth 1).

## Plan Description & Strategy

Ship the touch set as data: one pure module that parses and scores it, one planner hook that records it on the plan row,
one documentation update, and one calibration hook that reports precision and recall. Every step keeps existing complex
functions unchanged except for a single call line, and all tests are hermetic (temporary directories and fixture
strings; no `gh`, no network, no real agent).

Touch-set format (frozen from the parent design): a level-2 heading `## Touch Set`; each top-level bullet holds one
backticked repository-relative POSIX path or glob; a trailing `/` means the whole directory; `*` and `**` use
`fnmatch`-style matching with `**` spanning directories; optional trailing text `symbols: a, b` records symbols for
large modules (stored and reported, never used for scoring). Indented bullets are details and ignored. A first item
whose first word is a placeholder (`none`, `n/a`, `na`, `nil`, `no`, `not`) gives an empty set. Metadata paths
(`is_metadata_path`: `plans/`, `executions/`, `holon-knowledge/`, `.holon/` and the empty-diff excludes) are dropped.

## Touch Set

- `apps/sandbox-executor/src/sandbox_executor/flow/touch_set.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/__init__.py`
- `apps/sandbox-executor/src/sandbox_executor/entrypoint/planner.py` symbols: \_touch_set_field, main
- `apps/sandbox-executor/src/sandbox_executor/calibration.py` symbols: parse_actual_metrics, generate_calibration,
  format_markdown_report, CalibrationReport
- `apps/sandbox-executor/flow_module_budgets.json`
- `apps/sandbox-executor/tests/flow/test_touch_set.py`
- `apps/sandbox-executor/tests/test_planner_touch_set.py`
- `apps/sandbox-executor/tests/test_calibration_touch_set.py`
- `holon-config/prompts/planner.template.md`
- `docs/ledger_schema.md`

---

## Step 1: Add the pure touch-set module `flow/touch_set.py` and export it

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small pure module (target under 180 lines) with no I/O; every other step depends on it, but it is
  low-risk and fully unit-testable.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** One owner for parsing, normalising, matching and scoring touch sets.
- **Git branch:** this plan's execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `flow/touch_set.py` with a module docstring naming its consumers (planner row, Stage 5 calibration, and later
   admission and enforcement). Standard-library imports only (`fnmatch`, `re`, `dataclasses`) plus `is_metadata_path`
   from `sandbox_executor.calibration`.
2. Frozen dataclass `TouchSetEntry` (`path`, `symbols` tuple) and frozen dataclass `TouchSet` (tuple of entries) with
   `to_row()` returning a list of objects (`path`, plus `symbols` only when non-empty) and a classmethod `from_row()`
   tolerating `None`, a list of strings, or a list of objects (unknown shapes give `None`, never raise).
3. `normalise_touch_path(path)`: strip whitespace and backticks, drop a leading `./` or `/`, resolve a git rename arrow
   to its target side (same rule as `is_metadata_path`), convert backslashes to `/`.
4. `parse_touch_set_section(plan_markdown)`: find the `## Touch Set` heading (case-insensitive, level 2 exactly), read
   top-level bullets or numbered items until the next level-2 heading, take the first backticked span as the path
   (falling back to the first whitespace token), parse an optional `symbols:` suffix, treat a placeholder first word as
   an empty set, drop metadata paths, de-duplicate preserving order. Return `None` when the heading is absent. Split
   into two or three private helpers so each stays under `C901 <= 10`.
5. `entry_matches(entry, path)`: directory prefix match for a trailing `/`; `fnmatch` for entries containing `*`, `?` or
   `[` (with `**/` also matching zero directories); exact equality otherwise.
6. Frozen dataclass `TouchSetScore` (`precision`, `recall` as optional floats, `unpredicted_paths`, `unused_entries`)
   and `score_touch_set(touch_set, changed_paths)`: drop metadata from the changed paths; precision is matched entries
   over entries, recall is covered paths over paths; each is `None` when its denominator is zero. `to_dict()` gives the
   report and ledger shape.
7. In `flow/__init__.py`, import and add the public names (`TouchSet`, `TouchSetEntry`, `TouchSetScore`,
   `parse_touch_set_section`, `normalise_touch_path`, `entry_matches`, `score_touch_set`) to the exports, matching the
   existing import and `__all__` style there.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` section 2 (typing, docstrings); no new dependencies.
- **Potential failure modes for this step:** `test_package_exports.py` failing on an unexported name; `fnmatch` treating
  `*` as crossing `/` and over-matching.
- **Guardrails and early‑abort checks:** run `uv run pytest apps/sandbox-executor/tests/flow/test_package_exports.py`
  right after adding the exports; table-driven matching tests in Step 5.

### Success & Discard Criteria

- **Success:** Module importable, all public names exported, ruff clean without `noqa`.
- **Discard:** If `**` semantics push a helper over complexity limits after two attempts, support only exact paths,
  trailing-`/` directories and single-segment `*`, and record the limitation in the module docstring.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.5   |
| impact_pred         | 60.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 52.75 |

### Step Metrics Rationale

`EV = 0.92 * 60.0 + 0.5 * 2.0 - 0.3 * 1.5 - 3.0 = 55.2 + 1.0 - 0.45 - 3.0 = 52.75`. Pure logic on a frozen format; the
only non-obvious risk is the export ratchet, which is known in advance.

---

## Step 2: Teach the planner to declare the touch set and record it on the plan row

- **Sub‑intent recommendation:** NO
- **Reasoning:** Two small edits (prompt template and one helper in the planner entrypoint); moderate risk from the
  import cycle and template contract tests, both mitigated.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** New plan files carry `## Touch Set`, and new `plans.jsonl` rows carry `touch_set` (a list, or `null`
  when the section is missing).
- **Git branch:** this plan's execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `holon-config/prompts/planner.template.md`, extend the existing bullet "The plan lists its touch set at module
   granularity ..." to say where: a `## Touch Set` section placed after `## Plan Description & Strategy`, one backticked
   path or glob per top-level bullet, trailing `/` for a whole directory, optional `symbols:` for very large modules,
   excluding `plans/`, `executions/` and `holon-knowledge/`. Add a `## Touch Set` block with one example bullet to the
   plan format skeleton in the same template. Australian English.
2. In `entrypoint/planner.py`, add `_touch_set_field(plan_md_path)` next to `_plan_decision_fields`: read the
   prettier-converged plan file, lazily import `parse_touch_set_section` from `sandbox_executor.flow.touch_set` inside
   the function (comment: a top-level import would cycle through `flow/__init__.py`), return a dict with key `touch_set`
   holding `to_row()` output or `None`; on a missing section print one warning line to stderr. Any parse exception is
   caught and logged and gives `None` (the plan never fails because of this field).
3. In `main()`, add a single `plan_entry.update(_touch_set_field(plan_md_path))` line directly after the existing
   `_plan_decision_fields` update, so `main()` gains no branches.
4. Run `uv run pytest apps/sandbox-executor/tests/test_prompt_templates.py apps/sandbox-executor/tests/test_planner.py`
   and update any pinned template text only where the test asserts the exact block that changed.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** constraints section 3 (new field on new rows only); "Add no new imports from entrypoints into the
  host flow engine" (this is the allowed direction, entrypoint importing flow, and it is lazy).
- **Potential failure modes for this step:** circular import at planner start-up; contract tests pinning template text;
  prettier escaping in the template example.
- **Guardrails and early‑abort checks:** a fresh-interpreter import test (Step 5); run `uv run task lint-docs` after
  editing the template.

### Success & Discard Criteria

- **Success:** A planner run against a fixture plan with the section writes the parsed list on the row; without the
  section it writes `null`; existing planner tests still pass.
- **Discard:** If a contract test forbids template changes outside its pinned text and cannot be updated within this
  touch set, stop and report rather than editing unrelated tests.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 2.0   |
| impact_pred         | 65.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 53.15 |

### Step Metrics Rationale

`EV = 0.85 * 65.0 + 0.5 * 3.0 - 0.3 * 2.0 - 3.0 = 55.25 + 1.5 - 0.6 - 3.0 = 53.15`. Highest impact step (it creates the
data), lower p than Step 1 because it spans the prompt and the entrypoint and meets the cycle trap.

---

## Step 3: Document the `touch_set` field in `docs/ledger_schema.md`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Documentation only; cheap and low risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Readers of the ledger know the field's shape, meaning, null semantics and consumers.
- **Git branch:** this plan's execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add `touch_set` to the Stage 2 row of the Flow Stage Record Matrix (the list of plan row fields).
2. In the "Plan Selection Ledger (`plans.jsonl`)" section, add a short paragraph and an example: a list of objects with
   `path` (repository-relative POSIX path, directory ending in `/`, or glob) and optional `symbols`; metadata paths are
   never listed; `null` on rows from plans without the section and absent on historic rows; read by Stage 5 calibration
   (precision and recall) and, once Child 2 lands, by admission and enforcement.
3. Add the calibration report fields (`touch_set_precision`, `touch_set_recall`, `touch_set_unpredicted_paths`,
   `touch_set_unused_entries`) wherever the calibration report fields are documented in the same file.
4. Run `uv run task lint-docs` and let prettier settle the wide table row.

### Dependencies & Criticality

- **Depends on:** Step 1 (shape), Step 4 (field names)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` (documentation must match behaviour); markdown hygiene (underscored names inside
  code spans).
- **Potential failure modes for this step:** prettier churn on the very wide matrix row.
- **Guardrails and early‑abort checks:** run prettier `--write` on the single file, then `--check` across the repo.

### Success & Discard Criteria

- **Success:** `uv run task lint-docs` passes and the field is documented in both places.
- **Discard:** Not applicable; failures are fixed in place.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 0.5   |
| impact_pred         | 25.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 22.6  |

### Step Metrics Rationale

`EV = 0.93 * 25.0 + 0.5 * 1.0 - 0.3 * 0.5 - 1.0 = 23.25 + 0.5 - 0.15 - 1.0 = 22.6`. Required by the intent; low impact
on its own.

---

## Step 4: Score the touch set in the Stage 5 calibration report and metrics

- **Sub‑intent recommendation:** NO
- **Reasoning:** Touches the largest module (`calibration.py`, 1678 lines) but only through call sites and one helper;
  moderate risk from report text tests.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Each calibration report and its structured data carry touch-set precision, recall, unpredicted paths
  and unused entries, or `n/a` when the plan declared no touch set.
- **Git branch:** this plan's execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `parse_actual_metrics`, inside the existing numstat loop that already skips metadata paths, append each counted
   `file_path` to a list stored as `exec_meta["changed_paths"]` (one append line plus one initialisation line; the
   shortstat fallback leaves the list empty, which scores as `None`).
2. Add a small helper in `calibration.py`, `_touch_set_metrics(plan_meta, changed_paths)`, that lazily imports
   `TouchSet` and `score_touch_set` from `sandbox_executor.flow.touch_set` (comment explaining the cycle), builds the
   touch set with `TouchSet.from_row(plan_meta.get("touch_set"))` and returns the score dict, or a dict of `None` values
   when the plan has no touch set. `plan_meta` is the record `parse_predicted_metrics` already returns (the
   `plans.jsonl` row); when it came from the markdown fallback, parse the plan file's `## Touch Set` section instead.
3. In `generate_calibration`, add one `"touch_set": _touch_set_metrics(...)` entry to `report_dict` and an optional
   `touch_set` field (default `None`) on the `CalibrationReport` dataclass so the Stage 5 payload and any ledger row
   built from the report carry it.
4. Add a private `_touch_set_report_lines(report_data)` helper that renders a short "Touch Set Accuracy" section
   (precision, recall as percentages or `n/a`, then bullet lists of unpredicted paths and unused entries, capped at 20
   items each with a "and N more" line); `format_markdown_report` calls it with a single `lines.extend(...)` before the
   closing sections. Use code spans for every path and underscored name (markdown hygiene).
5. Run
   `uv run pytest apps/sandbox-executor/tests/test_calibration.py apps/sandbox-executor/tests/flow/stages/test_calibrate.py`
   and adjust only assertions that snapshot the full report text.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** constraints section 3 (calibration rows appended, never rewritten); `docs/safety.md` "Failure
  containment" (a scoring error must not fail Stage 5).
- **Potential failure modes for this step:** circular import; report snapshot tests; the helper raising on malformed
  historic rows and failing calibration.
- **Guardrails and early‑abort checks:** the helper catches `ValueError` and `TypeError` from malformed rows and returns
  `None` values; lazy import verified by the fresh-interpreter test.

### Success & Discard Criteria

- **Success:** A fixture calibration reports precision and recall values matching hand-computed numbers; a historic plan
  row reports `n/a`; existing calibration tests pass.
- **Discard:** If wiring the score into `CalibrationReport` breaks serialisation consumers beyond this touch set, keep
  the score in `report_dict` and the markdown only, and note the gap for Child 2.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 2.0   |
| impact_pred         | 60.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 47.1  |

### Step Metrics Rationale

`EV = 0.82 * 60.0 + 0.5 * 3.0 - 0.3 * 2.0 - 3.0 = 49.2 + 1.5 - 0.6 - 3.0 = 47.1`. Lowest p in the plan: the module is
large, its main functions already carry complexity suppressions, and report text tests may pin the layout.

---

## Step 5: Hermetic tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tests sit beside the code each step covers; fixtures are plain strings and temporary directories.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Cover every acceptance criterion of this child without `gh`, network or a real agent.
- **Git branch:** this plan's execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `tests/flow/test_touch_set.py`: section present, absent (`None`), placeholder (`None` item gives an empty set),
   nested bullets ignored, numbered items, `symbols:` parsing, metadata paths dropped, duplicates removed, stops at the
   next level-2 heading; `normalise_touch_path` cases (`./`, leading `/`, rename arrow, backslashes); `entry_matches`
   table (exact, directory, `*`, `**` at zero and many depths, no match); `score_touch_set` with hand-computed precision
   and recall, zero denominators, metadata-only diffs; `to_row` and `from_row` round trip plus tolerant inputs (`None`,
   list of strings, malformed).
2. `tests/test_planner_touch_set.py`: `_touch_set_field` on a temporary plan file with and without the section; a
   subprocess run of `python -c` importing `sandbox_executor.entrypoint.planner` first and, separately,
   `sandbox_executor.calibration` first, both succeeding (cycle guard).
3. `tests/test_calibration_touch_set.py`: `_touch_set_metrics` with a row that has a touch set, a historic row without
   one, and a malformed one; `format_markdown_report` output contains the section with values and with `n/a`; the
   changed-paths list excludes metadata (use the existing numstat-style fixtures in `tests/test_calibration.py` as the
   pattern).
4. Run the affected tests first:
   `uv run pytest apps/sandbox-executor/tests/flow/test_touch_set.py apps/sandbox-executor/tests/test_planner_touch_set.py apps/sandbox-executor/tests/test_calibration_touch_set.py apps/sandbox-executor/tests/flow/test_package_exports.py apps/sandbox-executor/tests/test_calibration.py apps/sandbox-executor/tests/test_planner.py apps/sandbox-executor/tests/test_prompt_templates.py`,
   then the full suite once with `uv run task test`. No repeated runs: the intent states no timing or performance
   target.

### Dependencies & Criticality

- **Depends on:** Steps 1, 2 and 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` section 3 (tests declared in the plan, pytest only); the
  repository's network and `gh` write guards stay active.
- **Potential failure modes for this step:** the subprocess cycle test picking up a different interpreter.
- **Guardrails and early‑abort checks:** use `sys.executable` for the subprocess; no test touches the real ledger files.

### Success & Discard Criteria

- **Success:** All new tests pass; the full suite passes once.
- **Discard:** A test that needs anything non-hermetic is rewritten against fixtures, never skipped.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.5   |
| impact_pred         | 50.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 40.55 |

### Step Metrics Rationale

`EV = 0.85 * 50.0 + 0.5 * 2.0 - 0.3 * 1.5 - 2.5 = 42.5 + 1.0 - 0.45 - 2.5 = 40.55`. Pure functions and string fixtures
keep cost low.

---

## Step 6: Budgets, lint and verification

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mechanical closure.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Keep `uv run task lint`, `uv run task lint-docs` and the flow module budget check green.
- **Git branch:** this plan's execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/flow_module_budgets.json`, add `touch_set.py` at its creation size and raise `__init__.py`
   by exactly the lines added in Step 1; append one sentence to the description ("touch_set.py was added at creation
   size to parse and score the planner's touch set; the budget of `__init__.py` was raised by N lines to import and
   export its public names, as the package export test requires").
2. Run `uv run task lint` and `uv run task lint-docs`; fix findings without new `noqa`.
3. Check Australian English in new prose (normalise, behaviour, recognise).
4. Compare `git diff --name-only` against this plan's `## Touch Set` by hand (dogfooding the future enforcement rule);
   any extra non-metadata file is either justified in the execution notes or removed.

### Dependencies & Criticality

- **Depends on:** Steps 1 to 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** repository lint rules; `uv.lock` unchanged (no new dependencies).
- **Potential failure modes for this step:** off-by-one budget values.
- **Guardrails and early‑abort checks:** compute line counts with `wc -l` after the final formatting pass.

### Success & Discard Criteria

- **Success:** Both lint tasks pass and the diff stays within the declared touch set.
- **Discard:** Not applicable; failures are fixed in place.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 0.75  |
| impact_pred         | 35.0  |
| cost_pred           | 0.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 30.58 |

### Step Metrics Rationale

`EV = 0.88 * 35.0 + 0.5 * 1.0 - 0.3 * 0.75 - 0.5 = 30.8 + 0.5 - 0.225 - 0.5 = 30.575` (rounded to 30.58). Routine but
mandatory for merge into the parent branch.

---
