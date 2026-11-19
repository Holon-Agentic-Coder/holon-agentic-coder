# Execution Record: E-1794977034-a79b4aa6-claude-agent-claude-fable-5

- **Plan ID:** P-1794976517-cac841ef-claude-agent-claude-fable-5
- **Intent ID:** I-1794976501-macgate
- **Agent:** claude-agent/claude-fable-5

## Summary

Complete. Implements the two coupled fixes of the plan: (1) `.github/workflows/test-unit.yml` moves from the
fixed-budget composite action to the baseline-comparison gate `scripts/ci_timing_gate.py` (per-OS `--min-delta` 120/150,
tolerance 50 %, label-proof `--ceiling 600`, baselines keyed per workflow/job/runner OS); (2) `check_final_head_ci` in
`flow/github/ci_final_head.py` gains a one-shot automatic rerun for timing-gate-only failures on bookkeeping-only heads
behind additive `FinalHeadSeams` fields (the off-limits caller `flow/stages/calibrate.py` is untouched), surfaced in the
Stage 5 verdict and the invalidation comment via an additive `timing_rerun` note. All 4,337 unit tests pass,
`uv run task lint` passes (budgets raised 190→334, 97→105, 137→156 as the declared scope amendment), and every
acceptance-criterion scenario is pinned by a hermetic test. One pre-existing repo condition is reported under
`## Tests Run`: `uv run task lint-docs` already fails on `main` for 39 old markdown files outside this touch set; every
markdown file this execution adds or changes passes prettier.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.95  |
| entropy_actual        | 6.0   |
| impact_actual         | 85.0  |
| cost_actual           | 24.0  |
| learning_value_actual | 6.0   |

`success_actual = 0.95`: every goal and plan step landed as designed with all tests and code lint green; the 0.05
deduction is for the `lint-docs` acceptance criterion being unsatisfiable repo-wide without out-of-scope edits (a
pre-existing `main` condition, reported rather than silently widened). `entropy_actual = 6.0` versus 11.0 predicted: the
only genuine surprises were the obsolete workflow-wiring test contract (anchor 5) and the package-export ratchet forcing
private helper names; the rerun design itself landed without rework. `cost_actual = 24.0` versus 32.0:
`EV_actual = 0.95 * 85.0 + 0.5 * 6.0 - 0.3 * 6.0 - 24.0 = 80.75 + 3.0 - 1.8 - 24.0 = 57.95`.

## Steps Executed

### Step 1: Verify repo-level assumptions (INFO_GATHERING)

All load-bearing anchors re-verified on the execution branch:

1. The only non-test caller of `check_final_head_ci` is `flow/stages/calibrate.py` (line 319, no seams argument, so
   defaults must keep working). No second off-limits caller exists; the seam-only design holds.
2. `ci_failure_detail.py` has `TIMING_GATE_STEP_PREFIX = "CI Timing Gate"` and `parse_run_id` over `/actions/runs/(\d+)`
   links; `failure_detail` tags `timing_gate` when every failed step name starts with the prefix.
3. The three scoped flow modules sit exactly at their budgets: `github/ci_final_head.py` 190/190,
   `github/ci_reverify.py` 97/97, `github/approval_invalidation.py` 137/137 (`wc -l` matches
   `check_flow_module_budgets.py`, which counts `splitlines()`).
4. `test-stress.yml` is the canonical `scripts/ci_timing_gate.py` wiring (start-epoch step, `TIMING_MODE` from the event
   name, artefact upload on `main` pushes, `--runs 5 --tolerance 0.5 --min-delta 120 --ceiling 600`).
5. **Discrepancy against the plan's expectation:** one test does assert the current contents of `test-unit.yml` —
   `test_every_workflow_wires_start_and_check_steps` in `apps/sandbox-executor/tests/test_ci_timing_gate.py` lists
   `test-unit.yml` among the five workflows that must wire the `./.github/actions/ci-timing-gate` composite action. That
   file is in the declared touch set; Step 5 updates it by removing `test-unit.yml` from the composite-action list (the
   other four workflows stay covered) and pinning the new `ci_timing_gate.py` wiring with a stricter dedicated contract
   test. This replaces an assertion made obsolete by the intended change; no behavioural assertion is weakened.

Additional execution-time constraint found: `tests/flow/test_package_exports.py` ratchets that every new public
(top-level, non-underscore) definition under `flow/` must be re-exported from `flow/__init__.py`, which is off-limits.
Consequence: every new helper added in Step 3/4 is module-private (`_`-prefixed); only the existing `FinalHeadSeams`
dataclass (already in `KNOWN_UNEXPORTED`) gains fields.

### Step 2: Rewire test-unit.yml onto the baseline-comparison timing gate (CONFIG)

`.github/workflows/test-unit.yml` now mirrors the proven `test-stress.yml` wiring of `scripts/ci_timing_gate.py`:

- A "Record job start time" step before checkout writes `CI_TIMING_START` so the whole job is measured, consistent with
  how `main` baseline records are produced.
- The two `./.github/actions/ci-timing-gate` composite steps are replaced by one step named
  `CI Timing Gate (compare with main)` (the `CI Timing Gate` prefix keeps `ci_failure_detail.failure_detail` tagging its
  failures `timing_gate`), running `compare` on `pull_request` events and `record` otherwise, with
  `--runs 5 --tolerance 0.5 --ceiling 600 --junit unit-report.xml` and the override label forwarded.
- Per-OS minimum delta via `matrix.include`: `timing-min-delta` 120 for `ubuntu-latest` (script default), 150 for
  `macos-latest` — strictly above the observed `370.2 - 235 = 135.2` s same-code swing, so the recorded noise cases
  (312.7 s, 332.0 s, 370.2 s against any plausible baseline median of 235–306 s) pass, while a real slowdown (for
  example 480 s against a 280 s median: `480 > 280 * 1.5 = 420` and `480 - 280 = 200 > 150`) still fails, and anything
  over the label-proof `--ceiling 600` fails unconditionally (fail-closed).
- An "Upload timing record" step gated on pushes to `main` publishes `ci-timing-test-unit-unit-<os>`
  (`retention-days: 30`, `if-no-files-found: error`), keying baselines per workflow/job/runner OS so macOS is only ever
  compared with macOS.
- Triggers (including `labeled`/`unlabeled`), permissions (`actions: read` for the artefact fetch), concurrency and the
  test step itself are unchanged. `.github/actions/ci-timing-gate/action.yml` and `scripts/check_ci_runtime_budget.py`
  are untouched; four other workflows still use them.

### Step 3: One automatic rerun for bookkeeping-only timing failures (IMPLEMENTATION)

`flow/github/ci_final_head.py` (190 → 334 lines) implements the rerun entirely behind the existing `check_final_head_ci`
seam, so the off-limits caller `flow/stages/calibrate.py` needs no edit:

- `FinalHeadSeams` gains three additive, default-`None` fields: `rerun_dispatcher` (default `_gh_rerun`, runs
  `gh run rerun <run-id> --failed` bounded by the step-lookup timeout, False on any error), `changed_paths` (default
  `_git_changed_paths`, runs `git diff --name-only <approved>..HEAD --`, None on any error) and `approved_head` (default
  `approved_head_of(context)` imported from `ci_reverify` — no import cycle, verified).
- New private helpers (every new top-level name is `_`-prefixed, keeping `test_package_exports.py` green with the
  off-limits `flow/__init__.py` untouched): `_FinalHeadCheck` bundles the invocation's fixed arguments so helpers stay
  within `PLR0913 <= 5`; `_bookkeeping_only` (non-empty diff, every path under `plans/`, `executions/` or
  `holon-knowledge/` — module constant `BOOKKEEPING_PATH_PREFIXES`); `_timing_gate_only` (non-empty `failed_details`,
  every detail tagged `timing_gate`); `_failed_run_ids` (`parse_run_id` over the failed checks' links, deduplicated,
  None when any link has no run id); `_eligible_run_ids` (fail-closed on every undecidable input);
  `_wait_for_rerun_start` (polls with the policy clock/sleep until the failed checks leave the failed state or the
  registration timeout elapses); `_retry_timing_noise` (dispatch once, wait, re-classify via the settled-status wait,
  annotate `timing_rerun = {attempted, run_ids, outcome}`; a failed dispatch returns the original record with
  `timing_rerun = {attempted: false, reason}`).
- `check_final_head_ci` calls `_retry_timing_noise` exactly once after the first classification and only on a `failed`
  state; there is no loop, so at most one rerun per invocation by construction. The never-raises outer try/except and
  `_record`'s shape are unchanged; ineligible paths return byte-identical records.
- Module docstring updated: it now reruns a failed job at most once under the conditions above.
- `apps/sandbox-executor/flow_module_budgets.json`: `github/ci_final_head.py` raised 190 → 334 with the
  convention-following description note (the declared scope amendment).

All 17 existing tests in `test_calibrate_final_head_ci.py` pass unmodified (the timing-gate-only case stays ineligible
there because no approved head exists — fail-closed default confirmed).

### Step 4: Surface the rerun in the Stage 5 verdict and the invalidation comment (IMPLEMENTATION)

- `flow/github/ci_reverify.py` (97 → 105 lines): `reverify_final_head` copies the record's `timing_rerun` note into the
  reverified mapping, and the private `_timing_rerun_clause` adds " after one automatic rerun of the timing gate" to the
  GREEN/RED report line exactly when a rerun was attempted. Verdict mapping is unchanged: `success` → GREEN, `failed` →
  RED; a record without the key renders byte-identical output.
- `flow/github/approval_invalidation.py` (137 → 156 lines): `render_invalidation` takes an optional fourth
  `timing_rerun` argument (passed by `invalidate_approval` from the reverified mapping) and, when a rerun was attempted
  and the head still failed, adds the line "The failed job was automatically rerun once and failed again." Marker,
  once-per-head and redaction behaviour unchanged.
- Budgets raised accordingly in `flow_module_budgets.json` (`github/ci_reverify.py` 105,
  `github/approval_invalidation.py` 156), covered by the Step 3 description note.

All 31 existing tests across `test_ci_reverify.py`, `test_approval_invalidation.py` and
`test_calibrate_final_head_ci.py` pass unmodified.

### Step 5: Hermetic tests for the new measure and the rerun semantics (TEST)

Declared test-file changes (world ruleset rule 3; all additive except the one obsolete-contract update declared in Step
1):

- `test_ci_timing_gate.py` (modified): new macOS-threshold cases with `Thresholds(min_delta=150)` — every recorded noise
  duration (312.7, 332.0, 370.2 s) passes against every plausible baseline median (235, 280, 306 s); a real slowdown
  (480 s against a 280 s median) fails; 601 s fails on the ceiling under every baseline and even with the override
  label; cold start passes 370.2 s but not 601 s. A new workflow-contract test
  `test_unit_workflow_compares_with_main_per_runner_os` pins the `CI Timing Gate` step-name prefix, the per-OS
  `timing-min-delta` values (120/150), `--ceiling 600`, the override label, compare-on-pull-request mode, the
  start-before-checkout ordering and the `ci-timing-test-unit-unit-<os>` artefact gated on `main` pushes.
  `test_every_workflow_wires_start_and_check_steps` now lists the 4 workflows that still use the composite action
  (`test-unit.yml` removed — the assertion the intent made obsolete; no behavioural assertion weakened).
- `test_final_head_timing_rerun.py` (new, 8 tests): through `FinalHeadSeams` fakes only — passing rerun yields a
  `success` record with `timing_rerun.outcome = success`, one dispatch, GREEN from `reverify_and_report` with the rerun
  clause and no invalidation call; a second failure yields `failed`, RED and the invalidation path; a code-changing
  diff, a non-timing failure, a mixed timing+test failure, an unknown approved head, an unreadable or empty diff and a
  missing run id each dispatch nothing and leave the record without a `timing_rerun` key; a failed dispatch keeps the
  original record (`attempted: false`, no re-probe); checks that never re-register get exactly one dispatch and a
  `failed` outcome after the registration window.
- `test_ci_reverify.py` (additive): the report clause appears exactly when `timing_rerun.attempted` is true,
  byte-identical report otherwise; the note is copied into the reverified mapping.
- `test_approval_invalidation.py` (additive): the comment line "The failed job was automatically rerun once and failed
  again." appears exactly when a rerun was attempted, byte-identical body otherwise, and flows through
  `invalidate_approval` end-to-end.
- `test_calibrate_final_head_ci.py`: unchanged — the additive seam defaults kept all 17 tests passing.

All fakes are deterministic (injected clocks and sleeps, no network, no subprocesses).

### Step 6: Full-suite verification, lint and docs lint (TEST)

Affected tests first, full suite once, both lint tasks, diff review. No repeated timing runs: the intent's timing
behaviour is exercised through fake clocks, so the test-run discipline's repetition clause does not apply. Results are
in `## Tests Run`. Diff review: `git diff --name-only main...HEAD` lists exactly the Touch Set (with
`test_calibrate_final_head_ci.py` unchanged and `scripts/check_ci_runtime_budget.py` deliberately untouched), the
declared `flow_module_budgets.json` amendment, this execution record, and the plan/ledger files the flow itself wrote.

## Tests Run

All runs with `uv run` from the repository root; full-suite runs use `-n auto --dist worksteal` via `uv run task test`.

| #   | command                                                                                    | result                           | pytest time |
| --- | ------------------------------------------------------------------------------------------ | -------------------------------- | ----------- |
| 1   | `uv run pytest apps/sandbox-executor/tests/test_calibrate_final_head_ci.py` (after Step 3) | 17 passed, 0 failed              | `0.10 s`    |
| 2   | `uv run pytest` on reverify, invalidation and final-head test files (after Step 4)         | 31 passed, 0 failed              | `0.12 s`    |
| 3   | `uv run pytest apps/sandbox-executor/tests/test_final_head_timing_rerun.py` (after Step 5) | 8 passed, 0 failed               | `0.07 s`    |
| 4   | `uv run pytest` on the five affected test files (Step 5 close)                             | 124 passed, 0 failed             | `0.30 s`    |
| 5   | `uv run pytest` on the five affected test files (Step 6)                                   | 124 passed, 0 failed             | `0.25 s`    |
| 6   | `uv run task test` (full suite, once)                                                      | 4337 passed, 1 skipped, 0 failed | `18.73 s`   |
| 7   | `uv run task lint` (uv lock check, ruff check, ruff format, flow module budgets)           | passed (62 modules in budget)    | —           |
| 8   | `uv run task lint-docs`                                                                    | fails on 39 pre-existing files   | —           |

Median of the two identical affected-test runs (rows 4 and 5): 124 passed at `0.275 s`. The full suite ran once as
planned.

`uv run task lint-docs` (`npx prettier --check "**/*.md"`) reports 39 files with style issues. Every one of them
pre-exists on `main` (verified: each failing path exists on `main` and none intersects
`git diff --name-only main...HEAD`), mostly old `plans/*_calibration.md` files. Both markdown files this execution
touches — the plan file and this record — pass `prettier --check`. Making the task pass repo-wide requires editing 39
files outside the intent's scope, so per the plan's Step 6 guardrail ("any failure that demands an edit outside the
Touch Set stops the step and is reported, never silently widened") it is reported here as a pre-existing blocked
criterion rather than fixed.
