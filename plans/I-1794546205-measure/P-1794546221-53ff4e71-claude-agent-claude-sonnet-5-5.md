# Plan for I-1794546205-measure

- **Plan ID:** P-1794546221-53ff4e71-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-13T05:03:42.002Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** None. One small refinement: the pytest-summary parser and test runner go in new
  focused modules, because `entrypoint/executor.py` (1594 lines) and `flow/review/loop.py` (897 lines) are already far
  over the ~400-line guidance.
- **Exploration stance:** balanced. The data path is well understood (calibration already reads `test_pass_rate`; Stage
  4 already handles `exit_code` and null rates), so the work is mostly an exploit of known seams. One exploratory step
  covers the review-loop fallback, where the CI-registration wait interacts with a local run.
- **Safety priority level:** elevated
- **Priority Justification:** The change runs project test code in the executor and review containers and records its
  output into an append-only ledger. `docs/safety.md` and the ledger rules require redaction of captured output, exactly
  three append-only ledgers, and no host-side network or credential leakage. A wrong parse could turn a failure into a
  pass, the most damaging error for the review gate.

## Exploration

- **Proportion of steps that are exploratory:** 0.15
- **Justification:** Only the review-loop fallback ordering (wait for CI, then measure locally) has real unknowns. The
  rest follows existing patterns.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.74  |
| entropy_pred        | 8.4   |
| impact_pred         | 82.0  |
| cost_pred           | 28.0  |
| learning_value_pred | 6.0   |
| ev_pred             | 38.0  |

### Strategy Rationale

The intent is accepted as written. Its EV is high: every flow so far has relied on CI alone, and the missing measurement
is a single point of failure for the gate. The strategy is a seam-by-seam change: a pure parser plus runner module
(testable in isolation), a thin wiring step in the executor, a host-side treatment step, a review-loop fallback module,
then tests and docs.

Overall metrics are derived qualitatively from the steps. The bottleneck is Step 3 (executor wiring into a 1594-line
`main()` that already carries `noqa` complexity suppressions, so any addition must go through a helper), followed by
Step 5 (the review-loop fallback). `p_success_pred` is about the product of the bottleneck steps,
`0.92 * 0.85 * 0.90 * 0.88 * 0.93 ≈ 0.58` if fully independent, lifted to 0.74 because steps 1, 2 and 6 are low risk and
the failures are not independent (the shared parser is verified first). `entropy_pred` is the sum of step entropies,
`1.0 + 2.2 + 1.2 + 2.0 + 0.6 + 1.0 + 0.4 = 8.4`, below the 15.0 budget, so no sub-intents are needed. `ev_pred` uses
`EV = 0.74 * 82.0 + 0.5 * 6.0 - 0.3 * 8.4 - 28.0 * 0.5 ≈ 60.7 + 3.0 - 2.5 - 14.0 + 0.8 ≈ 38.0`, with cost scaled as in
`docs/metrics.md`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md` and `constraints.md`: exactly three append-only ledgers; merging stays with the
    human maintainer; target-native paths.
  - `docs/safety.md`: redact captured output; no secrets in the ledger; containers stay hermetic.
  - `docs/testing/testing_guide.md`: tests run strictly via `uv run pytest`, hermetic and without network.
- **Potential violations or edge cases:**
  - A pytest summary parse that misreads output (xdist, `-q`, warnings-only, "no tests ran") and reports 1.0.
  - A test run that hangs and eats the executor's budget.
  - Output containing secrets written into a summary row.
  - The executor's own test run mutating the worktree (caches, `.holon/` files) and polluting the commit.
  - Existing partial-salvage and integration paths that strip `test_pass_rate` from superseding rows.
- **Mitigations built into the plan:**
  - The parser returns null plus an explicit `test_run_error` for any output it cannot fully reconcile, and a rate of
    1.0 is only recorded when the exit code is 0 and passed > 0.
  - A bounded timeout; a timeout records null plus an error.
  - All captured output passes through the existing redaction before storage and is capped.
  - The test run happens before `git add`, with caches covered by the existing ignore rules, and a clean-tree check
    afterwards.
  - The superseding-row logic is left alone but covered by a test.
- **Residual risk accepted (and why):** Test runs add wall-clock time to Stage 3; accepted because the resolver already
  does this and the timeout bounds it. Projects with no `uv run task test` record null plus an error, which is the same
  as today.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 8.4
- **Budget Compliance:** The strategy fits within budget.

## Plan Description & Strategy

Seven steps on one execution branch `I-1794546205-measure/exec`: (1) a pure measurement module, (2) its unit tests, (3)
executor wiring, (4) host-side Stage 3/4 handling and source attribution, (5) the review-loop fallback in a new module,
(6) calibration confirmation plus test, (7) docs and lint. Test discipline: run affected tests with
`uv run pytest <paths>` after each step, then the full suite once with `uv run task test`; no repeated runs because the
intent has no timing requirement.

---

## Step 1: Add a pure test-measurement module (pytest summary parser and runner)

- **Sub-intent recommendation:** NO
- **Reasoning:** Small, self-contained and reusable by both the executor and the review loop; no sub-intent branch
  needed.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Provide one tested place that runs `uv run task test`, parses the pytest summary and returns a
  structured result.
- **Git branch:** I-1794546205-measure/exec
- **Sub-intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add `entrypoint/test_measurement.py` (no engine imports, so both the executor entrypoint and the review loop can use
  it) with a frozen dataclass `TestMeasurement`: rate, passed, failed, errored, exit_code, run_error, output_excerpt.
- Parse the final pytest summary line (`N failed, M passed, K errors in Ts`, with optional `skipped`, `xfailed`,
  `warnings` and xdist forms); count only passed, failed and errored. Rate is `passed / (passed + failed + errored)`.
- Return rate null and a `test_run_error` when: the tool is missing (reuse `missing_tool_result` and the 127 convention
  from `review_verification.py`), the timeout expires, the summary cannot be found, or the total is zero ("no tests
  ran").
- A non-zero exit code with a parsed rate of 1.0 (for example collection error not in the summary) is recorded as
  measured with the real exit code; consumers already treat non-zero exit as failed. Never emit a rate above what the
  counts support.
- Run the command with a bounded `timeout` (default from a constant, overridable by an environment variable, documented)
  via `subprocess.run` with a list argv; capture stdout and stderr; redact with `sandbox_executor.redaction` and cap
  length before returning any excerpt.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO (but every later step depends on it)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` redaction; ruff complexity limits
- **Potential failure modes for this step:** Parser misreads variant summary formats; redaction missed on stderr;
  timeout leaves child processes running
- **Guardrails and early-abort checks:** Fail closed to null plus error; kill the process group on timeout; unit tests
  with real pytest output samples

### Success & Discard Criteria

- **Success:** Module passes lint, and parser handles the pass, fail, error and no-tests cases
- **Discard:** Needs more than ~120 lines or any new dependency

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.0   |
| impact_pred         | 70.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 64.0  |

### Step Metrics Rationale

Pure functions with known inputs; low entropy. The main learning is how pytest formats summaries under `-n auto`, which
the tests pin down.

---

## Step 2: Unit-test the measurement module hermetically

- **Sub-intent recommendation:** NO
- **Reasoning:** Cheap, no external effects; tests are the safety net for the highest-consequence parse.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove a pass records 1.0, a fail records the real fraction and non-zero exit, and an unrunnable test
  task records null plus `test_run_error`.
- **Git branch:** I-1794546205-measure/exec
- **Sub-intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add `tests/test_test_measurement.py` using a fake runner (monkeypatched `subprocess.run`) with canned pytest output:
  all pass; `2 failed, 8 passed` gives 0.8 and exit code 1; errors counted; skipped ignored; `no tests ran` gives null.
- Cases for missing `uv` (FileNotFoundError), timeout (`TimeoutExpired`), unparseable output and secrets in output
  (assert redaction with the `leak_assertions` helper).
- Assert null never coincides with exit code 0 and an absent error, and a measured rate never becomes null.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/testing/testing_guide.md`, `docs/safety.md`
- **Potential failure modes for this step:** Tests accidentally invoke the real `uv`; flaky timeout test
- **Guardrails and early-abort checks:** Use the network and subprocess guards in `conftest.py`; fake the clock rather
  than sleeping

### Success & Discard Criteria

- **Success:** `uv run pytest apps/sandbox-executor/tests/test_test_measurement.py` passes
- **Discard:** Tests need real subprocesses

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.4   |
| impact_pred         | 55.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 52.0  |

### Step Metrics Rationale

Straightforward.

---

## Step 3: Wire measurement into the executor entrypoint before the execution commit

- **Sub-intent recommendation:** NO
- **Reasoning:** Moderate risk concentrated in an oversized `main()`; keep as a step on the same branch but add only a
  helper call.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Run the tests after the agent turn and before the commit, and record the result in the
  `executions.jsonl` summary row.
- **Git branch:** I-1794546205-measure/exec
- **Sub-intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add a helper `measure_executor_tests(workspace)` in `entrypoint/execution_workspace.py` or a new small
  `entrypoint/executor_test_run.py`; do not add branching to `main()` beyond one call and one dict merge (it already
  carries `noqa` suppressions; add none).
- Call it after the agent turn succeeds (and on salvage paths where a commit is made) and before staging the commit;
  skip when there is no diff or for the non-execute outcomes (decomposition and failed-before-agent rows) so those rows
  keep a null rate with no error.
- Merge `test_pass_rate`, `tests_passed`, `tests_failed`, `tests_errored`, `test_exit_code` and `test_run_error` into
  the summary row at the point it is built (around the `entry_type: summary` dict). Reuse the existing `exit_code` key
  for the agent outcome; the test exit code is a separate field, so the two are not confused.
- Verify afterwards that the test run left no tracked changes beyond ignored caches; if it did, record that in
  `test_run_error`-adjacent note and do not commit the stray files.
- Print a one-line redacted summary to the container log.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md`; ledger append-only rule; `scripts/check_flow_module_budgets.py` and
  `flow_module_budgets.json` (do not grow the budgeted modules)
- **Potential failure modes for this step:** Test run exceeds container time budget; stray files committed; the
  `exit_code` field semantic collision; summary row schema drift breaks `ledger_reconcile`
- **Guardrails and early-abort checks:** Bounded timeout; add ledger-schema tests; keep new keys optional so old readers
  ignore them

### Success & Discard Criteria

- **Success:** Executor tests show the summary row carries the new fields in the passing, failing and cannot-run cases
- **Discard:** Needs a change to the ledger row schema validator beyond optional keys

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 2.2   |
| impact_pred         | 85.0  |
| cost_pred           | 7.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 70.0  |

### Step Metrics Rationale

Largest integration risk, because `main()` is long and carries existing suppressions. Keeping the diff to one helper
call lowers entropy.

---

## Step 4: Treat measured results correctly in Stage 3 and Stage 4 and attribute the source

- **Sub-intent recommendation:** NO
- **Reasoning:** Mostly existing logic; adds source attribution and tests-count passthrough.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Ensure a null rate stays "not measured", and a rate below 1.0 or non-zero exit is a failed result;
  carry the new fields and a `test_source` of `stage_3`, `ci` or `review_local`.
- **Git branch:** I-1794546205-measure/exec
- **Sub-intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `flow/dispatch.py` extend `recorded_test_pass_rate` neighbours to carry counts, `test_exit_code` and
  `test_run_error`; keep rejecting booleans and out-of-range values as unmeasured (null).
- In `flow/stages/execute.py` (416 lines) add the new fields to the execute result payload through a small helper in
  `dispatch.py`, so `execute.py` grows by only a few lines; keep the superseding-row logic stripping `test_pass_rate`,
  and add the fields to that strip list so a superseded row never keeps stale counts.
- In `flow/stages/review.py` (300 lines) use the new `test_exit_code` in `_test_evidence` and add `test_source` and
  counts to the review row evidence (`pass_rate`, `measured`, `passed`); a `test_run_error` stays unmeasured with the
  reason shown.
- Surface the source and counts in the posted summary text built in `flow/review/posting.py`.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** Ledger append-only; `docs/review_consensus.md`
- **Potential failure modes for this step:** Null misread as pass; a measured 1.0 with non-zero exit treated as pass
- **Guardrails and early-abort checks:** Table-driven test over (rate, exit code, error) combinations

### Success & Discard Criteria

- **Success:** Evidence matrix behaves as specified
- **Discard:** Any combination treats null as pass

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.9   |
| entropy_pred        | 1.2   |
| impact_pred         | 75.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 70.0  |

### Step Metrics Rationale

Existing code already handles the core; the risk is the interaction of source attribution with `ci_evidence.py`.

---

## Step 5: Let the review loop measure locally when no CI registers

- **Sub-intent recommendation:** YES
- **Reasoning:** The fallback touches the 897-line `loop.py` and CI registration timing; new module
  `flow/review/local_tests.py` keeps `loop.py` small. Exploratory because of timing interactions.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The review loop can run the local task after the CI-registration wait without breaking
  the existing dry-run and no-`gh` paths.
- **Learning target:** Where in `loop.py` the fallback fits without raising complexity above the ruff limits.
- **Maximum acceptable cost for this learning:** 1.5x the step cost.

### Intent & Git Integration

- **Step Intent:** When no CI checks register and Stage 3 recorded no rate, run the test task in the review container
  and record the result instead of reporting not-measured.
- **Git branch:** I-1794546205-measure/exec
- **Sub-intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add `flow/review/local_tests.py` with `measure_review_local(workspace, runner)` that calls
  `entrypoint/test_measurement.py` through the review container path. Check that host-flow code does not import from
  entrypoints in a forbidden direction; if it would, move the shared pure module to a neutral package (for example
  `sandbox_executor/test_measurement.py`) and import it from both sides. That choice is made in Step 1 review.
- In `loop.py` replace the immediate `apply_local_test_evidence(..., unmeasured_blocks=True)` not-measured result with:
  if the probe source is `local_tests`, rate is null and no failure is recorded, call the fallback once per loop run,
  cache the result, and re-run `apply_local_test_evidence` with the measured rate and `test_source = review_local`.
- Keep dry-run behaviour unchanged (no run, existing dry-run path) and the existing `gh` wait path unchanged; the
  fallback runs only after the registration timeout elapses.
- Record the `review_local` rate and counts in the review rows through `flow/review/records.py` and the posted summary.
- Run in the review container; the review container already holds the workspace, so no mount change.

### Dependencies & Criticality

- **Depends on:** Steps 1, 4
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** Resolver verification rules; `docs/review_consensus.md`; hermetic testing guide
- **Potential failure modes for this step:** Fallback runs on every iteration (cost); caches a stale result after the
  resolver pushes a fix; complexity limit breach in `loop.py`
- **Guardrails and early-abort checks:** Re-measure only when HEAD changed; cache keyed on commit SHA; keep new logic in
  the new module

### Success & Discard Criteria

- **Success:** No-CI scenario in tests produces a measurement and the source label
- **Discard:** Fallback requires a mount or credential change

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.8   |
| entropy_pred        | 2.0   |
| impact_pred         | 80.0  |
| cost_pred           | 7.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 62.0  |

### Step Metrics Rationale

Decisive step for acceptance criterion 3. The learning value is where the fallback fits with the resolver loop; the
SHA-keyed cache is the main design decision.

---

## Step 6: Confirm calibration reads the measured value and add a test

- **Sub-intent recommendation:** NO
- **Reasoning:** Calibration already parses `test_pass_rate` in `calibration.py:788`; this step only adds coverage.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Show that a measured rate flows into the calibration actuals and null stays null.
- **Git branch:** I-1794546205-measure/exec
- **Sub-intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add a case in `tests/test_calibration.py` (or `test_calibration_dual_ev.py`) with a summary row carrying
  `test_pass_rate` 0.8 and the new count fields, asserting `actual.test_pass_rate == 0.8`, and a null case.
- Check that `calibration_ev.py` uses the rate in its test-pass term; if the term ignores a measured rate below 1.0, fix
  in this module and note it.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/metrics.md`
- **Potential failure modes for this step:** Calibration ignores new fields
- **Guardrails and early-abort checks:** Assertions on both row shapes

### Success & Discard Criteria

- **Success:** Calibration tests pass
- **Discard:** Requires a calibration formula change outside scope

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 0.6   |
| impact_pred         | 50.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 47.0  |

### Step Metrics Rationale

Existing reader; small confirmation.

---

## Step 7: Add integration tests, update docs, run lint and the full suite

- **Sub-intent recommendation:** NO
- **Reasoning:** Docs and verification; cheap and required by the acceptance criteria.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Cover the review-loop fallback and a "measured is never unmeasured" test, then update docs and run
  all gates.
- **Git branch:** I-1794546205-measure/exec
- **Sub-intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add tests in `tests/test_review_loop.py`-adjacent new file `tests/test_review_local_tests.py` for the no-CI fallback
  (using `review_fakes.py`) and a measured-never-unmeasured matrix.
- Update `docs/ledger_schema.md` (new summary-row fields, null semantics, `test_source`),
  `docs/sandbox/execute_plan.md`, `docs/review_consensus.md` and the flow docs (Stage 3 and Stage 4 sections), in
  Australian English.
- Run `uv run pytest` on the affected test files, then `uv run task test` once, then `uv run task lint` and
  `uv run task lint-docs`; fix findings; check `scripts/check_flow_module_budgets.py`.

### Dependencies & Criticality

- **Depends on:** Steps 1 to 6
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/testing/testing_guide.md`; lint-docs
- **Potential failure modes for this step:** Docs lint on table formatting; module budget breach
- **Guardrails and early-abort checks:** Run prettier and lint before finishing

### Success & Discard Criteria

- **Success:** All three gates pass
- **Discard:** Gates cannot pass without out-of-scope changes

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.9   |
| entropy_pred        | 0.4   |
| impact_pred         | 60.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 52.0  |

### Step Metrics Rationale

Mechanical, with a moderate chance of a lint round-trip.

---

### Strategy Rationale (summary)

The plan stays inside the listed scope plus one new focused module and tests; no new dependencies, no new ledgers, and
merging remains with the human maintainer.

## Sub-intent list

None
