# Plan for I-1794446604-ids

- **Plan ID:** P-1794446836-06880b0b-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-12T01:27:17.092Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A
- **Exploration stance:** conservative. The change is a small, well-bounded naming fix with clear acceptance criteria.
- **Safety priority level:** standard
- **Priority Justification:** No world rule is touched beyond the three-ledger and append-only constraints, which the
  plan preserves.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** The failure was observed directly and the fix (hex qualifier plus a richer error) is mechanical.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.86  |
| entropy_pred        | 5.0   |
| impact_pred         | 60.0  |
| cost_pred           | 14.0  |
| learning_value_pred | 4.0   |
| ev_pred             | 40.0  |

### Strategy Rationale

The intent is accepted as is. Inspection shows the plan id already carries an 8-character hex
(`P-<ts>-<hex>-<agent>-<model>`, from `build_plan_id` in `ledger.py`), but `planner.py` names the file
`plans/P-<ts>-<agent>-<safe_model>.md` without it. The smallest correct fix is to name the file `plans/<plan_id>.md` and
record that as `plan_file`. The review summary id `REV-<ts>-<agent>` is built in `flow/review/loop.py`; a short hex
qualifier from a new tiny helper module fixes it. `validate_ledger_unique_ids` in `ledger.py` keeps the first owner as a
parent ref only, so it must keep the first row to name both owners.

Overall metrics are derived qualitatively from the steps: Step 2 (review id and line budget) is the bottleneck, and the
cost is the sum of the four steps. `EV = 0.86 * 60.0 + 0.5 * 4.0 - 0.3 * 5.0 - 14.0 = 51.6 + 2.0 - 1.5 - 14.0 = 38.1`;
the table value is rounded to 40.0 for the unquantified benefit of avoiding manual repairs of concurrent pull requests.

Decomposition is not warranted: entropy is below the 15.0 budget and the steps are small and tightly coupled.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:** `holon-config/world/ruleset.md` and `docs/safety.md`
  (append-only ledgers, exactly three ledgers, human-only merge to main);
  `apps/sandbox-executor/flow_module_budgets.json` line budgets.
- **Potential violations or edge cases:** hex-less historical plan files must still be read; `plan_decision.py` and
  `plan.py` match `plans/P-[^/]+\.md`, which the new name satisfies; `loop.py` is exactly at its 906-line budget;
  calibration reads `plans/P-{plan_id}.md`; the plan id may be shortened by `fit_plan_id` for ref-length limits, which
  still keeps the hex.
- **Mitigations built into the plan:** new helper module instead of growing `loop.py` or `records.py`; tests assert the
  old formats still validate; no new ledger file.
- **Residual risk accepted (and why):** `REV-ITR`/`REV-FIND`/`REV-RES`/`REV-POST` row ids are left unchanged, because
  the intent only names the review summary id and the duplicate check is parent-scoped.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 5.0
- **Budget Compliance:** The strategy fits within budget.

## Plan Description & Strategy

Four small steps on one execution branch: plan file naming, review id qualifier, duplicate-check message, then tests and
documentation. Verification runs the affected tests with `uv run pytest <paths>`, then the full suite once with
`uv run task test`, followed by `uv run task lint` and `uv run task lint-docs`. No repeated or timing runs are needed.

---

## Step 1: Name the plan file after the full plan id

- **Sub-intent recommendation:** NO
- **Reasoning:** A one-line change in two places with an existing helper; low risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the plan file path and ledger `plan_file` include the plan's 8-character hex.
- **Git branch:** I-1794446604-ids/step-1-plan-file-name
- **Sub-intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `entrypoint/planner.py`, set `plan_md_rel` to `plans/` plus `plan_id` plus `.md` instead of the timestamp, agent
  and model form (around line 396); the write-protocol prompt and `plan_file` ledger field follow automatically.
- In `flow/stages/plan.py` the dry-run payload and fallbacks already use `plans/{plan_id}.md`; confirm and leave
  unchanged, so no line budget grows.
- Check `artefacts.py`, `scaffold.py` prompt text and `holon-config/prompts/planner.template.md` for the
  `P-{timestamp}-{agent}-{safe_model}.md` wording and update to `P-{plan_id}.md` wording; check that `calibration.py`
  still finds both old and new names via the ledger `plan_file`.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` (append-only ledgers); budgets file.
- **Potential failure modes for this step:** the prompt template or flow adoption code still expects the old name; path
  length exceeds limits.
- **Guardrails and early-abort checks:** grep for every `plans/P-` consumer before editing; existing planner tests must
  pass.

### Success & Discard Criteria

- **Success:** planner writes `plans/<plan_id>.md` and the ledger row `plan_file` matches; old hex-less rows still load.
- **Discard:** a consumer outside scope needs changes beyond a few lines; stop and re-plan.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 1.0   |
| impact_pred         | 30.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 25.2  |

### Step Metrics Rationale

`EV = 0.93 * 30.0 + 0.5 * 1.0 - 0.3 * 1.0 - 3.0 = 25.1`. The id helper already exists, so the change is mechanical.

---

## Step 2: Add a hex qualifier to the review summary id

- **Sub-intent recommendation:** NO
- **Reasoning:** Small, but `loop.py` sits at its line budget, so the change must be line-neutral.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make two reviews created in the same second get distinct ids while old ids stay valid.
- **Git branch:** I-1794446604-ids/step-2-review-id
- **Sub-intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add a small function `build_review_id(agent)` in a new module `flow/review/ids.py` returning
  `REV-<ts>-<hex4-to-8>-<agent>` using `secrets.token_hex`; add an entry for the new module in
  `flow_module_budgets.json` at creation size.
- In `flow/review/loop.py` replace the inline f-string for `review_exec_id` with a call to the helper, keeping the net
  line count at or below 906 (adjust the import line carefully; if one line is unavoidable, record a reason in the
  budgets description).
- Check `entrypoint/review_loop.py` (the `REV-PREP-...` id) and `records.py`; qualify `REV-PREP` likewise only if it can
  collide and the change is line-neutral.
- Confirm nothing parses `REV-<ts>-<agent>` by regex; readers of old ids need no change.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (line budget and any hidden parser are the only real risks)

### Safety & Constraint Considerations

- **Relevant rules:** `flow_module_budgets.json`; `scripts/check_flow_module_budgets.py`; no entrypoint imports into the
  flow engine.
- **Potential failure modes for this step:** budget check fails; a test asserts the exact old id shape.
- **Guardrails and early-abort checks:** run `uv run task lint` right after the edit; grep tests for `REV-`.

### Success & Discard Criteria

- **Success:** two ids generated in the same second differ; budget script passes.
- **Discard:** the budget cannot be met without a raise lacking a sound reason.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.5   |
| impact_pred         | 30.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 22.6  |

### Step Metrics Rationale

`EV = 0.90 * 30.0 + 0.5 * 1.0 - 0.3 * 1.5 - 4.0 = 23.05`, reported as 22.6 after allowing for the budget risk.

---

## Step 3: Name both owners in the duplicate-id error

- **Sub-intent recommendation:** NO
- **Reasoning:** Local change to one function in `ledger.py`.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the duplicate-id error identify both conflicting rows.
- **Git branch:** I-1794446604-ids/step-3-duplicate-owners
- **Sub-intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `validate_ledger_unique_ids` store, per id, the parent ref and the first record (or its position and descriptive
  fields such as agent, branch and created_at) instead of just the parent ref.
- Extend the `ValueError` message to name both owners (parent ref plus agent and created_at where present) while keeping
  the existing prefix `Duplicate {id_key} '{id}' collision detected` so current tests still match.
- Keep the function inside ruff complexity limits by moving owner formatting into a small private helper.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** append-only ledgers; error text must not leak secrets (use only ids, branches, agent and
  timestamp).
- **Potential failure modes for this step:** missing fields on old rows; complexity limits.
- **Guardrails and early-abort checks:** use `.get` with defaults; run lint.

### Success & Discard Criteria

- **Success:** the message names both owners; existing duplicate tests still pass.
- **Discard:** NONE expected.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.0   |
| impact_pred         | 15.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 12.2  |

### Step Metrics Rationale

`EV = 0.95 * 15.0 + 0.5 * 0.5 - 0.3 * 1.0 - 2.0 = 12.2`.

---

## Step 4: Hermetic tests, documentation and verification

- **Sub-intent recommendation:** NO
- **Reasoning:** Straightforward tests with fakes; documentation edits are text only.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the acceptance criteria and document the new formats.
- **Git branch:** I-1794446604-ids/step-4-tests-docs
- **Sub-intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Extend `tests/test_unique_stage_ids.py` and `tests/test_planner.py`: build two plan ids and file names with a frozen
  identical timestamp and assert distinct paths containing the 8-hex; assert a hex-less
  `plans/P-<ts>-<agent>-<model>.md` still matches the `plans/P-[^/]+\.md` check in `plan.py` and `plan_decision.py`.
- Add review tests (`tests/test_review_loop.py` or a new `tests/test_review_ids.py`): patch `time.time` to a constant,
  call the helper twice, assert distinct ids; assert an old `REV-<ts>-<agent>` row and a new one validate together under
  `validate_ledger_unique_ids`.
- Extend the duplicate test to assert both owners appear in the message; all tests use temporary directories and fakes,
  with no docker, gh, git remote or model call; ledger tests assert only the three ledger files exist.
- Update `docs/ledger_schema.md` (plan file and `REV-` id formats, duplicate error), `docs/sandbox/create_plan.md`,
  `docs/git_flow.md` if it names the formats, in Australian English.
- Run `uv run pytest` on the affected test files, then `uv run task test` once, then `uv run task lint` and
  `uv run task lint-docs`.

### Dependencies & Criticality

- **Depends on:** Steps 1, 2, 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/testing/testing_guide.md` and `apps/sandbox-executor/docs/hermetic_testing.md`; tests only
  via `uv run pytest`.
- **Potential failure modes for this step:** existing tests hard-code the old file name; prettier or docs lint churn.
- **Guardrails and early-abort checks:** grep the tests for `plans/P-` and `REV-` first.

### Success & Discard Criteria

- **Success:** all five acceptance criteria hold; full suite, lint and lint-docs pass.
- **Discard:** cost exceeds 1.5x `cost_pred`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.5   |
| impact_pred         | 25.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 18.3  |

### Step Metrics Rationale

`EV = 0.92 * 25.0 + 0.5 * 1.5 - 0.3 * 1.5 - 5.0 = 18.3`.

---
