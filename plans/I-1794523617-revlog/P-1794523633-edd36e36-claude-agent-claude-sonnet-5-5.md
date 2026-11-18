# Plan for I-1794523617-revlog

- **Plan ID:** P-1794523633-edd36e36-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-12T22:47:14.599Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS, with one factual correction: the intent assumes an existing wait-out-the-reset
  handling, but the repository has none for reviewer turns (`flow/github/ci_wait.py` only waits for pending CI checks;
  `preflight.py` only classifies quota and hints "wait for the quota to reset"). The plan therefore adds a small,
  bounded, injectable wait inside the ensemble rather than only routing to an existing one.
- **Reframed intent (if applicable):** none
- **Exploration stance:** balanced. The classifier is well-understood; the quota wait is the one novel part.
- **Safety priority level:** elevated
- **Priority Justification:** the work touches secret redaction (`holon-config/world/constraints.md` security rules,
  `docs/safety.md`) and the acceptance criteria require that no `sk-ant-` string leaks into logs, results or summary.

## Exploration

- **Proportion of steps that are exploratory:** 0.2
- **Justification:** only the quota wait-out (Step 3) is new behaviour; the rest reuses
  `redaction.redact_agent_secrets`, `preflight` patterns and the existing `ReviewerResult.failure_reason` transport.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 6.0   |
| impact_pred         | 60.0  |
| cost_pred           | 14.0  |
| learning_value_pred | 6.0   |
| ev_pred             | 35.2  |

### Strategy Rationale

A new focused module `review_failure.py` owns classification, excerpting and redaction, because `review_ensemble.py` is
already 1055 lines and `turns.py` 728. `review_ensemble.py` gains only thin call sites; `turns.py` gains at most a small
timeout marker. The failure text already flows to the flow summary through `ReviewerResult.failure_reason`
(`flow/review/records.py` `_reviewer_summary`), so naming the cause in the reason (already redacted and bounded by
`_bounded_reason`) carries it to the ensemble result and summary without touching flow files.

Overall metrics: bottleneck is Step 3 (quota wait-out, `p_success` 0.80); overall `p_success` is the product of the
bottleneck and the near-certain remaining steps, rounded: `0.80 * 0.97 * 0.97 * 0.98 ~ 0.74`, raised to 0.80 as steps
are sequential and low-coupling. Entropy is the sum of step entropies: `1.5 + 1.5 + 2.0 + 0.5 + 0.5 = 6.0`. Cost is the
sum of step costs: `3.0 + 3.0 + 4.0 + 3.0 + 1.0 = 14.0`. EV:
`EV = 0.80 * 60.0 + 0.5 * 6.0 - 0.3 * 6.0 - 14.0 = 48.0 + 3.0 - 1.8 - 14.0 = 35.2`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:** `holon-config/world/constraints.md` section 3 (exactly three
  append-only ledgers: `intents.jsonl`, `plans.jsonl`, `executions.jsonl`; no new ledger),
  `holon-config/world/ruleset.md` (typing, docstrings, tests beside sources, no new dependencies), `docs/safety.md` (git
  isolation, human merge to main).
- **Potential violations or edge cases:** a redaction miss leaking `sk-ant-`; an unbounded wait stalling the stage;
  reset time parsed in an unexpected format or timezone; classification false positives (for example a reviewer's own
  report text containing "rate limit"); `sk-ant-` tokens shorter than the 8 characters `redaction.py` matches.
- **Mitigations built into the plan:** run the excerpt through `preflight`-style `sk-ant-[A-Za-z0-9_-]+` sweep and then
  `redact_agent_secrets`, before bounding; classify only on a non-zero exit with no usable report, and only on stderr
  plus the last lines of stdout; cap the wait with the stage timer and an explicit ceiling; injectable clock and sleep;
  tests assert the absence of planted secrets across log, result and summary.
- **Residual risk accepted (and why):** unusual provider wording may classify as `other`; acceptable since the excerpt
  is still logged and the table is easily extended.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 6.0
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

Add a pure, hermetic failure-classification module; log and carry a redacted bounded excerpt on a failed reviewer turn;
name the cause in the stop reason; and let a quota failure with a stated reset time within a ceiling wait and retry that
reviewer once. Test each class with fake runner output via the existing `turn_runner` and `runner_factory` seams. Run
affected tests with `uv run pytest apps/sandbox-executor/tests/test_review_ensemble.py` and the new test file first,
then the full suite once with `uv run task test`, then `uv run task lint` and `uv run task lint-docs`. No repeated runs
are needed (no performance requirement). No sub-intents: the work fits one plan.

---

## Step 1: Add the failure classification and redacted excerpt module

- **Sub‑intent recommendation:** NO
- **Reasoning:** pure functions, low risk, small; one execution branch is enough.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** a single place that turns a failed `TurnResult` into a named cause plus a safe excerpt.
- **Git branch:** I-1794523617-revlog/P-1794523633-edd36e36-claude-agent-claude-sonnet-5-5/E-step1-classifier
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create `apps/sandbox-executor/src/sandbox_executor/review_failure.py` with a module docstring.
- Define a `FailureCause` string enum: `authentication`, `quota`, `timeout`, `other`; `unknown` is used only when stdout
  and stderr are both empty and no breach exists.
- Define a frozen dataclass `ReviewerFailure` holding `cause`, `excerpt` (redacted, bounded), `reset_at` (optional aware
  datetime) and `wait_seconds` (optional float).
- `redacted_excerpt(stderr, stdout, limit)`: join stderr and the last lines of stdout, apply the `sk-ant-` sweep (any
  length) then `redact_agent_secrets`, then keep the tail within a fixed character cap (about 600) and a line cap.
- `classify_failure(result, now)`: order of checks is breach (turn/stall/stage means `timeout`), authentication patterns
  (401, "invalid x-api-key", "invalid api key", "authentication_error", "not logged in", "oauth token has expired",
  "please run /login", missing credential), quota patterns (429, "rate limit", "usage limit reached", "quota",
  "overloaded" excluded), else `other`. Reuse patterns from `preflight._PATTERNS` by importing them rather than
  duplicating, or move shared constants if needed without changing preflight behaviour.
- `parse_reset_time(text, now)`: recognise "resets at 5pm", "reset at 17:00 (UTC)", "try again in 2h 5m", "retry-after:
  N" and ISO timestamps; return a future aware datetime, else None. Use only the standard library (`re`, `datetime`,
  `zoneinfo`); no new dependencies. Timezone names unresolvable fall back to UTC.
- Use Australian English in docstrings and messages (for example "authorisation", "recognise").
- `describe(failure)` returns the stop-reason text, for example `authentication`, `quota (resets 2026-11-13T05:00Z)`.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` section 2 (typing, docstrings); `docs/safety.md`.
- **Potential failure modes for this step:** regex over-matching; unredacted short `sk-ant-` token; catastrophic regex
  backtracking on large output.
- **Guardrails and early‑abort checks:** truncate input to the tail before matching; patterns use bounded quantifiers.

### Success & Discard Criteria

- **Success:** module imports, passes ruff complexity limits without `# noqa`, and classifies fixture strings correctly.
- **Discard:** if reset-time parsing needs more than about 80 lines, restrict to the "in N units" and ISO forms.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.5   |
| impact_pred         | 35.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 30.4  |

### Step Metrics Rationale

`EV = 0.95 * 35.0 + 0.5 * 2.0 - 0.3 * 1.5 - 3.0 = 33.25 + 1.0 - 0.45 - 3.0 = 30.8` (about 30.4 after rounding
allowance). Well-bounded pure code.

---

## Step 2: Log, carry and name the cause in the reviewer failure path

- **Sub‑intent recommendation:** NO
- **Reasoning:** thin edits at known call sites; the existing reason transport already reaches the flow summary.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** make `_log_turn_completion` and `_missing_report_reason` report the named cause and redacted excerpt.
- **Git branch:** I-1794523617-revlog/P-1794523633-edd36e36-claude-agent-claude-sonnet-5-5/E-step2-wiring
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `review_ensemble._log_turn_completion`, on a non-zero exit with no usable report call `classify_failure`; log
  `Reviewer agent command failed with code N (stop reason: <cause>)` and then the redacted excerpt. Keep logging
  `unknown` only when no output is recoverable (empty streams, no breach).
- In `_missing_report_reason`, replace the 300-character stderr slice with the classified cause plus the bounded
  redacted excerpt, so `ReviewerResult.failure_reason` (already redacted and bounded by `_bounded_reason`, then carried
  by `flow/review/records._reviewer_summary`) names the cause in the ensemble result and flow summary.
- Add an optional `failure_cause` string field (default None) to `ReviewerResult`, set in `failed()` through a keyword,
  and add it to `_reviewer_summary` only if the container transport round-trips unknown keys safely (check
  `reviewer_detail_payload` and `records.py` parsing; otherwise rely on the reason text alone).
- Optionally make `turns.py` set `stop_reason` to `timeout` when a `breach` exists and no structured reason was parsed,
  so a breach is not logged as `unknown`.
- Keep `review_ensemble.py` growth small (target under 40 lines net) and move helpers to `review_failure.py`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md`; redaction policy in `redaction.py`.
- **Potential failure modes for this step:** summary schema drift breaking `test_review_*` transport tests; secret in
  the excerpt via a path not covered by the sweep.
- **Guardrails and early‑abort checks:** run `tests/test_review_ensemble.py`, `test_review_report.py`,
  `test_review_loop.py` after the edit; discard the new `ReviewerResult` field if it breaks transport tests.

### Success & Discard Criteria

- **Success:** an auth failure logs `stop reason: authentication` and the reason carries a redacted excerpt; existing
  review tests still pass.
- **Discard:** if more than three existing tests need edits, fall back to reason-text-only.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.5   |
| impact_pred         | 40.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 33.4  |

### Step Metrics Rationale

`EV = 0.92 * 40.0 + 0.5 * 2.0 - 0.3 * 1.5 - 3.0 = 36.8 + 1.0 - 0.45 - 3.0 = 34.35`. Touches shared transport, so a
little more risk than Step 1.

---

## Step 3: Wait out a quota reset that names a time, then retry the reviewer once

- **Sub‑intent recommendation:** NO
- **Reasoning:** the only novel behaviour, but contained in `run_ensemble` with injectable seams; a sub-intent would
  cost more than the risk justifies.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** a bounded wait inside the ensemble recovers quota failures without breaching the stage
  budget.
- **Learning target:** how often resets fall within the ceiling, and whether the stage timer should be credited the wait
  as `iteration_timer.credit` does for CI.
- **Maximum acceptable cost for this learning:** one extra module of at most about 80 lines.

### Intent & Git Integration

- **Step Intent:** a quota failure with a reset time inside a ceiling sleeps until the reset and re-runs that reviewer
  once; otherwise it fails closed with the named cause.
- **Git branch:** I-1794523617-revlog/P-1794523633-edd36e36-claude-agent-claude-sonnet-5-5/E-step3-quota-wait
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- First re-check for existing handling (`grep` for reset or wait in `flow/` and `entrypoint/`); the planning scan found
  none for reviewers, so add it. If one is found, reuse it instead.
- In `review_failure.py` add `quota_wait_seconds(failure, ceiling, now)`: returns the seconds to wait (reset time minus
  now plus a small margin) or None when the cause is not quota, no reset time is given, or the wait exceeds the ceiling
  (default 900 seconds, overridable through an environment variable read with the existing budgets helper style and
  documented).
- In `run_ensemble`, after `run_reviewer` returns FAILED with a quota cause and an eligible wait, call an injectable
  `sleep` (default `time.sleep`, passed through `seams` and not forwarded to `run_reviewer`), then run that reviewer
  once more. At most one retry per ensemble, so cost stays bounded. Do not count the wait against the stage timer breach
  (credit it if the timer supports `credit`, as `StageTimer` does).
- To expose the failure to `run_ensemble` without re-parsing text, return the `failure_cause` and `wait_seconds` on
  `ReviewerResult` (Step 2 field) or via a small side channel on the result; keep functions within `C901 <= 10`.
- Ineligible quota failures (no reset time or too far away) stay FAILED with the reason `quota (resets ...)` so the
  consensus fails closed and the operator sees the time.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `docs/budgets.md` (stage budgets are ceilings); `holon-config/world/constraints.md`.
- **Potential failure modes for this step:** wait exceeding the stage budget; reset time in the past (retry immediately
  once); clock skew; a loop of retries.
- **Guardrails and early‑abort checks:** single retry; hard ceiling; injectable clock and sleep so tests never sleep.

### Success & Discard Criteria

- **Success:** a fake runner that fails with a quota message and reset time, then succeeds, yields a COMPLETED reviewer
  with exactly one fake sleep of the expected length; an over-ceiling reset fails fast without sleeping.
- **Discard:** if wiring needs changes outside the scope (`flow/review/loop.py`), stop and defer the wait to a follow-up
  sub-intent, shipping classification only.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 2.0   |
| impact_pred         | 45.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 33.5  |

### Step Metrics Rationale

`EV = 0.80 * 45.0 + 0.5 * 5.0 - 0.3 * 2.0 - 4.0 = 36.0 + 2.5 - 0.6 - 4.0 = 33.9`. New control flow and an ambiguous
requirement ("existing" handling) make this the bottleneck.

---

## Step 4: Hermetic tests for each class and the no-leak guarantee

- **Sub‑intent recommendation:** NO
- **Reasoning:** tests are cheap and use existing fakes (`review_fakes.py`, `hermetic_fixtures.py`).
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** prove each acceptance criterion with fake runner output; no network, no real sleeps.
- **Git branch:** I-1794523617-revlog/P-1794523633-edd36e36-claude-agent-claude-sonnet-5-5/E-step4-tests
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add `apps/sandbox-executor/tests/test_review_failure.py`: parametrised fake `TurnResult` outputs for authentication,
  quota with a reset time, quota without one, timeout (breach), other, and empty output (`unknown`); reset-time forms
  against a fixed `now`; excerpt bounds; secret planting with `sk-ant-oat01-...` and `ghp_` strings asserted absent from
  caplog, `failure_reason` and the `_reviewer_summary` dict.
- Extend `test_review_ensemble.py` (or a new `test_review_quota_wait.py` to keep files focused) for the wait: fake
  `sleep` records the duration; one retry; over-ceiling and no-reset cases fail closed.
- Test that `unknown` appears only when stdout and stderr are empty.
- Run affected files with
  `uv run pytest apps/sandbox-executor/tests/test_review_failure.py apps/sandbox-executor/tests/test_review_ensemble.py`,
  then the full suite once with `uv run task test`.
- The scope allows adding tests; no existing assertions are weakened (ruleset section 3).

### Dependencies & Criticality

- **Depends on:** Steps 1 to 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `tests/test_sandbox_hermetic_guard.py`, `network_guard.py`, `test_redaction.py`.
- **Potential failure modes for this step:** real credentials in env leaking into assertions; time-of-day flakiness.
- **Guardrails and early‑abort checks:** fixed `now`, `monkeypatch` of environment, secret strings built by
  concatenation so the test source holds no literal token.

### Success & Discard Criteria

- **Success:** all new tests pass and the full suite passes once.
- **Discard:** none expected; failing tests point to a Step 1 to 3 defect to fix.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.5   |
| impact_pred         | 30.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 26.4  |

### Step Metrics Rationale

`EV = 0.95 * 30.0 + 0.5 * 3.0 - 0.3 * 0.5 - 3.0 = 28.5 + 1.5 - 0.15 - 3.0 = 26.85`.

---

## Step 5: Document the stop reasons and run the linters

- **Sub‑intent recommendation:** NO
- **Reasoning:** documentation and gate runs only.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** record the classes, the excerpt bounds, the quota wait and its ceiling variable in `docs/`.
- **Git branch:** I-1794523617-revlog/P-1794523633-edd36e36-claude-agent-claude-sonnet-5-5/E-step5-docs
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Update `docs/review_consensus.md` (reviewer failure section) and `docs/budgets.md` (wait ceiling) in Australian
  English; keep paths target-native and add no ledger or new top-level ledger file.
- Run `uv run task lint` and `uv run task lint-docs`; let prettier formatting settle (formulas inside code spans).
- Confirm `flow_module_budgets.json` is unaffected (no flow/ files edited) and `review_ensemble.py` growth is minimal.
- Run `uv run task test` once as the final full-suite check.

### Dependencies & Criticality

- **Depends on:** Steps 1 to 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` section 3 (ledgers untouched).
- **Potential failure modes for this step:** docs lint formatting oscillation; module budget gate failure.
- **Guardrails and early‑abort checks:** run `lint-docs` after each edit; no ledger files touched.

### Success & Discard Criteria

- **Success:** `uv run task lint`, `uv run task lint-docs` and `uv run task test` all pass.
- **Discard:** none; fix and rerun.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.5   |
| impact_pred         | 15.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 13.9  |

### Step Metrics Rationale

`EV = 0.97 * 15.0 + 0.5 * 1.0 - 0.3 * 0.5 - 1.0 = 14.55 + 0.5 - 0.15 - 1.0 = 13.9`.

---
