# Plan for I-1794456550-calci

- **Plan ID:** P-1794456566-07246b8e-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-12T04:09:27.307Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS (with one noted refinement: step detail for the timing gate needs a second `gh`
  call, see Step 1)
- **Reframed intent (if applicable):** None.
- **Exploration stance:** balanced. The wait and the failed-name extraction already exist and are reused; the only
  unknown is how to see the failed _step_ of a job, which `gh pr checks` does not expose.
- **Safety priority level:** standard
- **Priority Justification:** The change is read-only reporting after the push. It writes no ledger rows, never fails
  calibration and never merges. No rule in `world/` or `docs/safety.md` is stressed beyond the usual one-ledger and
  human-merge rules.

## Exploration

- **Proportion of steps that are exploratory:** 0.15
- **Justification:** One step (classifying a timing-gate-only failure) depends on `gh` output shape that cannot be
  exercised for real here, so it is built against injected fakes with a conservative fallback.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 6.5   |
| impact_pred         | 62.0  |
| cost_pred           | 15.0  |
| learning_value_pred | 5.0   |
| ev_pred             | 38.4  |

### Strategy Rationale

The gap is real: Stage 5 pushes the execution branch and nothing reads CI on that head. The cheapest safe design is a
new focused module `flow/github/ci_final_head.py` that composes the existing `probe_ci_checks`, `wait_for_pending_ci`
(`CiWaitPolicy`) and `_extract_failed_ci_check_names`, classifies the outcome and returns a plain dict.
`stages/calibrate.py` gains only a call and a payload key, so its line budget moves by a handful of lines (reason
recorded in `flow_module_budgets.json`). No new polling loop, no policy or rerun change, and the stage status stays
`SUCCESS` whatever the CI outcome.

Metrics: the plan is a chain, so `p_success_pred` is the product of the bottleneck steps (Step 2 classification at 0.90,
Step 3 wiring at 0.95, others near 0.97), about 0.82. `entropy_pred` is the sum of step entropies (6.5, within the 15.0
budget). Impact is the maximum-style judgement: the flow honestly reporting final-head CI is the whole value. Cost is
the sum of step costs. `EV = 0.82 * 62.0 + 0.5 * 5.0 - 0.3 * 0.0 - 15.0 = 50.84 + 2.5 - 15.0 = 38.34`, rounded to 38.4
in the table.

Assumption about the timing gate: `gh pr checks` returns job-level checks (name, bucket, state, workflow, link), not
step results. The timing gate is a step (`CI Timing Gate (Check <= 5 min)`) inside a test job. So a timing-gate-only
verdict needs the job's step list from one extra `gh run view <run-id> --json jobs` call per failed job. When that call
fails or the steps are unavailable, the outcome degrades to a plain `failed` (never to success and never to
`timing_gate`): conservative by design.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `docs/safety.md` core principles 1 and 5: git is the boundary, merge stays with the human maintainer.
  - `docs/ledger_schema.md`: exactly three append-only ledgers; this plan adds no ledger and no row type.
  - `apps/sandbox-executor/flow_module_budgets.json`: per-module line budgets, checked by `uv run task lint`.
  - Hermetic test rules (`docs/testing/testing_guide.md`, `tests/hermetic_fixtures.py`, `gh_write_guard.py`): no real
    docker, `gh`, git remote or model call.
- **Potential violations or edge cases:**
  - `calibrate.py` is at its budget (316 lines); any added line needs a raised budget with a reason.
  - The flow summary lives in `cli/commands/flow.py`, outside the stated scope list.
  - `skip_push`, dry run, and a failed calibration (nothing pushed) must not wait.
  - The probe falls back to a fake `local_tests` pass when `gh` is missing; that must not read as final-head success.
  - The PR may not exist (Stage 4 skipped); then the outcome is `unknown`.
  - Stage budget: calibration has a 600 s stage ceiling, but the CI wait is 1800 s by default.
- **Mitigations built into the plan:**
  - New module for all logic; `calibrate.py` gets a one-call hook and a recorded budget reason.
  - A two-line summary hook in `cli/commands/flow.py` calling a formatter that lives in the new module; flagged as a
    minimal scope extension that the acceptance criterion "flow summary" requires.
  - Only a `github_cli` probe source counts as measured; `local_tests` yields `unknown`.
  - The wait budget is the existing `resolve_review_ci_wait_seconds`; `calibrate` is exempt from the over-budget
    downgrade in `timed_execute_stage`, so an overrun is logged only. The plan documents this.
  - The whole check is wrapped so it never raises: any exception yields `unknown` with a bounded, redacted reason.
- **Residual risk accepted (and why):** The real `gh run view` JSON shape is verified only through fakes; a drift
  degrades to `failed`, which over-reports rather than hides. Acceptable for a report-only feature.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 6.5 (`1.2 + 1.8 + 1.2 + 1.0 + 0.8 + 0.5`)
- **Budget Compliance:** The strategy fits within budget.

## Plan Description & Strategy

Add `flow/github/ci_final_head.py` with `check_final_head_ci(context, repo_dir, branch, ...)` returning a dict
`{state, failed_checks, timing_gate_checks, wait, reason}` where `state` is one of `success`, `failed`, `timing_gate`,
`unknown`, `skipped`. Hook it in `run_calibrate_stage` after the final `git push origin <execution branch>`, store the
dict as `payload["final_head_ci"]`, log one line, and print it in the flow summary. Test with fakes for probe, steps,
clock and sleep. Document in `docs/git_flow.md` (Stage 5 and CI wait sections) and `docs/budgets.md`.

---

## Step 1: Define the outcome model and the timing-gate classifier

- **Sub-intent recommendation:** NO
- **Reasoning:** Small pure-function module, low risk, fully unit-testable; no reason for a separate branch.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The failed step names of a failed job can be read with one `gh run view <id> --json jobs`
  call and the run id parsed from the check `link`.
- **Learning target:** Whether the check JSON carries enough (`link`) to find the run id, and the minimal fallback when
  not.
- **Maximum acceptable cost for this learning:** One extra `gh` call per failed job, capped at a few jobs, timeout 30 s.

### Intent & Git Integration

- **Step Intent:** Provide pure helpers that turn a probe result plus optional step data into a final-head outcome.
- **Git branch:** I-1794456550-calci/step-1-outcome-model
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create `flow/github/ci_final_head.py`, Australian English in docstrings, target-native paths only.
- Constants: the timing gate step name `CI Timing Gate (Check <= 5 min)` (match by the `CI Timing Gate` prefix so the
  limit text can change without breaking the match; the 300 s limit itself is untouched).
- `classify_final_head(ci_status, wait_record, step_lookup)`: if the source is not `github_cli`, or the wait was
  exhausted with checks pending, return `unknown`. If failed is 0 and pending is 0 and all passed, return `success`.
  Otherwise take the names from `_extract_failed_ci_check_names`. For each failed check call `step_lookup(check)` which
  returns the failed step names, or `None` when unavailable. If every failed check has a known failed-step set equal to
  only the timing gate step, return `timing_gate`; anything else, including unknown steps, is `failed`.
- A failed check named as a test job with a failed non-timing step is `failed` and its name is listed.
- `parse_run_id(link)` extracts the run id from an Actions URL; returns `None` on no match.
- `gh_step_lookup(repo_dir, proxy_env)` builds the real lookup: `gh run view <id> --json jobs`, maps job name to the
  failed steps (`conclusion == failure`); every error returns `None`. Subprocess is a single injectable seam.
- `format_final_head_ci(record)` returns the one-line summary text used by the flow summary and the stage log (for
  example `final head CI: failed: <names>` or `final head CI: timing gate only: <names>`; `unknown` says why).

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (classification is the core of the intent)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` (human merge), `flow_module_budgets.json`.
- **Potential failure modes for this step:**
  - Timing gate mis-reported as pass when steps are missing.
  - Module over the budget (new module needs a budget entry at creation size).
- **Guardrails and early‑abort checks:**
  - Unknown steps always map to `failed`, never `timing_gate`.
  - `success` requires `all_passed` and a `github_cli` source.

### Success & Discard Criteria

- **Success:** Pure helpers cover all five states under unit tests; ruff complexity limits met without `noqa`.
- **Discard:** The `link` field is absent from `gh pr checks --json`; then fall back to `failed` only and defer the
  timing-gate distinction to a sub-intent (reduces p_success to about 0.5).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.2   |
| impact_pred         | 40.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 34.0  |

### Step Metrics Rationale

`EV = 0.90 * 40.0 + 0.5 * 4.0 - 4.0 = 36.0 + 2.0 - 4.0 = 34.0`. Main uncertainty is the `gh` JSON shape; `gh pr checks`
does list `link`, and Actions links embed `/runs/<id>/`, so 0.90.

---

## Step 2: Wait for CI on the final head using the existing facility

- **Sub-intent recommendation:** NO
- **Reasoning:** Thin composition of `probe_ci_checks`, `CiWaitPolicy` and `wait_for_pending_ci`; no new loop.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Provide `check_final_head_ci` that probes, waits within the existing budget and classifies.
- **Git branch:** I-1794456550-calci/step-2-final-head-wait
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- `check_final_head_ci(context, repo_dir, branch, *, proxy_env, policy=None, probe=None, step_lookup=None)` keeps the
  parameter count at five or fewer by bundling optional seams in a small frozen dataclass if needed (PLR0913).
- Return `skipped` immediately when `context.dry_run`, `context.skip_push`, or the caller says nothing was pushed; no
  probe, no sleep.
- Probe via `functools.partial(probe_ci_checks, repo_dir, branch, proxy_env=proxy_env)`; wait via `settle_pending_ci`
  (it already skips non-`github_cli` sources and records a `CiWaitResult`).
- Budget: `CiWaitPolicy(resolve_review_ci_wait_seconds(os.environ))`, the same wait budget style as Stage 4; the
  pending-at-timeout case yields `unknown`, never success.
- Registration lag: a push returns before GitHub registers new runs, so when the probe shows no `github_cli` checks yet,
  reuse `_wait_for_ci_registration` (existing) with `resolve_ci_registration_timeout()` before the first probe.
- Wrap in try/except returning `unknown` with a redacted, bounded reason (`CredentialRedactor.redact_text`).
- Result record: `state`, `failed_checks`, `timing_gate_checks`, `ci_wait` (from `CiWaitResult.as_record`), `reason`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `docs/budgets.md` (stage and wait budgets), `docs/git_flow.md#ci-wait-budget-and-outcomes`.
- **Potential failure modes for this step:**
  - Stale checks from the previous head read as the final head's result right after the push.
  - Wait blocks the pipeline for up to 1800 s.
- **Guardrails and early‑abort checks:**
  - Probe the pushed branch only after the push returned 0; compare the head SHA if the probe exposes it, else rely on
    the registration wait and document the limit.
  - Wait is bounded by the existing budget and logged; dry run and skip-push never wait.

### Success & Discard Criteria

- **Success:** All wait paths use `wait_for_pending_ci`; no `while` polling added in the new module.
- **Discard:** The stale-head risk cannot be reduced without a new `gh` field; then record it as a documented limit
  rather than adding a poller.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.8   |
| impact_pred         | 50.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 44.5  |

### Step Metrics Rationale

`EV = 0.92 * 50.0 + 0.5 * 3.0 - 3.0 = 46.0 + 1.5 - 3.0 = 44.5`. The stale-head ambiguity is the main entropy source.

---

## Step 3: Hook Stage 5 and the flow summary

- **Sub-intent recommendation:** NO
- **Reasoning:** A few lines in two files plus a budget reason; low risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Call the check after the Stage 5 push, put the record in the stage payload and the summary.
- **Git branch:** I-1794456550-calci/step-3-stage5-hook
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `stages/calibrate.py`, after the `git push origin <execution branch>` block, when not dry run, calibration
  succeeded and the push actually ran (not `skip_push`), call `check_final_head_ci`; store `payload["final_head_ci"]`;
  otherwise store a `skipped` record with the reason so the key is always present. Keep the stage status `SUCCESS`
  always (calibration success unchanged; no change to the push, rerun or ledger behaviour).
- Log one line through `context.log` using `format_final_head_ci`.
- In `cli/commands/flow.py::_report_flow_summary`, after the calibrate line, print `format_final_head_ci` when the
  record exists and is not `skipped` (about 3 lines). Name this as a scope extension in the PR text.
- Raise `stages/calibrate.py` in `flow_module_budgets.json` by the measured line increase and append the reason to the
  file's description (hook for final-head CI check). Add a budget entry for `github/ci_final_head.py` at creation size.
- Avoid ruff `noqa` additions; `run_calibrate_stage` already carries `noqa`, so put the new block in a small helper in
  `calibrate.py` to limit growth.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `flow_module_budgets.json` description; `timed_execute_stage` calibration exemption.
- **Potential failure modes for this step:**
  - The CI wait pushes the stage beyond 600 s; the stage is exempt from the downgrade but the overrun appears in
    `budget_overrun`.
  - Checkpoint serialisation of the payload (must be JSON-safe).
- **Guardrails and early‑abort checks:**
  - Never raise from the hook; never alter `status`.
  - Payload is plain dicts, lists and strings.

### Success & Discard Criteria

- **Success:** `uv run task lint` (incl. module budgets) passes; the summary shows the line for a failed record.
- **Discard:** Hook growth pushes `calibrate.py` well past 400 lines; move the helper into the new module.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.2   |
| impact_pred         | 55.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 50.8  |

### Step Metrics Rationale

`EV = 0.95 * 55.0 + 0.5 * 2.0 - 2.5 = 52.25 + 1.0 - 2.5 = 50.75`, shown as 50.8.

---

## Step 4: Hermetic tests

- **Sub-intent recommendation:** NO
- **Reasoning:** Fakes only, existing patterns (`test_review_ci_wait.py`, `review_fakes.py`).
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove each required outcome without docker, `gh`, a git remote or a model call.
- **Git branch:** I-1794456550-calci/step-4-tests
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- New `tests/test_calibrate_final_head_ci.py`, modelled on `tests/test_review_ci_wait.py` (fake clock, fake sleep,
  injected probe and step lookup; `run_git` monkeypatched as in `test_calibration.py`).
- Cases: all green is `success`; a failing test job lists its name and is `failed`; a timing-gate-only failure is
  `timing_gate` with the job name listed; a mixed failure (timing gate plus a failed test step) is `failed`; unknown
  steps are `failed`; pending then green within budget is `success` and the fake sleep was used; pending past the budget
  is `unknown` with the pending names and not success; `local_tests` source is `unknown`; dry run and skip-push make no
  probe call and no sleep; a probe exception is `unknown`; a failed final-head CI keeps the Stage 5 status `SUCCESS`;
  the 300 s constant is not referenced or altered.
- Stage test: `run_calibrate_stage` payload contains `final_head_ci`; the flow summary function prints the line.
- Run order: `uv run pytest apps/sandbox-executor/tests/test_calibrate_final_head_ci.py`, then the neighbouring
  `test_calibration.py`, `test_review_ci_wait.py` and `test_budgets.py`, then the full suite once with
  `uv run task test`. No repeated runs are needed; no timing measurement is involved.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/hermetic_testing.md`, `tests/gh_write_guard.py`, `test_sandbox_hermetic_guard.py`.
- **Potential failure modes for this step:**
  - A test reaches a real `gh` through the unpatched default lookup.
  - Existing Stage 5 tests break because a push now triggers a probe.
- **Guardrails and early‑abort checks:**
  - Patch the probe seam in existing calibrate tests (or default `skip_push`); hermetic guard fixtures stay on.

### Success & Discard Criteria

- **Success:** New and existing tests pass via `uv run pytest`, then `uv run task test` once.
- **Discard:** More than two unrelated existing tests need edits; reconsider the hook placement.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.0   |
| impact_pred         | 40.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 36.0  |

### Step Metrics Rationale

`EV = 0.95 * 40.0 + 0.5 * 2.0 - 3.0 = 38.0 + 1.0 - 3.0 = 36.0`.

---

## Step 5: Documentation

- **Sub-intent recommendation:** NO
- **Reasoning:** Prose only; must pass `uv run task lint-docs`.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Name the Stage 5 behaviour change where maintainers will look.
- **Git branch:** I-1794456550-calci/step-5-docs
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- `docs/git_flow.md`: in the Stage 5 section and the CI wait section, describe the final-head check: when it runs,
  states (`success`, `failed`, `timing_gate`, `unknown`, `skipped`), the use of `HOLON_REVIEW_CI_WAIT_SECONDS`, the
  summary line, and that it never fails calibration, reruns CI or changes the 300 s limit; merging stays with the human.
- `docs/budgets.md`: note that the Stage 5 CI wait draws on the review CI wait budget and that calibration overrun is
  logged, not failed.
- Australian English, Prettier-clean wrapping at the repo's width; run `uv run task lint-docs`.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `lint-docs`; docs must not contradict `docs/ledger_schema.md`.
- **Potential failure modes for this step:**
  - Prettier oscillation on tables or long lines.
- **Guardrails and early‑abort checks:**
  - Run the docs formatter and lint before committing.

### Success & Discard Criteria

- **Success:** `uv run task lint-docs` passes and the behaviour is stated once, accurately.
- **Discard:** None expected.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.8   |
| impact_pred         | 20.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 18.4  |

### Step Metrics Rationale

`EV = 0.97 * 20.0 + 0.5 * 1.0 - 1.5 = 19.4 + 0.5 - 1.5 = 18.4`.

---

## Step 6: Final verification

- **Sub-intent recommendation:** NO
- **Reasoning:** Gate run only.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Confirm all gates are green and constraints are met.
- **Git branch:** I-1794456550-calci/step-6-verify
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- `uv run task lint`, `uv run task lint-docs`, then `uv run task test` once.
- Check that only the three ledgers exist, no new dependency in `pyproject.toml`, and the diff touches only
  `calibrate.py`, `flow/github/ci_final_head.py`, `cli/commands/flow.py` (summary hook), `flow_module_budgets.json`,
  tests and docs.

### Dependencies & Criticality

- **Depends on:** Steps 4 and 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `scripts/check_flow_module_budgets.py`, `docs/ledger_schema.md`.
- **Potential failure modes for this step:**
  - Budget check fails on a missed entry.
- **Guardrails and early‑abort checks:**
  - Each gate is run on its own and fixed before the next.

### Success & Discard Criteria

- **Success:** Every gate passes.
- **Discard:** A gate failure that needs out-of-scope edits.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.5   |
| impact_pred         | 10.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 9.0   |

### Step Metrics Rationale

`EV = 0.97 * 10.0 + 0.5 * 0.5 - 1.0 = 9.7 + 0.25 - 1.0 = 8.95`, shown as 9.0.

---
