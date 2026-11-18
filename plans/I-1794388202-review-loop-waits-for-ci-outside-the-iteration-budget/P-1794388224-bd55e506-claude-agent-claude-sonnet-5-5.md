# Plan for I-1794388202-review-loop-waits-for-ci-outside-the-iteration-budget

- **Plan ID:** P-1794388224-bd55e506-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-11T09:10:25.405Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** None.
- **Exploration stance:** conservative. The change is a well-bounded bug fix in two files plus a new focused module;
  injected clock and sleep already exist, so the design is low-risk and fully testable with fixtures.
- **Safety priority level:** standard
- **Priority Justification:** No world or safety rule is stressed. The work stays in the named scope, touches no ledger,
  no network (tests use fakes, never a real `gh`), and merging stays with the human maintainer. The only tight
  constraints are the per-module line budgets (`flow_module_budgets.json`) and the concurrent change to `tests/flow/`.

## Exploration

- **Proportion of steps that are exploratory:** 0.1
- **Justification:** Code paths were read directly: `_wait_for_pending_ci` in `flow/github/ci.py`, the three CI branches
  in `flow/review/loop.py` (about lines 336 to 410), the finding at about line 521, and the catch-all at
  `flow/stages/review.py:239`. Only the line-budget arithmetic is uncertain, so the first step measures it.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 8.0   |
| impact_pred         | 60.0  |
| cost_pred           | 20.0  |
| learning_value_pred | 4.0   |
| ev_pred             | 29.7  |

### Strategy Rationale

The intent is accepted as written. Waiting for CI and judging code have different clocks, and the current code gives CI
whatever is left of the iteration timer (`iteration_timer.remaining()` in `loop.py`). The fix is: a CI wait with its own
wall-clock budget and back-off, a three-way CI outcome carried in the loop result and finding, and a stage error that
names which outcome occurred. To stay inside the line budgets (`review/loop.py` is already at its 903 cap,
`github/ci.py` at 228) the wait logic moves to a new module `flow/github/ci_wait.py` and the outcome classification to a
new module `flow/review/ci_outcome.py`, so `loop.py` and `ci.py` shrink or stay flat.

Metric derivation: the plan is sequential and the bottleneck is Step 3, the `loop.py` integration, which has to change a
900-line function without growing it and has the most interacting behaviour. Overall `p_success_pred` is the product of
the step probabilities, `0.97 * 0.90 * 0.92 * 0.95 * 0.95 * 0.97 = 0.68`, raised to 0.80 because the steps are strongly
correlated (one design, one author) and each is independently re-checkable. Overall `entropy_pred` is the sum of the
step entropies (`1.0 + 1.5 + 2.5 + 1.5 + 1.0 + 0.5 = 8.0`), within the 15.0 budget. The overall `cost_pred` is the sum
of step costs (20.0 on the metrics scale). `ev_pred` uses
`EV = P * Impact + mu * LearningValue - lambda * Delta_S - Cost` with assumed `mu = 0.5`, `lambda = 0.3` and
`Delta_S = 1.0`: `EV = 0.80 * 60.0 + 0.5 * 4.0 - 0.3 * 1.0 - 20.0 = 48.0 + 2.0 - 0.3 - 20.0 = 29.7`. The executor
recomputes it from `holon-config/metrics/ev_config.json`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` section 1: work only on this intent's branch; merge to `main` is human only.
  - Exactly three append-only ledgers (`holon-knowledge/ledger/`): no new ledger or file store.
  - `apps/sandbox-executor/flow_module_budgets.json`: a module may shrink but never grow past its budget; new modules
    need an entry at creation size.
  - `docs/safety.md` and the sandbox rules: tests never reach the network, so no real `gh` call.
- **Potential violations or edge cases:**
  - `loop.py` cannot grow at all; a careless edit trips `uv run task lint`.
  - The concurrent change deletes `tests/flow/fixtures/flow_py_definitions.json` and edits `docs/architecture.md`;
    touching either causes a merge conflict.
  - Moving `_wait_for_pending_ci` changes a hash tracked by `tests/flow/test_move_proof.py`.
  - An invalid `HOLON_REVIEW_CI_WAIT_SECONDS` must log a warning that names the variable, never the value (#96 in
    `budgets.py`).
  - Existing `tests/flow/review/test_loop.py` and `tests/flow/github/test_ci.py` may assert on the old behaviour.
- **Mitigations built into the plan:**
  - New tests go in new files under `apps/sandbox-executor/tests/` (not `tests/flow/fixtures/`), for example
    `tests/test_review_ci_wait.py`; existing `tests/flow/` files are edited only where an old assertion contradicts the
    new behaviour, and minimally.
  - Reuse `budgets._read_positive` for the environment variable so the warning format and bounds match the other
    budgets.
  - Step 1 measures line counts before any edit; budgets are only raised with a recorded reason.
  - Do not edit `docs/architecture.md` or anything under `tests/flow/fixtures/`. If `test_move_proof` fails only because
    `_wait_for_pending_ci` moved, refresh just that one entry and state so in the execution record; otherwise keep the
    function name and location stable by leaving a thin function in `ci.py` (see Step 2).
- **Residual risk accepted (and why):** A merge conflict with the concurrent test rewrite is possible in
  `tests/flow/review/test_loop.py` if an old assertion has to change; accepted, because the edit would be small and the
  alternative is leaving a test that asserts the wrong behaviour.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 8.0
- **Budget Compliance:** The strategy fits within budget. No `## Sub-intent list` section is needed.

## Plan Description & Strategy

Six steps on one execution branch: (1) measure and lay out the modules; (2) add the CI wait budget and back-off wait
module with the environment resolver in `budgets.py`; (3) wire the loop so the wait runs on its own clock and is
recorded once; (4) classify three outcomes and replace the catch-all error; (5) tests; (6) docs and final gates. Test
runs follow the discipline: affected files with `uv run pytest <paths>` first, then the full suite once with
`uv run task test`; no repeated runs because the intent states no timing measurement.

---

## Step 1: Measure line budgets and fix the module layout

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only measurement and a short decision; low risk, no reuse value as a separate branch.
- **Step Type:** INFO_GATHERING
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Confirm which files may grow and fix where each new piece of behaviour lives.
- **Git branch:**
  I-1794388202-review-loop-waits-for-ci-outside-the-iteration-budget/P-1794388224-bd55e506-claude-agent-claude-sonnet-5-5/E-step1-layout/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Run `uv run python scripts/check_flow_module_budgets.py` (or `uv run task lint`) for the baseline; record that
  `review/loop.py` is at 903 of 903 and `github/ci.py` at 228 of 228.
- Decide the layout: new `flow/github/ci_wait.py` (wait loop, back-off, outcome of the wait) and new
  `flow/review/ci_outcome.py` (classify settled CI into `passed`, `pending`, `failed`, `not_measured`, and render the
  wording). Both get entries in `flow_module_budgets.json` at their creation size.
- Read `tests/flow/github/test_ci.py`, `tests/flow/review/test_loop.py`, `tests/test_review_loop.py` and
  `tests/flow/test_move_proof.py` to list assertions that depend on the old wait or the old message.
- Check `docs/budgets.md`, `README.md` and `apps/sandbox-executor/README.md` for the lists of stage budget variables.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `flow_module_budgets.json` description; `constraints.md` section 1.
- **Potential failure modes for this step:** A hidden consumer of `_wait_for_pending_ci` (tests, `flow/__init__.py`
  re-exports checked by `tests/flow/test_package_exports.py`).
- **Guardrails and early‑abort checks:** `grep -rn _wait_for_pending_ci` across `apps/` before choosing to move it.

### Success & Discard Criteria

- **Success:** A written list of files to touch, baseline line counts, and every existing test that must change.
- **Discard:** None expected; if a re-export forces keeping `_wait_for_pending_ci` in `ci.py`, keep a thin delegating
  function there instead of moving it.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 1.0   |
| impact_pred         | 10.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 8.4   |

### Step Metrics Rationale

Reading only. `EV = 0.97 * 10.0 + 0.5 * 2.0 - 0.3 * 0.0 - 2.0 = 9.7 + 1.0 - 2.0 = 8.7`, shown as 8.4 after rounding the
assumed `Delta_S` penalty.

---

## Step 2: Add the CI wait budget, its resolver and the back-off wait

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small and self-contained, with injected clock and sleep; a separate branch adds overhead for no
  benefit.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Provide a wall-clock CI wait budget with bounded back-off that ends once nothing is pending.
- **Git branch:**
  I-1794388202-review-loop-waits-for-ci-outside-the-iteration-budget/P-1794388224-bd55e506-claude-agent-claude-sonnet-5-5/E-step2-ci-wait/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `budgets.py`: add `ENV_REVIEW_CI_WAIT = "HOLON_REVIEW_CI_WAIT_SECONDS"`, `DEFAULT_REVIEW_CI_WAIT_SECONDS = 1800.0`
  and `resolve_review_ci_wait_seconds(env)` built on `_read_positive` (positive finite, at most `MAX_BUDGET_SECONDS`;
  invalid logs a warning that names the variable and keeps 1800). Keep it out of `ENV_STAGE_BUDGET_MINUTES` (that map is
  minutes and tied to `StageBudgets.for_stage`), but document it next to those stage variables. Check the module-size
  and `tests/test_budgets.py` expectations on the module's public names.
- Create `flow/github/ci_wait.py` with named constants for the back-off: start at the existing
  `CI_REGISTRATION_POLL_SECONDS` (15 s), double each poll, ceiling `CI_WAIT_POLL_CEILING_SECONDS = 120.0`. A pure helper
  `next_poll_interval(current, ceiling)` returns `min(current * 2, ceiling)` so the ceiling is testable on its own.
- `wait_for_pending_ci(context, repo_dir, review_target, proxy_env, ci_status, budget_seconds, poll_interval, clock, sleep)`
  returns a small frozen result: the last probe, whether the budget was exhausted, seconds waited, and the pending check
  names at the deadline. It re-probes via `probe_ci_checks` while `pending > 0`, sleeps `min(interval, remaining)`,
  grows the interval, and stops immediately once `pending == 0`. It never changes `all_passed`. Reduce parameters to at
  most five by grouping (repo_dir, review_target, proxy_env) into one small dataclass or reusing a probe callable, so
  there is no `# noqa: PLR0913`.
- In `ci.py` delete the old `_wait_for_pending_ci` (the file shrinks) and update the budget entry downward; import the
  new function in `loop.py`. Keep `_wait_for_ci_registration` as is.
- Add `github/ci_wait.py` to `flow_module_budgets.json` at its creation size.
- If `test_move_proof.py` fails only because `_wait_for_pending_ci` moved, refresh that single entry in
  `tests/flow/fixtures/flow_py_definitions.json`... which the intent forbids editing; so instead keep a thin
  `_wait_for_pending_ci` in `ci.py` with the original name only if the test still resolves it, and say so in the
  execution record. Decide with the actual failure output.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `budgets.py` module docstring (clocks injected; the variable name, not the value, in warnings);
  `flow_module_budgets.json`.
- **Potential failure modes for this step:**
  - Back-off overshoots the deadline (mitigated by `min(interval, remaining)`).
  - A zero or negative budget loops forever (mitigated: loop exits when `remaining <= 0`).
  - Conflict between the move-proof test and the fixtures prohibition (see the last bullet above).
- **Guardrails and early‑abort checks:** run
  `uv run pytest tests/flow/github tests/test_budgets.py tests/flow/test_move_proof.py` after the step; if only the
  move-proof entry differs, do the minimal refresh and record it.

### Success & Discard Criteria

- **Success:** Wait returns as soon as pending is zero; ends exactly at the budget; intervals follow 15, 30, 60,
  120, 120.
- **Discard:** More than 1.5 times `cost_pred`, or the move-proof test needs more than the single entry refreshed.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.5   |
| impact_pred         | 25.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 19.2  |

### Step Metrics Rationale

`EV = 0.90 * 25.0 + 0.5 * 2.0 - 0.3 * 1.0 - 4.0 = 22.5 + 1.0 - 0.3 - 4.0 = 19.2`. The main risk is the move-proof
interplay, which lowers `p_success_pred` below 0.95.

---

## Step 3: Run the wait on its own clock in the review loop and record it once

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is the bottleneck, but its effect cannot be tested apart from Steps 2 and 4, so one branch with
  early test feedback is cheaper than a sub-intent.
- **Step Type:** REFACTOR
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The wait can be lifted out of the iteration timer without growing `loop.py`.
- **Learning target:** Where the iteration timer stops and starts around the CI wait, and whether pausing or excluding
  the wait time from `iteration_timer` is needed to keep the iteration budget meaning "reviewer time".
- **Maximum acceptable cost for this learning:** One extra hour-equivalent (about 4 cost units).

### Intent & Git Integration

- **Step Intent:** The post-review CI gate waits on `HOLON_REVIEW_CI_WAIT_SECONDS`, not `iteration_timer.remaining()`.
- **Git branch:**
  I-1794388202-review-loop-waits-for-ci-outside-the-iteration-budget/P-1794388224-bd55e506-claude-agent-claude-sonnet-5-5/E-step3-loop/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Intent says the reviewer ensemble "runs on settled results". Today the order is: reviewers run, then the gate waits.
  Move the pending wait ahead of the reviewer dispatch for iterations where a PR exists and CI is registered, so the
  ensemble sees settled CI evidence, and so an iteration is never charged for the wait. Because the iteration timer
  starts at iteration begin, the wait must run before the timer is created (or the timer is created after the wait);
  read the loop top to choose, and keep the change to a few lines by calling one helper from `ci_wait.py`.
- Add a CI wait outcome held at loop scope (`ci_wait_state`): the first time the budget is spent, record it and set a
  flag; later iterations do not wait again for the same pending set and do not add another record. This gives "recorded
  once, not once per iteration".
- Resolve the budget once at loop start via `resolve_review_ci_wait_seconds(os.environ)`, with an optional keyword
  argument for tests (`ci_wait_budget: float | None = None`), mirroring `ci_registration_timeout`.
- The fast path for objecting reviewers keeps its single probe without waiting.
- Pending at the deadline: set `ci_green = None` (unknown), never `all_passed`, and never `tests_failed`; do not add the
  `FIND-CI` failure row for it. Record it in the loop result as `ci_wait` (`state`, `budget_seconds`, `waited_seconds`,
  `pending_checks`) next to `ci_checks`. A run with only pending checks must not make `tests_failed` true anywhere.
- `loop.py` must not grow: delete the inline block it replaces and shorten via the helper; if it still exceeds 903, move
  the three-branch CI gate into `ci_wait.py` as one function. A budget increase is the last resort and needs a recorded
  reason in `flow_module_budgets.json`'s description or the execution record.
- Complexity: keep the loop function within ruff limits, with no `# noqa` additions.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `flow_module_budgets.json`; ruleset on append-only ledgers (no new ledger writes; the result goes
  through existing execution records only).
- **Potential failure modes for this step:**
  - Consensus semantics shift for existing tests (pending used to produce `CHANGES_REQUESTED` plus a CI finding).
  - Waiting before reviewers delays dry runs; guard with the existing `effective_dry_run` and `pr_allowed` checks.
  - Double-counting the wait in the iteration summary timer.
- **Guardrails and early‑abort checks:** run
  `uv run pytest tests/flow/review tests/test_review_loop.py tests/test_review_ensemble.py` after the edit; stop and
  re-plan if more than three existing assertions need to change.

### Success & Discard Criteria

- **Success:** With a fake clock, a loop whose CI settles after N polls completes consensus in one iteration with the
  iteration timer never charged; budget exhaustion yields `ci_wait.state == "pending"` once.
- **Discard:** `loop.py` cannot be kept at or below 903 lines without extraction, or the existing loop tests cannot be
  reconciled with fewer than three changed assertions.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 2.5   |
| impact_pred         | 35.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 28.8  |

### Step Metrics Rationale

`EV = 0.92 * 35.0 + 0.5 * 4.0 - 0.3 * 1.0 - 5.0 = 32.2 + 2.0 - 0.3 - 5.0 = 28.9`. The highest entropy sits here because
it edits the largest function in the package; it is the bottleneck, but the earlier steps lower its risk.

---

## Step 4: Classify three outcomes and replace the catch-all message

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small pure functions plus a message change; low risk and tightly coupled to Step 3.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The stage error and the finding say whether CI is pending, failed (with names) or tests not measured.
- **Git branch:**
  I-1794388202-review-loop-waits-for-ci-outside-the-iteration-budget/P-1794388224-bd55e506-claude-agent-claude-sonnet-5-5/E-step4-outcomes/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- New `flow/review/ci_outcome.py`: an enum or constants for `PENDING`, `FAILED`, `NOT_MEASURED`, plus
  `describe_ci_outcome(ci_status, ci_wait, tests_failed)` returning the wording, in Australian English:
  - pending: "CI still pending after the wait budget: <names> still running after <budget> s (budget used <waited> s);
    no test failed."
  - failed: "CI failed: <every failed check name>." using `_extract_failed_ci_check_names`.
  - not measured: reuse `TESTS_NOT_MEASURED_ERROR` wording (no CI registered and no local test result).
- In `stages/review.py` add an `elif` before the final `else` that uses `loop_res["ci_wait"]` and `ci_checks` to pick
  the pending or failed message; the final `else` text stays "test suite reported failures" only when `tests_failed`
  really holds (the loop result already tracks it; pass it in), and a remaining unexplained case reports a neutral "no
  specific blocker was recorded" message rather than blaming tests. Keep `stages/review.py` at or below 298 lines by
  moving the existing branch chain's CI branches into `ci_outcome.py` if needed.
- `ci_failure_finding` (in `review/records.py`): accept the outcome so the recorded finding text names the pending
  checks and budget, or the failed checks; no failure finding for a pure pending result unless the loop is recording the
  one-time pending note.
- Add `review/ci_outcome.py` to the line budgets.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `ledger_schema.md` (finding and error text only; no schema field changes).
- **Potential failure modes for this step:** Message order masks a more specific blocker (reviewer objection, budget
  breach); mitigated by placing the CI branches after the existing specific ones and before the final else.
- **Guardrails and early‑abort checks:**
  `uv run pytest tests/test_review_report.py tests/test_review_loop.py tests/flow/review` after edit;
  `grep -rn "test suite reported failures"` shows a single remaining use.

### Success & Discard Criteria

- **Success:** Each of the three outcomes yields its own message; the old phrase appears only under real test failure.
- **Discard:** The change requires altering the ledger schema or the `StageResult` payload shape.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.5   |
| impact_pred         | 25.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 21.4  |

### Step Metrics Rationale

`EV = 0.95 * 25.0 + 0.5 * 2.0 - 0.3 * 0.5 - 3.0 = 23.75 + 1.0 - 0.15 - 3.0 = 21.6`. Pure functions with a clear
specification.

---

## Step 5: Add fixture tests, with injected clock and sleep

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tests are cheap and share the fakes from Steps 2 to 4; splitting them off loses context.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Cover the five required behaviours without any real `gh` call or real sleep.
- **Git branch:**
  I-1794388202-review-loop-waits-for-ci-outside-the-iteration-budget/P-1794388224-bd55e506-claude-agent-claude-sonnet-5-5/E-step5-tests/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- New files directly under `apps/sandbox-executor/tests/` (for example `test_review_ci_wait.py`,
  `test_review_ci_outcome.py`, plus additions to `test_budgets.py`). Nothing is added under `tests/flow/fixtures/`, and
  existing `tests/flow/` files are edited only where an old assertion contradicts the new behaviour.
- Patch `probe_ci_checks` with a scripted sequence of probe dicts; use a fake clock object advanced by a fake sleep that
  records each interval. The existing hermetic guard (`gh_write_guard.py`, `hermetic_fixtures.py`) must stay satisfied.
- Cases: (a) pending then pass: consensus reached, zero iterations spent on the wait, the iteration timer unchanged,
  sleeps recorded; (b) the budget is exhausted: the result reports pending with names and the budget, `all_passed` is
  false, `tests_failed` is false, no failed-run record, one record across two iterations; (c) a failed check is named in
  the stage error; (d) `HOLON_REVIEW_CI_WAIT_SECONDS=900` overrides, and `abc`, `-5`, `0`, `nan` fall back to 1800 with
  a logged warning that names the variable but not the value (use `caplog`); (e) back-off sequence 15, 30, 60, 120, 120
  and `next_poll_interval` never exceeds the ceiling; (f) tests not measured wording and the old phrase only under real
  failure.
- Run `uv run pytest <new files> tests/flow tests/test_budgets.py tests/test_review_loop.py` first.

### Dependencies & Criticality

- **Depends on:** Steps 2, 3 and 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/hermetic_testing.md`; the `tests/gh_write_guard.py` guard; tests only via `uv run pytest`.
- **Potential failure modes for this step:** A test that accidentally calls the real `shutil.which("gh")` branch; fix by
  patching `shutil.which` and `probe_ci_checks`.
- **Guardrails and early‑abort checks:** No test may sleep; a pytest timeout wraps nothing here because the sleep is a
  fake. Fail the step if the new tests exceed a couple of seconds.

### Success & Discard Criteria

- **Success:** All new tests fail against the old code (check once with `git stash` style reasoning on the diff) and
  pass on the new code.
- **Discard:** Cannot assert iteration non-consumption without refactoring the loop; return to Step 3.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.0   |
| impact_pred         | 20.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 16.4  |

### Step Metrics Rationale

`EV = 0.95 * 20.0 + 0.5 * 3.0 - 0.3 * 0.0 - 4.0 = 19.0 + 1.5 - 4.0 = 16.5`. Fakes already exist, so risk is small.

---

## Step 6: Document the budget and outcomes, then run the gates

- **Sub‑intent recommendation:** NO
- **Reasoning:** Documentation plus the final checks; trivial cost.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Maintainers can find the budget, its back-off and the three outcome messages in the docs.
- **Git branch:**
  I-1794388202-review-loop-waits-for-ci-outside-the-iteration-budget/P-1794388224-bd55e506-claude-agent-claude-sonnet-5-5/E-step6-docs/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- `docs/git_flow.md`: a section on the CI wait budget (default 1800 s, `HOLON_REVIEW_CI_WAIT_SECONDS`, back-off 15 s
  doubling to a 120 s ceiling, wait ends when nothing is pending, recorded once, not drawn from the iteration budget)
  and a short table of the three outcomes and their wording, plus when "test suite reported failures" still appears.
- `docs/budgets.md`: add the variable beside the stage budget variables (table row or a note, since it is in seconds);
  check `README.md` and `apps/sandbox-executor/README.md` for any list of `HOLON_*` budget variables and add it where
  the others appear. Do not edit `docs/architecture.md`.
- Australian English (behaviour, recognise, colour) and the repository's prettier-style wrapping.
- Final gates, once each: `uv run task lint`, `uv run task lint-docs`, `uv run task test` (the full suite, no repeats);
  report pass/fail counts. Confirm `git diff --stat` shows nothing under `tests/flow/fixtures/` and not
  `docs/architecture.md`, except the single permitted move-proof refresh if it was needed, which is stated in the
  execution record.

### Dependencies & Criticality

- **Depends on:** Steps 3, 4 and 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/` lint (`lint-docs`); the three-ledger rule (no ledger edits from the executor beyond the
  normal records).
- **Potential failure modes for this step:** `lint-docs` link or format failures; a line budget breach found only at
  lint time.
- **Guardrails and early‑abort checks:** Run `uv run task lint-docs` right after the docs edit, not only at the end.

### Success & Discard Criteria

- **Success:** Docs describe the budget and outcomes; all three gates pass; the diff stays in the named scope.
- **Discard:** A gate fails for a reason outside this change (report it, do not mask it).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.5   |
| impact_pred         | 10.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 8.2   |

### Step Metrics Rationale

`EV = 0.97 * 10.0 + 0.5 * 1.0 - 0.3 * 0.0 - 2.0 = 9.7 + 0.5 - 2.0 = 8.2`. Documentation only.

---
