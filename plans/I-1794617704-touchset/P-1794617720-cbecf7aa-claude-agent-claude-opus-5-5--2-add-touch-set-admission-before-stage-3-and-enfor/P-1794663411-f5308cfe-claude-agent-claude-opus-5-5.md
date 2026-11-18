# Plan for I-1794617704-touchset/P-1794617720-cbecf7aa-claude-agent-claude-opus-5-5--2-add-touch-set-admission-before-stage-3-and-enfor

- **Plan ID:** P-1794663411-f5308cfe-claude-agent-claude-opus-5-5
- **Parent Intent ID:** I-1794617704-touchset/P-1794617720-cbecf7aa-claude-agent-claude-opus-5-5/\_
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-14T13:36:52.673Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** None. One clarification of placement: discovery of in-flight flows goes into a
  focused `flow/in_flight.py` and post-Stage 3 enforcement into `flow/stages/touch_set_guard.py`, with
  `flow/admission.py` owning the comparison, the decision and the refusal text, so no module grows past ~400 lines and
  `stages/execute.py` (at its 416-line budget) only gains short hooks.
- **Exploration stance:** balanced. Discovery of "in flight" (Step 2) is the one real unknown and is explored with a
  fail-closed fallback; everything else reuses Child 1's `touch_set.py` and the existing over-budget re-plan path.
- **Safety priority level:** elevated
- **Priority Justification:** The feature gates Stage 3 dispatch and appends a new row kind to the append-only
  `executions.jsonl` (`holon-config/world/constraints.md` section 3), and it calls `gh` host-side, which must never run
  from a role container or from tests (`docs/safety.md` sandboxing; `tests/gh_write_guard.py`,
  `tests/network_guard.py`). A wrong gate either blocks all flows or silently admits overlapping ones.

## Exploration

- **Proportion of steps that are exploratory:** 0.14 (1 of 7 steps)
- **Justification:** Child 1 already landed parsing, matching and scoring (`sandbox_executor/touch_set.py`), so the
  remaining uncertainty is concentrated in how reliably open pull requests and local checkpoints describe in-flight
  flows. That step is EXPLORATORY with a bounded cost; the rest is exploitation of known patterns.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.72  |
| entropy_pred        | 11.0  |
| impact_pred         | 82.0  |
| cost_pred           | 16.0  |
| learning_value_pred | 6.0   |
| ev_pred             | 42.74 |

`EV = 0.72 * 82.0 + 0.5 * 6.0 - 0.3 * 11.0 - 16.0 = 59.04 + 3.0 - 3.3 - 16.0 = 42.74`

## Plan Description & Strategy

Child 1 (merged into the target branch) gave every plan row a parsed `touch_set` and `sandbox_executor/touch_set.py`
with `TouchSet`, `TouchSetEntry`, `normalise_touch_path`, `entry_matches` and `score_touch_set`, plus
`git.is_metadata_path`. This child adds the two uses of that data around Stage 3:

1. **Admission (before dispatch):** gather in-flight flows (open flow pull requests via host-side `gh`, plus local
   checkpoints in other worktrees), compare their declared touch sets and the real pull request diffs with this plan's
   declared set, and refuse Stage 3 with a message naming branches, pull requests and files. `--allow-overlap` admits
   the run and appends one `admission_override` row to `executions.jsonl`.
2. **Enforcement (after dispatch):** files in the execution diff outside the declared set (ignoring metadata) fail Stage
   3 with `touch_set_violation`, and the pipeline returns to Stage 2 once with feedback to widen the set; the next Stage
   3 re-runs admission.

Order: entry-overlap helper (pure) → discovery (exploratory, fail-closed) → admission decision and Stage 3 gate with the
CLI override → enforcement and pipeline re-plan → tests → budgets, docs and verification. All `gh` and `git` calls are
injected runners so tests stay hermetic. The plan fits the 15.0 entropy budget and runs as one plan with no sub-intent
list.

## Touch Set

- `apps/sandbox-executor/src/sandbox_executor/touch_set.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/admission.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/in_flight.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/stages/touch_set_guard.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/stages/execute.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/pipeline.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/context.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/__init__.py`
- `apps/sandbox-executor/src/sandbox_executor/cli/commands/flow.py`
- `apps/sandbox-executor/src/sandbox_executor/ledger.py`
- `apps/sandbox-executor/flow_module_budgets.json`
- `apps/sandbox-executor/tests/test_touch_set.py`
- `apps/sandbox-executor/tests/flow/test_admission.py`
- `apps/sandbox-executor/tests/flow/test_in_flight.py`
- `apps/sandbox-executor/tests/flow/stages/test_execute_touch_set.py`
- `apps/sandbox-executor/tests/flow/test_pipeline.py`
- `apps/sandbox-executor/tests/flow/test_context.py`
- `docs/touch_set.md`
- `docs/ledger_schema.md`

---

## Step 1: Add an entry-against-entry overlap helper to `touch_set.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, pure and fully unit-testable; it extends the module that owns the matching rules.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Decide whether two declared sets can touch a common file, since declared sets hold directories and
  globs, not only files.
- **Git branch:** this plan's execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add `entries_overlap(a, b)` to `sandbox_executor/touch_set.py`: two literal paths overlap when equal; a literal and a
   directory or glob overlap when `entry_matches` accepts the literal; two directories overlap when one is a prefix of
   the other; for glob against directory or glob, compare the literal prefixes up to the first glob character (one
   prefix must start the other) and treat that as a conservative overlap (false positives are safe; the user can
   override).
2. Add `overlapping_entries(own, other)` returning the pairs (own entry path, other entry path) that overlap, and
   `files_in_set(touch_set, paths)` returning the non-metadata paths some entry matches (reusing `normalise_touch_path`,
   `is_metadata_path` and `entry_matches`).
3. Add `out_of_set_files(touch_set, changed_paths)`: the normalised non-metadata changed paths no entry matches.
4. Keep the module pure (no I/O) and below ~300 lines; update the module docstring sentence that says admission "will"
   read the rows.

### Dependencies & Criticality

- **Depends on:** NONE (Child 1 is already merged into the target branch)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` section 2 (typed, documented public functions); ruff complexity
  limits.
- **Potential failure modes for this step:** glob-against-glob false negatives letting an overlap through.
- **Guardrails and early‑abort checks:** prefer the conservative prefix rule over clever glob intersection; tests cover
  `**`, `*` and directory cases.

### Success & Discard Criteria

- **Success:** New unit tests in `tests/test_touch_set.py` pass for literal, directory, glob and metadata cases.
- **Discard:** If precise glob intersection is attempted and exceeds complexity limits, fall back to the prefix rule.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.8   |
| impact_pred         | 50.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 44.26 |

### Step Metrics Rationale

`EV = 0.90 * 50.0 + 0.5 * 2.0 - 0.3 * 0.8 - 1.5 = 45.0 + 1.0 - 0.24 - 1.5 = 44.26`. Pure logic next to existing matching
code; low entropy.

---

## Step 2: Discover in-flight flows and their touch sets in `flow/in_flight.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** The riskiest piece, but already a child intent of depth 1 sized for it; splitting further would add a
  merge round without reducing the unknown. A fail-closed result keeps the downside bounded.
- **Step Type:** EXPLORATION
- **Exploration level:** EXPLORATORY

- **Hypothesis being tested:** Open pull requests whose head parses as a flow branch, plus checkpoints in other git
  worktrees whose stage is execute or review, identify every in-flight flow, and their plan rows yield declared touch
  sets via the existing ledger read helpers.
- **Learning target:** How often discovery mis-classifies (stale checkpoints, non-flow heads, plan rows without
  `touch_set`), recorded in the execution notes.
- **Maximum acceptable cost for this learning:** 4.0 cost units; abort the checkpoint half above 6.0 and keep only pull
  requests.

### Intent & Git Integration

- **Step Intent:** Return a list of `InFlightFlow` records (label naming the pull request number and head branch or the
  local branch, declared `TouchSet` or `None`, real changed files or `None`) or a typed `AdmissionUnavailable`.
- **Git branch:** this plan's execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `open_flow_pull_requests(repo_dir, run_gh)`: one `gh pr list --state open --json number,headRefName,baseRefName`
   call; keep heads that parse as flow branches using `ref_names` helpers; for each kept pull request,
   `gh pr diff <number> --name-only` gives real changed files (metadata dropped). Cap the count (for example 50) with a
   logged warning.
2. Declared set of a pull request: derive its plan branch from the head (drop the `E-.../_` segment), read the newest
   matching `plans.jsonl` row through the existing ref resolution and ledger read helpers used by
   `stages/plan_decision.py`, and rebuild with `TouchSet.from_row`. A missing ref yields `None` (only the real diff is
   compared); no fetch is attempted inside a mounted role container.
3. `local_in_flight_flows(repo_dir, run_git)`: `git worktree list --porcelain`; for every other worktree read
   `.holon/flow/` checkpoints through `checkpoint.load_checkpoint`; keep those whose current stage is execute or review
   and whose plan branch still exists locally; read their declared set the same way.
4. Exclude the current flow's own lineage (anything sharing the root intent prefix of `context.intent_branch`) and
   de-duplicate a local flow that also has an open pull request.
5. `gh` or `git` failure returns `AdmissionUnavailable(reason)` rather than raising.
6. Runners are parameters defaulting to the real host runners; nothing here runs in a role container.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (p 0.70; mitigated by the fail-closed result and the pull-request-only fallback)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` sandboxing (host-side `gh` only); `tests/gh_write_guard.py` and
  `tests/network_guard.py` (read-only `gh` and no network in tests); constraints section 1 (never check out other
  intents' branches: read refs and checkpoint files only).
- **Potential failure modes for this step:** stale checkpoints blocking flows; slow diff calls; forked heads.
- **Guardrails and early‑abort checks:** only flow-shaped heads count; cap on pull requests; no checkout or write to any
  other branch or worktree.

### Success & Discard Criteria

- **Success:** On fixtures (fake `gh`, a second worktree with a live and a stale checkpoint) discovery returns exactly
  the live flows, excludes own lineage and returns `AdmissionUnavailable` on a `gh` error.
- **Discard:** If checkpoint discovery proves unreliable, keep open pull requests only and record the gap in the
  execution notes and `docs/touch_set.md`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.70  |
| entropy_pred        | 3.5   |
| impact_pred         | 85.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 57.45 |

### Step Metrics Rationale

`EV = 0.70 * 85.0 + 0.5 * 6.0 - 0.3 * 3.5 - 4.0 = 59.5 + 3.0 - 1.05 - 4.0 = 57.45`. Highest entropy and learning value
of the plan: the definition of "in flight" is the main unknown.

---

## Step 3: Decide admission, gate Stage 3 and add `--allow-overlap`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Decision logic in a new module plus short hooks; risk lies in line budgets and ledger readers.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Stage 3 refuses before any worktree, container or token is spent when the plan overlaps an in-flight
  flow, unless `--allow-overlap`, which is recorded in the ledger.
- **Git branch:** this plan's execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `flow/admission.py`: `check_admission(context, run_gh=None, run_git=None)` reads the active plan row's `touch_set`
   (same read path as `_plan_budget_decision`), calls Step 2, and returns an `AdmissionDecision` (admitted flag,
   conflicts as label plus overlapping files or entry pairs, override flag). Comparison: own declared set against each
   other declared set (`overlapping_entries`), and own declared set against each pull request's real diff
   (`files_in_set`). Metadata never conflicts.
2. `format_refusal(decision)`: one message such as "Stage 3 refused: the touch set overlaps pull request #N (branch X)
   on A, B; finish or merge that flow first, or rerun with --allow-overlap." listing every conflict.
3. A plan whose row has no touch set is refused with a request to re-plan; `AdmissionUnavailable` fails closed with its
   reason; both are admitted under `--allow-overlap`.
4. `stages/execute.py`: one call after the plan budget decision and before child-branch snapshotting and dispatch; on
   refusal return `_stage_failure(..., dispatched=False)` with payload `admission_refused` and the conflicts. In
   `dry_run`, log the decision and continue. Keep the hook to about 6 lines by putting payload building in
   `admission.py`.
5. `FlowContext.allow_overlap: bool = False`, serialised in `to_dict` and read in `from_dict` with a false default for
   old checkpoints; `--allow-overlap` in `cli/commands/flow.py` with Australian English help text.
6. With the override and some conflict, append one `executions.jsonl` row on the plan branch through the existing ledger
   append helper: `kind: "admission_override"`, `plan_id`, `intent_id`, conflicts, `recorded_by: "flow"`, `created_at`.
   Check that `ledger.py` and ledger query readers skip rows with this `kind` (add a filter if not).
7. Re-export the public names from `flow/__init__.py` as the package export test requires.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** constraints section 3 (append-only ledgers: append a row, never edit one); `docs/safety.md` "Human
  review is the final gate" (refusal never closes or merges anything); flow module budgets.
- **Potential failure modes for this step:** `execute.py`, `context.py` and `__init__.py` exceed their budgets; the new
  row kind is miscounted as an execution by calibration or ledger queries.
- **Guardrails and early‑abort checks:** budgets raised by the exact lines added with an explanatory sentence; a test
  shows calibration and ledger queries ignore the override row.

### Success & Discard Criteria

- **Success:** With an overlapping fake pull request the dispatcher stub is never called and the message names the pull
  request, branch and files; with the flag it is called once and exactly one override row is appended.
- **Discard:** If readers cannot tolerate a new row kind, record the override as fields on the Stage 3 summary row
  instead.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.75  |
| entropy_pred        | 2.5   |
| impact_pred         | 80.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 57.25 |

### Step Metrics Rationale

`EV = 0.75 * 80.0 + 0.5 * 3.0 - 0.3 * 2.5 - 3.5 = 60.0 + 1.5 - 0.75 - 3.5 = 57.25`. Delivers most of the intent's value;
risk is in budgets and ledger readers, both checked mechanically.

---

## Step 4: Enforce the declared touch set after Stage 3 and re-plan once

- **Sub‑intent recommendation:** NO
- **Reasoning:** Reuses the execution diff Stage 3 already computes and the bounded over-budget re-plan path.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** An execution diff outside the declared set (ignoring metadata) fails Stage 3 and returns once to
  Stage 2 with the files as feedback; admission re-runs before the next dispatch.
- **Git branch:** this plan's execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. New `flow/stages/touch_set_guard.py`: `touch_set_violation(context, changed)` reads the declared set and returns the
   out-of-set files via `out_of_set_files`, or nothing; `touch_set_violation_result(...)` builds the failed
   `StageResult` with payload `touch_set_violation` (files), `replan_requested: true` and writes a superseding failed
   summary row in the same shape the empty-diff guard uses (share or move that row-building helper rather than copy it).
2. `stages/execute.py`: right after the non-empty `changed` check, a short hook calls the guard. Skipped when
   `allow_overlap` is set and the plan has no declared set (nothing to enforce against).
3. `pipeline.py`: generalise `_replan_after_over_budget` into one helper that also accepts `touch_set_violation`,
   choosing the feedback text (widen the touch set to include the listed files, or decompose the plan) and sharing the
   `MAX_REPLAN_ATTEMPTS` counter; the exhausted message names the files.
4. The failed execution branch is kept for the human; the next execution starts from the new plan branch.

### Dependencies & Criticality

- **Depends on:** Steps 1 and 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` failure containment (fail clearly, resumable); constraints section 3 (superseding
  rows are appended, never edits); `pipeline.py` at its 395-line budget.
- **Potential failure modes for this step:** re-plan loops; complexity of the generalised helper.
- **Guardrails and early‑abort checks:** bounded by `MAX_REPLAN_ATTEMPTS`; helper split to stay within `C901 <= 10`.

### Success & Discard Criteria

- **Success:** A fake executor editing an undeclared file fails Stage 3 with the file named and the pipeline returns to
  Stage 2 once; a metadata-only extra file passes; existing over-budget re-plan tests stay green.
- **Discard:** If the pipeline change cannot stay within complexity limits, keep fail-only (no automatic re-plan) and
  record it in the execution notes.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.75  |
| entropy_pred        | 2.0   |
| impact_pred         | 60.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 43.4  |

### Step Metrics Rationale

`EV = 0.75 * 60.0 + 0.5 * 4.0 - 0.3 * 2.0 - 3.0 = 45.0 + 2.0 - 0.6 - 3.0 = 43.4`. The fail-only fallback keeps the
downside small.

---

## Step 5: Hermetic tests for admission, override and enforcement

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tests sit with the code they cover; reuse of `tests/flow/fake_gh.py` and existing stage fixtures keeps
  cost low.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Cover every acceptance criterion with no real `gh`, network or role container.
- **Git branch:** this plan's execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `tests/test_touch_set.py`: `entries_overlap`, `overlapping_entries`, `files_in_set` and `out_of_set_files` for
   literal, directory, `*`, `**` and metadata cases.
2. `tests/flow/test_in_flight.py`: fake `gh` listing flow and non-flow heads; own-lineage exclusion; a second worktree
   (existing worktree fixtures) with live and stale checkpoints; `gh` failure returns `AdmissionUnavailable`.
3. `tests/flow/test_admission.py`: overlap against a declared set, against a real pull request diff, metadata-only
   overlap admitted, missing touch set refused, refusal text names pull request, branch and files.
4. `tests/flow/stages/test_execute_touch_set.py`: refusal happens before the dispatcher stub is called;
   `--allow-overlap` dispatches and appends exactly one `admission_override` row; out-of-set diff fails with
   `touch_set_violation`; metadata-only extras pass.
5. `tests/flow/test_pipeline.py`: violation returns to Stage 2 once, then fails clearly; `tests/flow/test_context.py`:
   `allow_overlap` round-trips and defaults to false for old checkpoints. Add a CLI option test where the existing CLI
   tests live if one is needed.
6. Run affected tests first:
   `uv run pytest apps/sandbox-executor/tests/test_touch_set.py apps/sandbox-executor/tests/flow/test_in_flight.py apps/sandbox-executor/tests/flow/test_admission.py apps/sandbox-executor/tests/flow/stages/test_execute_touch_set.py apps/sandbox-executor/tests/flow/test_pipeline.py apps/sandbox-executor/tests/flow/test_context.py`,
   then the full suite once with `uv run task test`. No repeated runs: the intent sets no timing target.

### Dependencies & Criticality

- **Depends on:** Steps 1 to 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` section 3 (test changes declared here); hermetic guards
  (`tests/gh_write_guard.py`, `tests/network_guard.py`, `tests/test_sandbox_hermetic_guard.py`).
- **Potential failure modes for this step:** a test accidentally reaching real `gh`; worktree fixtures being slow.
- **Guardrails and early‑abort checks:** inject runners everywhere; reuse `worktree_fixtures.py`.

### Success & Discard Criteria

- **Success:** All new and existing tests pass; the slowest-tests CI gate is not tripped.
- **Discard:** Not applicable; failures are fixed in place.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.2   |
| impact_pred         | 55.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 44.89 |

### Step Metrics Rationale

`EV = 0.85 * 55.0 + 0.5 * 2.0 - 0.3 * 1.2 - 2.5 = 46.75 + 1.0 - 0.36 - 2.5 = 44.89`. Known fixtures; moderate cost.

---

## Step 6: Document admission, override and enforcement

- **Sub‑intent recommendation:** NO
- **Reasoning:** Short doc additions in the existing touch-set and ledger schema docs.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Operators can read what blocks Stage 3, how to override it and what gets recorded.
- **Git branch:** this plan's execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `docs/touch_set.md`: replace "will read the same rows later" with sections on admission (what counts as in flight,
   what is compared, the refusal), `--allow-overlap`, and enforcement with the single re-plan.
2. `docs/ledger_schema.md`: document the `admission_override` row in `executions.jsonl` and the `touch_set_violation`
   status payload.
3. Australian English spelling; run `uv run task lint-docs` after edits.

### Dependencies & Criticality

- **Depends on:** Steps 3 and 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** docs lint (prettier); constraints section 3 (schema of append-only rows documented).
- **Potential failure modes for this step:** prettier churn.
- **Guardrails and early‑abort checks:** formulas and identifiers in code spans.

### Success & Discard Criteria

- **Success:** `uv run task lint-docs` passes and the docs describe the shipped behaviour.
- **Discard:** Not applicable.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.5   |
| impact_pred         | 20.0  |
| cost_pred           | 0.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 17.85 |

### Step Metrics Rationale

`EV = 0.90 * 20.0 + 0.5 * 1.0 - 0.3 * 0.5 - 0.5 = 18.0 + 0.5 - 0.15 - 0.5 = 17.85`. Cheap and low risk.

---

## Step 7: Budgets, lint and full verification

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mechanical closure.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Keep `uv run task lint`, `uv run task lint-docs`, the module budget check and the suite green.
- **Git branch:** this plan's execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `apps/sandbox-executor/flow_module_budgets.json`: add `admission.py`, `in_flight.py` and `stages/touch_set_guard.py`
   at creation size; raise `stages/execute.py`, `context.py`, `pipeline.py` and `__init__.py` by the exact lines added,
   appending one explanatory sentence to the description.
2. `uv run task lint` and `uv run task lint-docs`; fix findings without `noqa` (the existing `noqa` on
   `run_execute_stage` is not widened).
3. Full suite once with `uv run task test`.
4. Confirm the diff stays within `## Touch Set` (dogfooding the new enforcement by hand); no new dependencies, so
   `uv.lock` is unchanged.

### Dependencies & Criticality

- **Depends on:** Steps 1 to 6
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** repository lint rules; budget policy in `flow_module_budgets.json`.
- **Potential failure modes for this step:** budget check failures; complexity findings in `pipeline.py`.
- **Guardrails and early‑abort checks:** extract helpers instead of suppressing.

### Success & Discard Criteria

- **Success:** Lint, docs lint, budget check and full suite all pass.
- **Discard:** Not applicable; failures are fixed in place.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 0.5   |
| impact_pred         | 40.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 33.85 |

### Step Metrics Rationale

`EV = 0.85 * 40.0 + 0.5 * 2.0 - 0.3 * 0.5 - 1.0 = 34.0 + 1.0 - 0.15 - 1.0 = 33.85`. Routine but mandatory.

---

### Strategy Rationale

The intent is accepted as is: it is Child 2 of an already decomposed parent, Child 1 delivered the pure touch-set
primitives, and the remaining work (Steps 5 to 7 of the parent plus their tests and budgets) fits one plan. Two
placement choices raise success odds: discovery lives in `flow/in_flight.py` and enforcement in
`flow/stages/touch_set_guard.py`, so `stages/execute.py` and `pipeline.py`, both at their line budgets, only gain short
hooks.

Aggregation: entropy and cost are sums (`0.8 + 3.5 + 2.5 + 2.0 + 1.2 + 0.5 + 0.5 = 11.0`;
`1.5 + 4.0 + 3.5 + 3.0 + 2.5 + 0.5 + 1.0 = 16.0`). `p_success` is driven by the bottleneck chain Step 2 to Step 3 (0.70
and 0.75), lifted to 0.72 because Step 2 has a defined pull-request-only fallback and Step 4 a fail-only fallback, so a
partial success still ships a working gate. Impact (82.0) is the weighted value of the gate plus enforcement; learning
value (6.0) is Step 2's maximum. Overall `EV = 0.72 * 82.0 + 0.5 * 6.0 - 0.3 * 11.0 - 16.0 = 42.74`. Strong assumption:
Child 1's `touch_set` field is present on plan rows written since its merge, and older rows without it are handled
(refused unless overridden).

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:** `holon-config/world/constraints.md` section 1 (read other
  flows' refs and checkpoints only; never check out or commit to them), section 3 (append-only ledgers: the override and
  superseding rows are appends); `docs/safety.md` sandboxing (host-side `gh` only) and "Human review is the final gate";
  `holon-config/world/ruleset.md` sections 2 and 3 (typed docstrings, declared test changes).
- **Potential violations or edge cases:** tests reaching real `gh`; a false-positive refusal blocking all flows; the
  override row miscounted as an execution; re-plan loops.
- **Mitigations built into the plan:** injected runners and hermetic guards; conservative overlap plus a recorded
  override; readers filter by `kind`; re-plan bounded by `MAX_REPLAN_ATTEMPTS`; fail closed with a clear message when
  discovery is unavailable.
- **Residual risk accepted (and why):** conservative glob overlap may refuse some non-overlapping plans; accepted
  because the override is one flag and recorded, and false negatives are the costlier error.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 11.0
- **Budget Compliance:** The strategy fits within budget, so it runs as one plan with no sub-intent list.
