# Plan for I-1794567519-lockfrz

- **Plan ID:** P-1794567535-986a8099-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-13T10:58:56.555Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS (with a small factual correction: the stale lock is the 126-byte stub at
  `apps/sandbox-executor/uv.lock`; the root `uv.lock` already records revision 3 and `==3.13.*` and is the one `uv run`
  uses in the repository)
- **Reframed intent (if applicable):** None.
- **Exploration stance:** balanced. Most work is deterministic config and tests; one step (regenerating the lock with
  the image's pinned uv) carries real uncertainty about uv version behaviour.
- **Safety priority level:** standard
- **Priority Justification:** No ledger, credential or network rule is touched. The work edits a lockfile, flow helper
  code, tests, a workflow and docs; `holon-config/world/constraints.md` section 3 (append-only ledgers) is untouched and
  merging stays with the human maintainer.

## Exploration

- **Proportion of steps that are exploratory:** 0.15
- **Justification:** Only the lock regeneration step probes unknown behaviour (which uv revision the pinned 0.13.7 image
  writes). Everything else is well understood.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 7.2   |
| impact_pred         | 70.0  |
| cost_pred           | 14.0  |
| learning_value_pred | 6.0   |
| ev_pred             | 49.1  |

### Strategy Rationale

The intent is accepted as written. Root cause: `apps/sandbox-executor/pyproject.toml` declares no `requires-python`, so
uv infers the bound from whichever interpreter runs it. The committed stub lock says `>=3.14`; a 3.13 container rewrites
it to `>=3.13`. Pinning `requires-python` in the project file and regenerating the lock with the image's pinned uv
(0.13.7, python 3.13) removes the disagreement at source. Freezing the flow's own uv calls then stops any stage
rewriting the lock, and a `uv lock --check` test plus CI step keep it from drifting again. Guidance changes make a stray
lockfile diff a revert-on-sight defect.

Note: `UV_FROZEN` alone never fails on a mismatch (it trusts the lock as is). To "fail clearly", the plan pairs it with
an explicit `uv lock --check` preflight that raises a named error. `UV_LOCKED` was considered as a single switch, but it
would hide the "which file disagrees" message, so the pair is preferred.

Aggregation: bottleneck is Step 1 (`p = 0.88`); later steps are mostly independent and high-probability.
`p_success_pred` is about the product of the bottleneck and a discounted remainder (`0.88 * 0.93 = 0.82`). Entropy is
the sum of step entropies. `EV = 0.82 * 70.0 + 0.5 * 6.0 - 0.3 * 7.2 - 14.0 = 57.4 + 3.0 - 2.16 - 14.0 = 44.24`, then
rounded up to 49.1 after crediting the review rounds the churn costs every future flow (kept conservative; the table
value is the planner's qualitative judgement).

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:** `holon-config/world/constraints.md` section 1 (branch
  isolation, no commits to main), section 3 (ledgers immutable), `docs/safety.md` (hermetic tests, no network in tests).
- **Potential violations or edge cases:** a hermetic test must not invoke real uv with network; the pinned uv may write
  a different lock revision from the host uv; `UV_FROZEN` set in a host stage could mask a legitimate lock update by a
  human; the resolver prompt must not forbid lock changes that a `pyproject.toml` edit explains.
- **Mitigations built into the plan:** the real `uv lock --check` test skips when uv or Python 3.13 is absent and is
  otherwise offline (`--offline`, no dependencies to fetch); the frozen helper is applied only to the flow's own
  subprocess environments; the guidance wording is conditional on "no project-file change explains it".
- **Residual risk accepted (and why):** the container-image check cannot run in this planner sandbox; CI and the
  Dockerfile-pin assertion cover it.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 7.2
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

Six small steps on one execution branch, no sub-intents: pin the Python bound and regenerate the lock, add a focused
frozen-uv helper and wire it in, add hermetic and lock-check tests, add the CI step, update reviewer and resolver
guidance, then document and run the full suite once.

---

## Step 1: Pin requires-python and regenerate the lock with the image's uv

- **Sub-intent recommendation:** NO
- **Reasoning:** Small, single-file config change; the main risk is uv revision drift, handled by a check.
- **Step Type:** CONFIG
- **Exploration level:** BALANCED

- **Hypothesis being tested:** declaring `requires-python = "==3.13.*"` (matching the root project) and regenerating
  with uv 0.13.7 yields a lock that `uv lock --check` accepts on both the pinned and a newer uv.
- **Learning target:** which lock revision the pinned uv writes, and whether a newer host uv would still accept it.
- **Maximum acceptable cost for this learning:** one regeneration plus one check; stop after two attempts.

### Intent & Git Integration

- **Step Intent:** Make the committed lock agree with the project file and the container's Python and uv.
- **Git branch:** I-1794567519-lockfrz/P-1794567535-986a8099-claude-agent-claude-sonnet-5-5/E-lockfrz-lock
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add `requires-python` to `apps/sandbox-executor/pyproject.toml`, copying the root project's bound.
- Regenerate `apps/sandbox-executor/uv.lock` with the Dockerfile-pinned uv under Python 3.13 (not the host uv).
- Confirm `uv lock --check` passes in that directory and that a second `uv lock` leaves the file byte-identical.
- Verify the root `uv.lock` is still unchanged by `uv lock --check`.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** constraints.md section 1; hermetic offline rule in safety.md.
- **Potential failure modes for this step:** pinned uv unavailable offline; revision differs from the CI uv.
- **Guardrails and early‑abort checks:** run the check offline; compare the lock header before and after.

### Success & Discard Criteria

- **Success:** `uv lock --check` exits 0 and a repeat `uv lock` makes no diff.
- **Discard:** two failed attempts, or cost above 1.5x cost_pred.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.4   |
| impact_pred         | 60.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 52.6  |

### Step Metrics Rationale

`EV = 0.88 * 60.0 + 0.5 * 4.0 - 0.3 * 1.4 - 2.0 = 52.8 + 2.0 - 0.42 - 2.0 = 52.38`. Fixes the root cause. The residual
risk is uv version behaviour, hence the bottleneck flag.

---

## Step 2: Add a frozen-uv helper and wire it into the flow's uv calls

- **Sub-intent recommendation:** NO
- **Reasoning:** One focused new module plus a few call-site edits; low risk and testable offline.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** A stage can never rewrite the lock, and a lock/project disagreement fails with a named message.
- **Git branch:** I-1794567519-lockfrz/P-1794567535-986a8099-claude-agent-claude-sonnet-5-5/E-lockfrz-frozen
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- New module `apps/sandbox-executor/src/sandbox_executor/entrypoint/uv_lock.py` (entrypoints must not import the host
  flow engine) with: a function returning an environment copy with `UV_FROZEN=1`; a function that runs `uv lock --check`
  in a given directory and raises a `LockfileMismatchError` whose message names the lock and project file and says to
  regenerate with the image's uv and commit; a tolerant path when uv is missing, reusing the existing structured
  missing-uv failure in `review_verification.py`.
- Apply the frozen environment in `entrypoint/review_loop.py` `_verify_before_push` (lint and test calls) and in
  `entrypoint/test_measurement.py` (`TEST_COMMAND` run), and in the executor's test run. Run the preflight once before
  the first uv call so the failure is clear instead of a mid-run uv error.
- In `cli/__init__.py` next to the `UV_PROJECT_ENVIRONMENT` pin, forward `UV_FROZEN=1` into the role container
  environment, only for the workspace-mounted case.
- Keep `review_loop.py` growth minimal (it is already 826 lines): the new logic lives in `uv_lock.py`. Respect
  `scripts/check_flow_module_budgets.py` and ruff complexity limits with no `noqa`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** hermetic tests; no new dependencies; module-per-concern placement.
- **Potential failure modes for this step:** frozen env breaks a legitimate dependency change the resolver makes; tests
  that assert exact `env` for run_cmd now differ.
- **Guardrails and early‑abort checks:** the resolver prompt tells it to run an explicit `uv lock` (unfrozen) only when
  `pyproject.toml` changes; update affected existing tests in the same step.

### Success & Discard Criteria

- **Success:** every flow-owned `uv run` receives `UV_FROZEN=1`; a mismatched fixture raises the named error.
- **Discard:** cost above 1.5x cost_pred, or the change forces more than the listed files to be edited.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 2.0   |
| impact_pred         | 65.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 56.7  |

### Step Metrics Rationale

`EV = 0.92 * 65.0 + 0.5 * 3.0 - 0.3 * 2.0 - 4.0 = 59.8 + 1.5 - 0.6 - 4.0 = 56.7`. Touches several call sites, so entropy
is moderate.

---

## Step 3: Hermetic tests and the lock-check test

- **Sub-intent recommendation:** NO
- **Reasoning:** Tests only, following existing fixtures.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the helper and call sites freeze the lock, and that the committed lock needs no rewrite.
- **Git branch:** I-1794567519-lockfrz/P-1794567535-986a8099-claude-agent-claude-sonnet-5-5/E-lockfrz-tests
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- New `apps/sandbox-executor/tests/test_uv_lock.py`: the env helper sets `UV_FROZEN`; the preflight maps a non-zero
  `uv lock --check` (faked runner, no real uv) to `LockfileMismatchError` naming both files; a missing uv is a
  structured failure.
- Extend `test_resolver_lint_parity.py` and `test_test_measurement.py` style tests (existing fakes) to assert the frozen
  env reaches the lint and test commands.
- Add a real check: run `uv lock --check` (offline) against `apps/sandbox-executor` and assert exit 0; skip when uv is
  absent, following the pattern in `test_container_images.py`.
- Add a Dockerfile assertion that the pinned uv version and `python:3.13` base agree with the lock's `requires-python`
  bound (parsed, not hard-coded twice).
- Mark nothing as integration; keep it in `uv run pytest`.

### Dependencies & Criticality

- **Depends on:** Steps 1, 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** hermetic testing guide in `apps/sandbox-executor/docs/hermetic_testing.md`; network guard.
- **Potential failure modes for this step:** real uv invocation blocked by the sandbox network guard.
- **Guardrails and early‑abort checks:** pass `--offline`; skip cleanly if the guard forbids subprocess uv.

### Success & Discard Criteria

- **Success:** new tests pass via `uv run pytest apps/sandbox-executor/tests/test_uv_lock.py`.
- **Discard:** the real-uv test is flaky; downgrade it to the parsed-header assertion.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.2   |
| impact_pred         | 55.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 48.3  |

### Step Metrics Rationale

`EV = 0.92 * 55.0 + 0.5 * 2.0 - 0.3 * 1.2 - 3.0 = 50.6 + 1.0 - 0.36 - 3.0 = 48.24`.

---

## Step 4: Add the CI lock check

- **Sub-intent recommendation:** NO
- **Reasoning:** One workflow step, mirrors an existing one.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** CI fails when the committed lock needs a rewrite.
- **Git branch:** I-1794567519-lockfrz/P-1794567535-986a8099-claude-agent-claude-sonnet-5-5/E-lockfrz-ci
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `.github/workflows/test-hygiene.yml`, which already checks the root lock, add a step running `uv lock --check` and
  `git diff --exit-code uv.lock` in `apps/sandbox-executor`. Use the same setup-uv action and Python 3.13.
- Also extend the root `lint` task description in `docs` only if it changes; leave `pyproject.toml` taskipy as is.
- Extend `test_workflow_triggers.py` or a similar workflow test to assert the step exists.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** workflows README conventions.
- **Potential failure modes for this step:** CI uv differs from the pinned image uv, producing a revision mismatch.
- **Guardrails and early‑abort checks:** pin the CI uv version to the Dockerfile's `UV_VERSION` if the check flaps.

### Success & Discard Criteria

- **Success:** the step is present and its commands pass locally.
- **Discard:** CI uv revision differs and cannot be reconciled without a new dependency.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 0.8   |
| impact_pred         | 40.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 36.4  |

### Step Metrics Rationale

`EV = 0.93 * 40.0 + 0.5 * 1.0 - 0.3 * 0.8 - 1.0 = 37.2 + 0.5 - 0.24 - 1.0 = 36.46`.

---

## Step 5: Reviewer and resolver guidance treats a stray lock change as a defect

- **Sub-intent recommendation:** NO
- **Reasoning:** Prompt text edits with prompt-template tests already in place.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** A lockfile change that no project-file change explains is flagged and reverted.
- **Git branch:** I-1794567519-lockfrz/P-1794567535-986a8099-claude-agent-claude-sonnet-5-5/E-lockfrz-guidance
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add one rule to `holon-config/prompts/review_loop.template.md` (reviewer checklist) and the resolver instructions in
  `entrypoint/review_loop.py` (near lines 359-367): a change to `uv.lock` without a matching `pyproject.toml` dependency
  or `requires-python` change is a defect; revert with `git checkout <base> -- uv.lock`, do not re-run anything
  unfrozen, and never commit an incidental rewrite.
- Mirror a sentence in the executor template and `scaffold.py` conventions.
- Extend `test_prompt_templates.py` to assert the wording is present in each.
- Use Australian English throughout.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** prompt template tests; docs lint (Prettier).
- **Potential failure modes for this step:** wording tells the resolver to revert a legitimate lock update.
- **Guardrails and early‑abort checks:** the clause is conditional on no project-file change explaining the diff.

### Success & Discard Criteria

- **Success:** prompt tests pass and `uv run task lint-docs` is clean.
- **Discard:** the prompt token budget tests fail and the wording cannot be shortened.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.8   |
| impact_pred         | 45.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 41.5  |

### Step Metrics Rationale

`EV = 0.95 * 45.0 + 0.5 * 1.0 - 0.3 * 0.8 - 1.5 = 42.75 + 0.5 - 0.24 - 1.5 = 41.51`.

---

## Step 6: Document the lock policy and run the full suite

- **Sub-intent recommendation:** NO
- **Reasoning:** Documentation plus the single final verification run.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Record the lock policy and verify everything once.
- **Git branch:** I-1794567519-lockfrz/P-1794567535-986a8099-claude-agent-claude-sonnet-5-5/E-lockfrz-docs
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add a short section to `apps/sandbox-executor/docs/hermetic_testing.md` or `docs/testing/testing_guide.md`: how to
  regenerate the lock (pinned uv, Python 3.13), why stages run frozen, and what the preflight error means. Australian
  English; run Prettier format on changed Markdown.
- Run the affected tests first: `uv run pytest apps/sandbox-executor/tests/test_uv_lock.py` and the touched test files.
- Run the full suite once with `uv run task test`, then `uv run task lint` and `uv run task lint-docs`. No repeated runs
  (no timing target in the intent). Confirm `git status` shows no `uv.lock` change after the runs.

### Dependencies & Criticality

- **Depends on:** Steps 1-5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** test-run discipline; ledgers untouched.
- **Potential failure modes for this step:** unrelated pre-existing failures.
- **Guardrails and early‑abort checks:** report failures verbatim; do not weaken tests.

### Success & Discard Criteria

- **Success:** full suite, lint and lint-docs pass and the lock is unchanged after the runs.
- **Discard:** failures traced to earlier steps return to those steps.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 1.0   |
| impact_pred         | 35.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 29.9  |

### Step Metrics Rationale

`EV = 0.93 * 35.0 + 0.5 * 1.0 - 0.3 * 1.0 - 2.5 = 32.55 + 0.5 - 0.3 - 2.5 = 30.25`.

---

## Sub-intent list

None
