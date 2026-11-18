# Execution Record: E-1794977034-a79b4aa6-claude-agent-claude-fable-5

- **Plan ID:** P-1794976517-cac841ef-claude-agent-claude-fable-5
- **Intent ID:** I-1794976501-macgate
- **Agent:** claude-agent/claude-fable-5

## Summary

In progress. Implements the two coupled fixes of the plan: (1) `.github/workflows/test-unit.yml` moves from the
fixed-budget composite action to the baseline-comparison gate `scripts/ci_timing_gate.py` (per-OS `--min-delta`,
tolerance 50 %, label-proof `--ceiling 600`); (2) `check_final_head_ci` in `flow/github/ci_final_head.py` gains a
one-shot automatic rerun for timing-gate-only failures on bookkeeping-only heads, surfaced in the Stage 5 verdict and
the invalidation comment.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: Verify repo-level assumptions (INFO_GATHERING)

All load-bearing anchors re-verified on the execution branch:

1. The only non-test caller of `check_final_head_ci` is `flow/stages/calibrate.py` (line 319, no seams argument, so
   defaults must keep working). No second off-limits caller exists; the seam-only design holds.
2. `ci_failure_detail.py` has `TIMING_GATE_STEP_PREFIX = "CI Timing Gate"` and `parse_run_id` over
   `/actions/runs/(\d+)` links; `failure_detail` tags `timing_gate` when every failed step name starts with the
   prefix.
3. The three scoped flow modules sit exactly at their budgets: `github/ci_final_head.py` 190/190,
   `github/ci_reverify.py` 97/97, `github/approval_invalidation.py` 137/137 (`wc -l` matches
   `check_flow_module_budgets.py`, which counts `splitlines()`).
4. `test-stress.yml` is the canonical `scripts/ci_timing_gate.py` wiring (start-epoch step, `TIMING_MODE` from the
   event name, artefact upload on `main` pushes, `--runs 5 --tolerance 0.5 --min-delta 120 --ceiling 600`).
5. **Discrepancy against the plan's expectation:** one test does assert the current contents of `test-unit.yml` —
   `test_every_workflow_wires_start_and_check_steps` in `apps/sandbox-executor/tests/test_ci_timing_gate.py` lists
   `test-unit.yml` among the five workflows that must wire the `./.github/actions/ci-timing-gate` composite action.
   That file is in the declared touch set; Step 5 updates it by removing `test-unit.yml` from the composite-action
   list (the other four workflows stay covered) and pinning the new `ci_timing_gate.py` wiring with a stricter
   dedicated contract test. This replaces an assertion made obsolete by the intended change; no behavioural
   assertion is weakened.

Additional execution-time constraint found: `tests/flow/test_package_exports.py` ratchets that every new public
(top-level, non-underscore) definition under `flow/` must be re-exported from `flow/__init__.py`, which is
off-limits. Consequence: every new helper added in Step 3/4 is module-private (`_`-prefixed); only the existing
`FinalHeadSeams` dataclass (already in `KNOWN_UNEXPORTED`) gains fields.

### Step 2: Rewire test-unit.yml onto the baseline-comparison timing gate (CONFIG)

`.github/workflows/test-unit.yml` now mirrors the proven `test-stress.yml` wiring of `scripts/ci_timing_gate.py`:

- A "Record job start time" step before checkout writes `CI_TIMING_START` so the whole job is measured, consistent
  with how `main` baseline records are produced.
- The two `./.github/actions/ci-timing-gate` composite steps are replaced by one step named
  `CI Timing Gate (compare with main)` (the `CI Timing Gate` prefix keeps `ci_failure_detail.failure_detail`
  tagging its failures `timing_gate`), running `compare` on `pull_request` events and `record` otherwise, with
  `--runs 5 --tolerance 0.5 --ceiling 600 --junit unit-report.xml` and the override label forwarded.
- Per-OS minimum delta via `matrix.include`: `timing-min-delta` 120 for `ubuntu-latest` (script default), 150 for
  `macos-latest` — strictly above the observed `370.2 - 235 = 135.2` s same-code swing, so the recorded noise
  cases (312.7 s, 332.0 s, 370.2 s against any plausible baseline median of 235–306 s) pass, while a real
  slowdown (for example 480 s against a 280 s median: `480 > 280 * 1.5 = 420` and `480 - 280 = 200 > 150`) still
  fails, and anything over the label-proof `--ceiling 600` fails unconditionally (fail-closed).
- An "Upload timing record" step gated on pushes to `main` publishes `ci-timing-test-unit-unit-<os>`
  (`retention-days: 30`, `if-no-files-found: error`), keying baselines per workflow/job/runner OS so macOS is only
  ever compared with macOS.
- Triggers (including `labeled`/`unlabeled`), permissions (`actions: read` for the artefact fetch), concurrency
  and the test step itself are unchanged. `.github/actions/ci-timing-gate/action.yml` and
  `scripts/check_ci_runtime_budget.py` are untouched; four other workflows still use them.

### Step 3: One automatic rerun for bookkeeping-only timing failures (IMPLEMENTATION)

`flow/github/ci_final_head.py` (190 → 334 lines) implements the rerun entirely behind the existing
`check_final_head_ci` seam, so the off-limits caller `flow/stages/calibrate.py` needs no edit:

- `FinalHeadSeams` gains three additive, default-`None` fields: `rerun_dispatcher` (default `_gh_rerun`, runs
  `gh run rerun <run-id> --failed` bounded by the step-lookup timeout, False on any error), `changed_paths`
  (default `_git_changed_paths`, runs `git diff --name-only <approved>..HEAD --`, None on any error) and
  `approved_head` (default `approved_head_of(context)` imported from `ci_reverify` — no import cycle, verified).
- New private helpers (every new top-level name is `_`-prefixed, keeping `test_package_exports.py` green with the
  off-limits `flow/__init__.py` untouched): `_FinalHeadCheck` bundles the invocation's fixed arguments so helpers
  stay within `PLR0913 <= 5`; `_bookkeeping_only` (non-empty diff, every path under `plans/`, `executions/` or
  `holon-knowledge/` — module constant `BOOKKEEPING_PATH_PREFIXES`); `_timing_gate_only` (non-empty
  `failed_details`, every detail tagged `timing_gate`); `_failed_run_ids` (`parse_run_id` over the failed checks'
  links, deduplicated, None when any link has no run id); `_eligible_run_ids` (fail-closed on every undecidable
  input); `_wait_for_rerun_start` (polls with the policy clock/sleep until the failed checks leave the failed
  state or the registration timeout elapses); `_retry_timing_noise` (dispatch once, wait, re-classify via the
  settled-status wait, annotate `timing_rerun = {attempted, run_ids, outcome}`; a failed dispatch returns the
  original record with `timing_rerun = {attempted: false, reason}`).
- `check_final_head_ci` calls `_retry_timing_noise` exactly once after the first classification and only on a
  `failed` state; there is no loop, so at most one rerun per invocation by construction. The never-raises outer
  try/except and `_record`'s shape are unchanged; ineligible paths return byte-identical records.
- Module docstring updated: it now reruns a failed job at most once under the conditions above.
- `apps/sandbox-executor/flow_module_budgets.json`: `github/ci_final_head.py` raised 190 → 334 with the
  convention-following description note (the declared scope amendment).

All 17 existing tests in `test_calibrate_final_head_ci.py` pass unmodified (the timing-gate-only case stays
ineligible there because no approved head exists — fail-closed default confirmed).

## Tests Run
