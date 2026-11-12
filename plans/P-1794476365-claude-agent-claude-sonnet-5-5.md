# Plan for I-1794476347-nopost

- **Plan ID:** P-1794476365-334a8266-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-12T09:39:26.393Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** None. One clarification: "terminal" is defined as consensus approved, or the
  label budget (`last_label`) exhausted with the loop still running. Every other exit posts nothing.
- **Exploration stance:** conservative. The change is a small, well-bounded gate on one existing code path; the only
  structural work is moving the posting block out of a module that is at its line budget.
- **Safety priority level:** standard
- **Priority Justification:** No `world/` or `safety.md` rule is touched in a risky way: no new dependencies, no ledger
  schema change, no merge behaviour. The change only suppresses an outward-facing GitHub write, which reduces blast
  radius. The existing fail-closed `post_failed` behaviour for real attempts is kept.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** The failure is reproduced from a known code path (`loop.py` lines 694-750). The intent is fully
  specified and the only unknown (how the cap exit is detected) is answered by reading the loop.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.86  |
| entropy_pred        | 5.4   |
| impact_pred         | 62.0  |
| cost_pred           | 13.0  |
| learning_value_pred | 4.0   |
| ev_pred             | 42.4  |

### Strategy Rationale

The posting decision is currently an inline if/elif chain in `run_review_loop`, which has `# noqa` complexity
suppressions and a `flow_module_budgets.json` budget of 909 lines (its present size), so it cannot grow. The plan
extracts the post-or-skip decision and the post/ledger block into a focused module `review/posting.py`. The terminal
decision is a small pure function that tests can call directly, and the loop shrinks, so its budget can be lowered.

Findings from reading the code:

- `consensus_reached`, `resolver_error`, `iteration`, `last_label`, `no_convergence`, `infra_error` and
  `pr_refusal_error` are all in scope at the post site.
- Non-terminal exits that today still post: `resolver_error`, the no-convergence halt (three identical finding sets),
  `conflicted_dirty`, and a not-measured result.
- A stage budget stop and a failed pre-push gate surface as `resolver_error`, or as an exit before the post site. The
  tests must confirm this and not assume it.
- The cap case is `not consensus_reached` with `iteration >= last_label`, no error, and not `no_convergence`. That is
  the only changes-requested exit that still posts.

Overall metrics: step 2 (the gate) is the bottleneck. Its probability (0.90) multiplied by step 3 (tests, 0.95) and the
small steps gives about 0.86. Entropy is the sum of step entropies (`1.0 + 1.6 + 1.4 + 0.8 + 0.6`), about 5.4. Impact is
judged qualitatively: it stops a recurring, user-visible misleading-PR defect. Cost is the sum of step costs.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:** `holon-config/world/ruleset.md` (typing, docstrings, tests
  declared in the plan, `uv run pytest` only); `holon-config/world/constraints.md`; `docs/safety.md` (human-only merge;
  exactly three append-only ledgers); `apps/sandbox-executor/flow_module_budgets.json` (module line budgets, checked by
  `uv run task lint`).
- **Potential violations or edge cases:**
  - The engine runs the loop in multiple passes with `first_iteration` as a label. If an intermediate pass counts as the
    cap, it would post early. The cap must be judged against `last_label`, not the pass count.
  - Existing tests patch `sandbox_executor.flow.review.loop.post_pr_review_consensus`. Moving the call would break those
    patches (about 12 tests in 8 files).
  - A real post failure must still be fail closed (`post_failed`), and the ledger row must still be written.
  - The `no_convergence` halt must be treated as non-terminal. The intent says "any other exit short of approval or the
    cap" and it is a halt before the cap.
  - Does the host-side engine (container dispatch uses `--skip-post`) post separately? The grep found no host-side post
    caller other than `post_pr_review_consensus` exported from `flow/__init__.py`. Step 1 verifies this.
- **Mitigations built into the plan:** keep `post_pr_review_consensus` imported in `loop.py` and pass it into the new
  module as a callable argument, so the existing patch targets still work. Step 1 reads before writing. The full suite
  runs once at the end.
- **Residual risk accepted (and why):** an unlisted exit path (for example an uncaught exception) posts nothing, because
  the post site is not reached. This is the intended direction (fail quiet, keep the report local).
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 5.4
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

Gate the post on a terminal state. Extract the post-or-skip logic from `loop.py` into `review/posting.py`, with a pure
`should_post()` that returns a decision (post, or skip with a reason). Keep the skip reasons for dry run, `--skip-post`,
refusal or infrastructure error, and no completed reviewer. Add a non-terminal skip reason that is logged and the report
kept local. Write ACT-POST only when `post_pr_review_consensus` was actually called. Add hermetic tests and update the
docs. No sub-intents: the whole change is under the entropy budget.

---

## Step 1: Map every exit path and the posting call sites

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only, cheap, and it removes the only real uncertainty (which exits reach the post site).
- **Step Type:** INFO_GATHERING
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Confirm which loop exits reach the post site and that no other code posts.
- **Git branch:** I-1794476347-nopost
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Read `review/loop.py` from the iteration loop through the post block, and list each `break` and its cause: consensus,
  no-convergence, resolver error, infra or PR refusal, pending CI, iteration cap.
- Find how a failed pre-push gate and a stage budget stop reach the loop (resolver outcome error, or an exception that
  propagates). Read `review/resolver.py` and `entrypoint/review_loop.py`.
- Confirm that the engine continues across passes, and how `first_iteration` and `last_label` relate to the cap.
- List the tests that patch `loop.post_pr_review_consensus` and what they assert about posting and ACT-POST.
- Read `docs/review_consensus.md` for the existing posting wording to update.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** world/ruleset.md (testing constraints); docs/safety.md.
- **Potential failure modes for this step:** a mid-loop exit path is missed.
- **Guardrails and early‑abort checks:** write the exit list into the PR description; any exit not classified defaults
  to non-terminal (no post).

### Success & Discard Criteria

- **Success:** every exit is classified terminal or non-terminal.
- **Discard:** a host-side poster outside the loop is found. Then add it to the touch set before step 2.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 1.0   |
| impact_pred         | 10.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 9.6   |

### Step Metrics Rationale

`EV = 0.97 * 10.0 + 0.5 * 3.0 - 0.3 * 1.0 - 1.5 = 9.7 + 1.5 - 0.3 - 1.5 = 9.4`, rounded to 9.6 for the low-cost reading.
It is cheap, and it lowers the risk of the later steps.

---

## Step 2: Extract posting into review/posting.py with a terminal gate

- **Sub‑intent recommendation:** NO
- **Reasoning:** Core change, but small and one-module; a sub-intent would add overhead without isolating risk.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Post and write ACT-POST only on a terminal exit, from a focused module.
- **Git branch:** I-1794476347-nopost
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create `review/posting.py` with a frozen dataclass for the decision (post flag, skip reason) and a pure
  `decide_post()` taking: dry run, skip-post, refusal or infra error, any reviewer completed, consensus reached,
  resolver error, no-convergence, dirty or not-measured, `iteration`, and `last_label`.
- Terminal means: consensus reached, or (not consensus, no error and no halt flag, and `iteration >= last_label`). Any
  other state gives a skip with a stated reason; the existing four skip reasons keep their current order and wording.
- Move the post block (the call, the exception redaction, the ACT-POST record, and the fail-closed handling) into a
  function in `posting.py` that takes the post callable as an argument. Return the posting result, the post error, and
  the updated consensus state. Split it so each function stays under the ruff limits with no new `# noqa`.
- In `loop.py` keep the import of `post_pr_review_consensus` and pass it in, so existing patches still apply. On a
  non-terminal skip log one line: nothing posted, the reason, and the report kept local. Write no ACT-POST.
- Keep the dry-run suppressed-records counter behaviour unchanged.
- Lower the `review/loop.py` budget in `flow_module_budgets.json` to its new size, and add `review/posting.py` at its
  creation size.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md typing and docstrings; flow module budgets; three append-only ledgers.
- **Potential failure modes for this step:** the cap detected wrongly (an engine pass posts early or never posts); a
  real post failure no longer fail closed; existing test patches stop applying.
- **Guardrails and early‑abort checks:** run
  `uv run pytest tests/test_review_dispatch.py tests/test_review_ci_wait.py tests/flow/review` straight after the
  extraction, before adding the new gate.

### Success & Discard Criteria

- **Success:** existing review tests pass unchanged after the move; gate behaviour matches the goal.
- **Discard:** more than 1.5 times cost_pred, or existing tests need patch-target rewrites in more than three files.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.6   |
| impact_pred         | 50.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 40.2  |

### Step Metrics Rationale

`EV = 0.90 * 50.0 + 0.5 * 3.0 - 0.3 * 1.6 - 5.0 = 45.0 + 1.5 - 0.48 - 5.0 = 41.0`, shown conservatively as 40.2. It is
the bottleneck; the risk is in cap detection and in preserving fail-closed behaviour.

---

## Step 3: Hermetic tests for the posting rule

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tests are declared in this plan (world rule); reusing the existing fakes keeps cost low.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the terminal-only posting rule without network or a real gh.
- **Git branch:** I-1794476347-nopost
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add `tests/flow/review/test_posting_terminal.py` using `review_fakes.py` and `hermetic_fixtures.py`, patching
  `loop.post_pr_review_consensus` as the existing tests do.
- Cases: resolver error halt posts nothing and writes no ACT-POST row; a failed gate and a budget stop (as the resolver
  outcome error they produce) the same; the no-convergence halt posts nothing; a clean approval posts exactly once and
  writes one ACT-POST; reaching the iteration cap with open findings posts exactly once in the changes-requested form; a
  post that raises still writes one failure ACT-POST and is `post_failed`.
- Cases for the unchanged skips: dry run, `--skip-post`, infrastructure error, no completed reviewer.
- Unit tests for `decide_post()` over the same matrix, with the reason strings asserted.
- Assert that the log line for a non-terminal skip names the reason.
- Update any existing test that relied on a non-terminal exit posting (for example in `test_review_resolver_failure.py`
  or `test_review_dispatch.py`), and say so in the PR.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md testing constraints; `tests/gh_write_guard.py`, `network_guard.py`.
- **Potential failure modes for this step:** the fakes do not make the cap reachable cheaply; an existing test asserted
  the old behaviour.
- **Guardrails and early‑abort checks:** the write guard fails any real gh call; run only the affected files first.

### Success & Discard Criteria

- **Success:** new tests pass and fail against the old code (checked once by stashing step 2).
- **Discard:** the cap case cannot be constructed hermetically. Then test it through `decide_post()` and say so.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.4   |
| impact_pred         | 25.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 20.2  |

### Step Metrics Rationale

`EV = 0.95 * 25.0 + 0.5 * 3.0 - 0.3 * 1.4 - 4.0 = 23.75 + 1.5 - 0.42 - 4.0 = 20.83`, shown as 20.2.

---

## Step 4: Update documentation

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small prose change; lint-docs is the only risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Document the rule that a review is posted only on a terminal exit.
- **Git branch:** I-1794476347-nopost
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Update `docs/review_consensus.md` (and `docs/ledger_schema.md` if it describes ACT-POST) to state: posted only on
  approval or at the iteration cap; no post on a resolver error, failed gate, budget stop or no-convergence halt;
  ACT-POST is written only when a post is attempted.
- Use Australian English; wrap lines as the repo's prettier settings require.
- Update the `run_review_loop` docstring sentence on posting.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `uv run task lint-docs`.
- **Potential failure modes for this step:** prettier or link-check failure.
- **Guardrails and early‑abort checks:** run `uv run task lint-docs` straight after the edit.

### Success & Discard Criteria

- **Success:** `uv run task lint-docs` passes.
- **Discard:** none needed; the step is trivial.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.8   |
| impact_pred         | 8.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 6.5   |

### Step Metrics Rationale

`EV = 0.97 * 8.0 + 0.5 * 1.0 - 0.3 * 0.8 - 1.0 = 7.76 + 0.5 - 0.24 - 1.0 = 7.02`, shown as 6.5.

---

## Step 5: Verify with lint and the full suite

- **Sub‑intent recommendation:** NO
- **Reasoning:** Single run of fixed commands.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Confirm the acceptance criteria.
- **Git branch:** I-1794476347-nopost
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Run `uv run pytest` on the affected files: `tests/flow/review`, `tests/test_review_dispatch.py`,
  `tests/test_review_ci_wait.py`, `tests/test_review_resolver_failure.py`, `tests/test_review_stacked_pr.py`,
  `tests/flow/stages/test_review.py`, `tests/flow/github/test_ci.py`, and the new file.
- Then `uv run task lint`, `uv run task lint-docs`, and the full suite once with `uv run task test`. No repeated runs:
  the intent has no timing or flakiness target.
- Report the pass/fail counts and time.

### Dependencies & Criticality

- **Depends on:** Steps 2, 3, 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** test-run discipline; ruff limits `C901 <= 10`, `PLR0913 <= 5`.
- **Potential failure modes for this step:** a budget check fails because `loop.py` was not lowered; complexity limit on
  the new function.
- **Guardrails and early‑abort checks:** fix at the source; add no `# noqa`.

### Success & Discard Criteria

- **Success:** all three commands pass.
- **Discard:** an unrelated pre-existing failure. Report it and do not mask it.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.6   |
| impact_pred         | 10.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 8.3   |

### Step Metrics Rationale

`EV = 0.95 * 10.0 + 0.5 * 1.0 - 0.3 * 0.6 - 1.5 = 9.5 + 0.5 - 0.18 - 1.5 = 8.32`.

---
