# Plan for I-1795130255-rescrash

- **Plan ID:** P-1795130272-f4c1d312-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-19T23:17:53.355Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A. Two small additions stay inside the intent's goals: (1) the resolver
  entrypoint also treats a clean exit with no dispositions file as a failed turn ("no usable output" in goal 1), and (2)
  `tests/test_resolver_evidence.py`, `flow/context.py`, `flow_module_budgets.json`, `docs/review_consensus.md` and
  `docs/ledger_schema.md` join the touch set because goals 2 and 3 cannot be met or documented without them.
- **Exploration stance:** conservative. The root cause is known from the code (see Plan Description). The work is fixing
  a fail-open path in an append-only data flow, so exploit-mode steps behind hermetic tests are right. The only open
  design question (where carried findings live in the checkpoint) is settled in Step 1 with one cheap check.
- **Safety priority level:** elevated
- **Priority Justification:** `holon-config/world/constraints.md` §3 (Ledger Immutability) and `docs/safety.md`
  Invariant 6 (Immutability): Step 6 writes to `holon-knowledge/ledger/executions.jsonl`, and the bug itself writes
  false rows to it. A wrong correction cannot be undone, only corrected again. The review loop is also the human-review
  gate's evidence source (`docs/safety.md` "Human review is the final gate"), so a fail-open loop weakens a safety
  boundary.

## Exploration

- **Proportion of steps that are exploratory:** 0.125 (1 of 8; Step 1 is INFO_GATHERING at BALANCED)
- **Justification:** The failure path has a clear cause in the code. The one unknown is whether any ledger reader or
  test relies on the old "unadjudicated nit = rejected" default. A short grep and baseline run (Step 1) answers that
  before any edit. More exploration would add cost without much learning.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.68  |
| entropy_pred        | 10.5  |
| impact_pred         | 88.0  |
| cost_pred           | 33.5  |
| learning_value_pred | 6.0   |
| ev_pred             | 26.19 |

### Strategy Rationale

**Root cause (read from the code, not guessed):**

1. `resolver_evidence.default_disposition` makes a finding the resolver did not adjudicate `rejected` when its severity
   is `nit`. `review_report._normalise_rejection_reason` then turns the missing reason into `unspecified`.
   `settle_rejection` lets a nit stand as rejected with any reason, `unspecified` included.
2. In `flow/review/loop.py`, when `resolver_outcome.error` is set, the loop breaks before adjudication (correct). But
   the post-loop block (`if resolver_enabled and not effective_dry_run:` around lines 638–651) then builds
   `fallback_dispositions` for every unrecorded finding with `deserialize_finding_dispositions([], ...)`. For the two PR
   #203 nits that produced `rejected / unspecified / outcome success`, which is exactly ACT-RES-1794920909-f4cc16ac and
   ACT-RES-1794920909-d214a869.
3. Nothing about the halted iteration's findings is checkpointed. `FlowContext` carries only `review_iterations_done`.
   So `--from-stage review` starts a fresh ensemble iteration, and a clean ensemble on the unchanged head reaches
   `ConsensusVerdict.APPROVED`.
4. The container entrypoint (`entrypoint/review_loop.py::_run_resolver_agent`) returns `[]` when the agent exits 0
   without writing a dispositions file. So "no usable output" also falls into the default-rejection path.

**Strategy:** close each fail-open path at the module that owns the data:

- the rejection-reason rule in `resolver_evidence.py`;
- the ACT-RES write guard in `records.py`;
- the failed-turn rule in the loop, the resolver seam and the container entrypoint;
- carried findings in a new focused module `flow/review/carried_findings.py`, plus one checkpointed `FlowContext` field;
- the two PR #203 nits in `calibrate_rerun.py`;
- the ledger repair as two appended correction rows.

`loop.py` (913 lines), `resolver.py` (307), `records.py` (316), `context.py` (272) and `calibrate_rerun.py` (284) sit
exactly at their `flow_module_budgets.json` budgets. So new logic goes into the new module. The post-loop fallback block
moves out of `loop.py` to offset the new call sites. Any remaining growth is a stated budget raise with a description
sentence, as earlier intents did.

**Aggregation:**

- `p_success` is driven by the bottleneck Step 4 (0.72). Steps 2, 3 and 4 share the same loop and the same tests, so
  their failures are correlated. The plan-level estimate is a judgement of 0.68, not the independent product (about
  0.33), which would understate a correlated chain.
- Entropy is the sum of step entropies: `0.4 + 1.6 + 2.0 + 3.6 + 0.9 + 0.8 + 0.5 + 0.7 = 10.5`.
- Cost is the sum of step costs: `1.5 + 5 + 6 + 10 + 3 + 2 + 2 + 4 = 33.5`.
- Impact is 88. The fix restores the review loop's core guarantee (every finding is applied or rejected with a reason)
  and the trustworthiness of the calibration data. That is close to the maximum for a non-security fix.
- Learning value is the max over steps (6, Step 4: the first checkpointed cross-run review state).
- `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S - Cost`, with `mu = 0.5` and `lambda = 0.3` from
  `holon-config/metrics/ev_config.json`:
  `EV = 0.68 * 88.0 + 0.5 * 6.0 - 0.3 * 10.5 - 33.5 = 59.84 + 3.00 - 3.15 - 33.5 = 26.19`.

**Strong assumptions:**

- Loop-supplied `no-convergence` rejections stay allowed. They carry a named reason from the closed vocabulary and are
  recorded with `outcome: failure`. Goal 2 targets an empty or `unspecified` reason, which this plan refuses everywhere.
- The rejected-attempt row (`rejected_attempt_record.py`, written only when a resolver patch failed pre-push tests)
  carries no `review_disposition`. It is a record of a run that happened, not an adjudication, so it is kept. A turn
  that crashes or hits a usage limit keeps no rejected attempt, so it writes no ACT-RES row at all.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §3 Ledger Immutability: append-only. No historical line is edited, removed or
    re-serialised.
  - `holon-config/world/constraints.md` §1 Git Flow: work stays on the execution branch under
    `I-1795130255-rescrash/P-1795130272-f4c1d312-claude-agent-claude-opus-5-5/`. Nothing is committed to `main`.
  - `holon-config/world/ruleset.md` §2: typed public functions with docstrings, no wildcard imports.
  - `holon-config/world/ruleset.md` §3: test changes must be declared in the plan. The changed existing assertions in
    `tests/test_resolver_evidence.py` (the `test_an_unknown_reason_is_unspecified_never_out_of_scope` expectations) and
    any `tests/test_review_loop.py` expectations that relied on the old nit default are declared in Steps 2 and 3.
  - `docs/safety.md` Invariant 6 (Immutability) and "Human review is the final gate".
  - Repository limits: ruff `C901 <= 10`, `PLR0912 <= 12`, `PLR0913 <= 5`, `PLR0915 <= 50`, no new inline `# noqa`;
    `scripts/check_flow_module_budgets.py`; no new entrypoint imports into the host flow engine.
- **Potential violations or edge cases:**
  - A correction row written with the wrong shape (or by rewriting the file) would itself break §3.
  - `run_review_loop` already has a long body. Adding the carried-findings pass inline could exceed `PLR0915` or `C901`.
  - Older checkpoints have no `open_review_findings` key.
  - A resolver that keeps leaving a carried finding open could loop until the iteration budget is spent.
  - Changing the nit default could flip existing tests that encoded the fail-open behaviour.
- **Mitigations built into the plan:**
  - Step 6 appends through the ledger's own locked append helper and checks the result with `assert_ledger_prefix` (or
    an equivalent byte-prefix comparison against `git show HEAD:...`) before commit.
  - All carried-findings logic lives in `carried_findings.py`. `loop.py` gets only short call sites, and the moved
    post-loop fallback block offsets them.
  - `FlowContext.from_dict` defaults the new field to empty, so no schema version bump is needed and old checkpoints
    still load.
  - The existing three-iteration no-convergence guard and the iteration budget bound repeated open findings.
  - The test expectations that encoded fail-open behaviour are declared and changed on purpose.
- **Residual risk accepted (and why):** Ledger readers that do not know `event_type: ledger_correction` will still count
  the two voided rows. A reader-side void filter is out of scope: no current reader aggregates `review_disposition`, and
  the correction rows make the void auditable. A follow-up intent can add the filter if a reader starts counting
  dispositions.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 10.5
- **Budget Compliance:** The strategy fits within budget (10.5 ≤ 15.0). It runs as one plan with no sub-intent list.

## Plan Description & Strategy

Make a crashed resolver turn impossible to record as a decision, require a stated reason for every rejection, carry open
findings across a halted iteration and a resume, repair the two false ledger rows by appending void corrections, and
dispose of the two PR #203 nits. Order:

1. Baseline and dependency check.
2. Rejection-reason rule plus write guard.
3. Failed resolver turn writes nothing.
4. Carried findings through checkpoint and resume, with the consensus gate.
5. The calibrate_rerun nits.
6. Ledger correction rows.
7. Docs.
8. One full verification pass.

Steps 2–4 share the review loop and are executed in order on one branch. They are too coupled to split across
sub-intents cheaply: each later step's tests assume the earlier rule.

## Touch Set

- `apps/sandbox-executor/src/sandbox_executor/resolver_evidence.py`
- `apps/sandbox-executor/src/sandbox_executor/review_report.py` symbols: \_disposition_from_mapping,
  \_normalise_rejection_reason
- `apps/sandbox-executor/src/sandbox_executor/flow/review/records.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/review/resolver.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/review/loop.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/review/adjudication.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/review/carried_findings.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/context.py`
- `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/calibrate_rerun.py`
- `apps/sandbox-executor/flow_module_budgets.json`
- `apps/sandbox-executor/tests/test_review_resolver_failure.py`
- `apps/sandbox-executor/tests/test_review_loop.py`
- `apps/sandbox-executor/tests/test_resolver_evidence.py`
- `apps/sandbox-executor/tests/test_calibrate_rerun.py`
- `docs/review_consensus.md`
- `docs/ledger_schema.md`

(`holon-knowledge/ledger/executions.jsonl` is also changed, in Step 6, append-only. It is left out of the list as the
template requires.)

---

## Step 1: Baseline the affected tests and map every dependency on the old nit default

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only, cheap, and it decides two small design points. It has no branch-worthy output.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Only `review_report._disposition_from_mapping`, the loop's post-loop fallback and a
  handful of tests depend on "an unadjudicated nit defaults to rejected". No ledger reader validates historical ACT-RES
  rows in a way the new write guard would break.
- **Learning target:** The exact list of tests encoding the fail-open default. Whether `ActionRecord.validate` runs on
  read paths (`ledger_query`, knowledge ingestion). Where resolver-input `FindingRow`s are parsed today (to reuse it for
  checkpoint deserialisation).
- **Maximum acceptable cost for this learning:** 1.5 cost units (about 15 minutes, one targeted test run).

### Intent & Git Integration

- **Step Intent:** Establish a green baseline and a precise dependency map before editing.
- **Git branch:** `I-1795130255-rescrash/P-1795130272-f4c1d312-claude-agent-claude-opus-5-5/_` (execution branch per
  constraints §1; no commit in this step)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run
   `uv run pytest apps/sandbox-executor/tests/test_review_resolver_failure.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_resolver_evidence.py apps/sandbox-executor/tests/test_calibrate_rerun.py apps/sandbox-executor/tests/test_review_report.py`
   once and record the pass count.
2. Grep `src/` and `tests/` for `default_disposition`, `UNSPECIFIED_REASON`, `"unspecified"`,
   `deserialize_finding_dispositions(` with an empty first argument, and `ActionRecord.from_dict`/`.validate(` call
   sites. List the tests asserting a nit defaults to `rejected` or `unspecified`.
3. Find the existing parser that turns a serialised resolver finding back into a `FindingRow` (used by
   `entrypoint/review_loop.py::_load_resolver_findings`). If it lives in `review_report.py`, Step 4 reuses it from the
   host side. If it lives only in the entrypoint, Step 4 adds a small `deserialize_finding_row` to `review_report.py`
   rather than importing from the entrypoint (rule: no entrypoint imports into the flow engine).
4. Run `python scripts/check_flow_module_budgets.py` to confirm the current headroom (expected: zero for `loop.py`,
   `resolver.py`, `records.py`, `context.py`, `calibrate_rerun.py`).

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (test changes must be declared); `docs/safety.md` Invariant 1.
- **Potential failure modes for this step:** Baseline already red, which would hide regressions.
- **Guardrails and early‑abort checks:** If the baseline is red for reasons unrelated to this intent, record the failing
  test IDs in the execution report and continue only if none of them touch the review loop or calibrate_rerun.

### Success & Discard Criteria

- **Success:** Baseline pass count recorded. The dependency list and the FindingRow parser location are written into the
  execution report.
- **Discard:** Cost exceeds 2x `cost_pred`, or the baseline shows review-loop failures unrelated to this intent. In that
  case stop and report, because the plan's assumptions would be wrong.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.4   |
| impact_pred         | 10.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 9.58  |

### Step Metrics Rationale

Read-only, so it almost always succeeds and adds little entropy. Its impact is enabling only. Its learning value is real
because it decides whether the write guard can live in `ActionRecord.validate` or must stay in `records.py`.
`EV = 0.97 * 10.0 + 0.5 * 3.0 - 0.3 * 0.4 - 1.5 = 9.70 + 1.50 - 0.12 - 1.50 = 9.58`.

---

## Step 2: Require a resolver-supplied reason for every rejection and refuse unreasoned ACT-RES rejections

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, local rule change in two owned modules, with direct unit tests. Splitting it out would add a
  merge round-trip for little isolation.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Goal 2. A rejection stands only with a reason the resolver supplied from the vocabulary. An empty or
  `unspecified` reason is refused at settlement and again at the ledger write.
- **Git branch:** `I-1795130255-rescrash/P-1795130272-f4c1d312-claude-agent-claude-opus-5-5/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `resolver_evidence.py`:
   - `default_disposition(severity)` returns `OPEN` for every severity. A finding the resolver did not adjudicate is
     never a rejection. Update the module and function docstrings.
   - Add `has_stated_reason(reason: str | None) -> bool`: true only for a non-empty reason other than
     `UNSPECIFIED_REASON`.
   - In `settle_rejection`, check first: when `has_stated_reason(reason)` is false, return `OPEN, None` with the audit
     text `rejection without a stated reason` (plus any resolver evidence), for every severity. The existing evidence
     rule for important/critical stays as is.
2. `review_report.py`: leave `_normalise_rejection_reason` mapping unknown reasons to `unspecified`. Settlement now
   turns that into `open`, so the only change is the docstring of `_disposition_from_mapping` and of
   `_normalise_rejection_reason` noting the outcome. No behavioural change beyond what `resolver_evidence` decides.
3. `flow/review/records.py`: in `record_review_action`, before the append, refuse any record whose `review_disposition`
   is `rejected` and whose `rejection_reason` fails `has_stated_reason`. Log a WARNING mirrored to `context.log` naming
   the action id, write nothing, return None. This is the last line of defence for any caller, present or future. Keep
   the check in a small private helper so `record_review_action` stays under complexity limits. If Step 1 shows
   `ActionRecord.validate` is never run on read paths, also add the same check to `ActionRecord.validate` in
   `ledger.py`. Otherwise keep it in `records.py` only, so historical rows stay readable.
4. Raise the `review/records.py` budget in `flow_module_budgets.json` by the exact added line count, and append a
   description sentence naming this intent's reason.
5. Tests:
   - New, in `tests/test_review_resolver_failure.py`: `settle_rejection("nit", "unspecified", None)` and
     `settle_rejection("nit", "", None)` return `open`; a nit with a vocabulary reason still returns `rejected`.
     `record_review_action` given a rejected row with reason `unspecified`, `""` or None appends nothing to a tmp
     `executions.jsonl` and returns None.
   - Declared test change: update
     `tests/test_resolver_evidence.py::test_an_unknown_reason_is_unspecified_never_out_of_scope`. The disposition
     becomes `open` with no rejection reason. Rename it so the name states the new rule. Update any other assertion Step
     1 listed that encodes "nit defaults to rejected".

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 and §3; `docs/safety.md` "Human review is the final gate".
- **Potential failure modes for this step:**
  - Other tests silently relied on the nit default.
  - The guard placed in `ActionRecord.validate` breaks a reader of historical rows.
- **Guardrails and early‑abort checks:**
  - Run the five affected test files after the change.
  - Any newly failing test outside the Step 1 list is investigated before its expectation is changed, and declared in
    the execution report.

### Success & Discard Criteria

- **Success:** New tests pass. Updated declared tests pass. No ACT-RES rejected row with an empty or `unspecified`
  reason can be written.
- **Discard:** More than 10 unrelated tests flip, which would mean the default is load-bearing elsewhere. In that case
  stop and re-scope the default change to the loop's fallback path only.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.6   |
| impact_pred         | 70.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 57.62 |

### Step Metrics Rationale

The logic is small, but it changes a default that several tests may encode, so `p` is 0.88 rather than about 0.95. High
impact: it removes the `unspecified` rejection everywhere at once.
`EV = 0.88 * 70.0 + 0.5 * 3.0 - 0.3 * 1.6 - 5.0 = 61.60 + 1.50 - 0.48 - 5.00 = 57.62`.

---

## Step 3: Make a failed resolver turn write no adjudication rows and keep its findings open

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is the direct fix for the observed incident. It shares the loop and its tests with Steps 2 and 4,
  and it is the precondition for Step 4's carried findings.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Goal 1. A non-zero exit, a usage limit, a timeout or no usable output is a failed turn. The loop
  writes no ACT-RES adjudication row for its findings, and they stay open.
- **Git branch:** `I-1795130255-rescrash/P-1795130272-f4c1d312-claude-agent-claude-opus-5-5/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `flow/review/carried_findings.py` (new focused module, module docstring, typed public functions). It first
   holds `record_unresolved_findings(recorder, iteration, remaining_rows, open_dispositions, finding_action_ids)`: the
   post-loop fallback block moved verbatim out of `loop.py` (lines around 638–651). With Step 2 the fallback is now
   `open` for every severity, so these rows read "left open", never "adjudicated: rejected".
2. `flow/review/loop.py`:
   - Replace the post-loop block with a call to `record_unresolved_findings`. Skip the call entirely when
     `resolver_error` is set: a failed turn's findings get no ACT-RES row at all (they are carried by Step 4).
     `pending_dispositions` that an earlier, successful pass produced in this run are still recorded, since those were
     real decisions.
   - Update the comment at the halt site ("Open findings are recorded once after the loop") to state the new rule.
3. `flow/review/resolver.py::_invoke_resolver_pass`:
   - The fall-through branch (a raw result that is neither `ResolverOutcome`, `Mapping` nor `Sequence`) returns an
     outcome with `error="Resolver returned no usable output"`.
   - A `Mapping` with findings but no `dispositions` list, or an empty `Sequence` while findings were sent, also returns
     that error.
   - Keep the edits inside the existing function or a tiny private helper so `C901`/`PLR0912` hold.
   - Raise the `review/resolver.py` budget by the exact delta with a description sentence.
4. `entrypoint/review_loop.py::_run_resolver_agent`: when findings were sent and the agent exits 0 but writes no
   dispositions file (or an empty or unparseable one), raise
   `ResolverTurnError("Resolver agent produced no dispositions", output tail)`. A usage-limit message that exits 0
   therefore becomes a failed turn with `exit_code: 1`, and the existing `read_container_resolver_result` path already
   fails closed on it. This keeps the container and host rules identical.
5. Tests in `tests/test_review_resolver_failure.py` (hermetic, `run_ensemble_fn`/`run_resolver_fn` seams from
   `tests/review_fakes.py`):
   - (a) A resolver seam returning `{"exit_code": 1, "error": "You've hit your session limit", "output_tail": ...}` for
     two nit findings: assert no row in the tmp `executions.jsonl` has an `ACT-RES` id with a `review_disposition`, no
     row says `adjudicated`, and the loop result reports `resolver_error`.
   - (b) The same with a seam raising an exception (timeout stand-in).
   - (c) The same with a seam returning `object()`/None (no usable output).
   - (d) An entrypoint-level test of `_run_resolver_agent` with a fake runner that exits 0 and writes nothing raises
     `ResolverTurnError`.
   - Declared test change: any `tests/test_review_loop.py` test from the Step 1 list that asserted fallback `rejected`
     rows after a resolver error is updated to assert no ACT-RES adjudication row.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES (goal 1 is the core of the intent; p_success 0.85 ≥ 0.6, so no sub-intent is required)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §3; repository rule "no new imports from entrypoints into the
  host flow engine" (the new module imports only flow and review_report names).
- **Potential failure modes for this step:**
  - Moving the fallback block changes the order of rows on the normal path.
  - `run_review_loop` exceeds `PLR0915`.
  - Treating "exit 0 without dispositions" as failure breaks a dry-run path where no agent runs.
- **Guardrails and early‑abort checks:**
  - Keep the moved block's behaviour identical on the success path; existing order-sensitive tests in
    `test_review_loop.py` must pass unchanged.
  - Leave the dry-run branch (`findings and not args.dry_run`) untouched.
  - Run ruff after the edit.

### Success & Discard Criteria

- **Success:** Tests (a)–(d) pass. Existing success-path review-loop tests pass unchanged. `loop.py` net line count does
  not grow from this step.
- **Discard:** The move forces changes to more than 5 unrelated loop tests. In that case revert the move and keep a
  minimal in-place guard (`if not resolver_error`) instead.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 2.0   |
| impact_pred         | 85.0  |
| cost_pred           | 6.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 67.15 |

### Step Metrics Rationale

This step touches three modules on both sides of the container boundary, hence entropy 2.0. The fix is well understood,
so `p` is 0.85. It directly prevents the false ACT-RES rows: highest impact of all steps.
`EV = 0.85 * 85.0 + 0.5 * 3.0 - 0.3 * 2.0 - 6.0 = 72.25 + 1.50 - 0.60 - 6.00 = 67.15`.

---

## Step 4: Carry open findings through the checkpoint, resolve them first on resume, and block approval while any lacks a disposition

- **Sub‑intent recommendation:** NO (the step-level recommendation would be YES on risk alone, but it is too coupled to
  Step 3's state to merge separately, and the plan is within the entropy budget)
- **Reasoning:** This is the riskiest step: new checkpointed state and a new pass ordering in the review loop. It is
  also the most reusable result (cross-run review state). It stays in this plan because its tests need Steps 2–3.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Goal 3. A halted iteration's findings survive in the checkpoint as open. A resumed review stage sends
  them to the resolver before any new ensemble iteration. Consensus approval is unreachable while an earlier-iteration
  finding has no disposition.
- **Git branch:** `I-1795130255-rescrash/P-1795130272-f4c1d312-claude-agent-claude-opus-5-5/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `flow/context.py`: add `open_review_findings: list[dict[str, Any]]` (default empty). Comment it: "Stage 4 findings
   without a disposition, carried across a halt so a resume resolves them first". Each item is the serialised finding
   (`serialize_finding_for_resolver` shape) plus its `iteration` and `introduced_by_action_id`. Include it in `to_dict`
   and read it in `from_dict` with `data.get("open_review_findings") or []`. No schema version bump, because old
   checkpoints load as empty. Raise the `context.py` budget by the exact delta.
2. `flow/review/carried_findings.py` gains:
   - `carry_open_findings(context, iteration, rows, action_ids)`: replace `context.open_review_findings` with the
     serialised rows.
   - `load_carried_findings(context) -> tuple[int, list[FindingRow], dict[str, str]]`: deserialises through the
     `review_report` parser found in Step 1, or a new `deserialize_finding_row` there. It returns the halted iteration,
     the rows, and the introducing action ids.
   - `still_open(carried_rows, dispositions) -> list[FindingRow]`: a row stays open unless it got `applied`, or
     `rejected` with `has_stated_reason`. Matching is by `finding_signature`.
   - `resolve_carried_findings(context, target, carried, recorder, run_resolver_fn) -> CarriedResolution`: one resolver
     pass over the carried rows through `_invoke_resolver_pass`, labelled with the carried iteration. On error it
     records nothing and returns the error. On success it records the applied and reasoned-rejected dispositions with
     the shared `DispositionRecorder` and returns the remaining open rows and the possibly moved head.
3. `flow/review/loop.py` call sites (short, each 1–4 lines):
   - Before the `while` loop, when `resolver_enabled` and carried findings exist, call `resolve_carried_findings`.
     - If it fails: set `resolver_error`, keep the carried state and skip the ensemble iterations. The existing halt and
       reporting path runs, and approval is impossible.
     - If it succeeds: update `resolved_target.head_sha` when the head moved, and seed the loop's `carried_open` list
       with the rows still open.
   - Each iteration merges `carried_open` into `iter_findings` (deduplicated by signature) before the resolver pass, so
     an open finding is re-offered to the resolver until it gets a disposition.
   - At the termination check, a clean ensemble counts as consensus only when `carried_open` is empty. Otherwise log
     `Consensus blocked at iteration N: K finding(s) from earlier iterations have no disposition` and fall through to
     the resolver pass with the carried rows as findings. `adjudication.py` gets a one-line helper only if the blocker
     string belongs with `consensus_blockers`; otherwise no change there.
   - After the loop: on `resolver_error`, call `carry_open_findings` with the halted iteration's findings. On any other
     exit, carry the rows still open, or clear the field when none remain. The engine's existing post-Stage-4 checkpoint
     save persists the field.
   - Raise the `review/loop.py` budget by the exact remaining delta (target ≤ 12 lines after the Step 3 offset) with a
     description sentence.
4. Tests:
   - New, in `tests/test_review_loop.py` (hermetic, fake seams, tmp ledger, `save_checkpoint`/`load_checkpoint` round
     trip):
     - (i) Iteration 1 returns two nits from Reviewer 1. The resolver seam returns a usage-limit failure. Assert: the
       loop halts with `resolver_error`, no ACT-RES adjudication rows, `context.open_review_findings` holds both nits,
       and they survive a checkpoint save and load.
     - (ii) Resume from the loaded context with a recording seam pair. Assert the call order: the resolver is called
       with both carried nits before the first ensemble call. The resolver applies one and rejects the other with reason
       `out of scope`. The ensemble returns APPROVED, consensus is reached, two ACT-RES rows carry the real dispositions
       and reasons, and `open_review_findings` is empty.
     - (iii) Resume where the resolver applies one and leaves the other open, and the ensemble returns 3/3 APPROVED with
       no findings. Assert consensus is not reached, the open nit is re-offered to the resolver on the next iteration,
       and it is still carried after the budget ends.
   - New, in `tests/test_review_resolver_failure.py`: a resume whose carried resolver pass fails again dispatches no
     ensemble and writes no ACT-RES row.
   - A `FlowContext.from_dict` test: a checkpoint without the key loads with an empty list.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** YES (goal 3 and its acceptance test; p_success 0.72 ≥ 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` "Human review is the final gate"; repository complexity limits; flow module
  budgets; no entrypoint imports into the flow engine.
- **Potential failure modes for this step:**
  - `run_review_loop` exceeds `PLR0915`/`C901`.
  - Carried rows deserialise with a different signature from re-raised rows, which duplicates findings.
  - Iteration labels collide when the carried pass reuses the halted iteration's label.
  - Infinite re-offer of a stubbornly open finding.
- **Guardrails and early‑abort checks:**
  - All non-trivial logic lives in `carried_findings.py`.
  - Signature equality is asserted in test (ii).
  - The carried pass reuses the halted label only for ACT-RES `review_iteration`. It does not advance
    `review_iterations_done`.
  - The no-convergence guard and the iteration budget bound repeats, and test (iii) covers it.
  - Run ruff after each call site is added.

### Success & Discard Criteria

- **Success:** Tests (i)–(iii) and the failure-resume and back-compat tests pass. `uv run task lint` budget check passes
  with the stated raises. No `# noqa` is added.
- **Discard:** `loop.py` needs more than 25 net new lines, or the work exceeds 1.5x `cost_pred` (15). In that case stop,
  keep Steps 2–3 (which already stop the false rows), and file a follow-up sub-intent "Carry open review findings across
  a Stage 4 resume" with the findings from this attempt.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.72  |
| entropy_pred        | 3.6   |
| impact_pred         | 80.0  |
| cost_pred           | 10.0  |
| learning_value_pred | 6.0   |
| ev_pred             | 49.52 |

### Step Metrics Rationale

New persisted state, a new pass ordering and a changed consensus condition in a 913-line, budget-capped module make this
the highest-entropy and lowest-probability step. Learning value is highest: it is the first review state carried across
runs, and its tests become the template for future resume semantics.
`EV = 0.72 * 80.0 + 0.5 * 6.0 - 0.3 * 3.6 - 10.0 = 57.60 + 3.00 - 1.08 - 10.00 = 49.52`.

---

## Step 5: Apply both carried PR #203 nits in flow/calibrate_rerun.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Two few-line, well-specified fixes in one module, each with one test.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Goal 5. Dispose of FIND-R1-003-01 and FIND-R1-003-02 by applying both. Both are valid on reading the
  code: (a) `approval_pr_number(context)` runs outside the `try` of a function documented as "never raises"; (b)
  `review_calibration_freshness` drops the refreshed `StageResult`, so `context.stage_results["calibrate"]` keeps naming
  the superseded head.
- **Git branch:** `I-1795130255-rescrash/P-1795130272-f4c1d312-claude-agent-claude-opus-5-5/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. (a) In `_post_marked_comment`, move the `pr_number or approval_pr_number(context)` resolution and the early-return
   guard inside the existing `try`, so any exception from the lookup becomes
   `{"posted": False, "reason": "comment posting failed"}` with the WARNING log. Behaviour is otherwise unchanged.
2. (b) In `review_calibration_freshness`, when `refreshed is not prior`, store
   `context.stage_results["calibrate"] = refreshed` before building the record. The report-back and the engine's next
   checkpoint save then name the final head (`refreshed.payload["calibrated_head"]`). A failed rerun is stored too, so
   the checkpoint never claims the old success.
3. Keep the docstrings accurate (mention the stored result in `review_calibration_freshness`). Raise the
   `calibrate_rerun.py` budget by the exact delta (expected 1–2 lines) with a description sentence.
4. Tests in `tests/test_calibrate_rerun.py`:
   - (a) Monkeypatch `approval_pr_number` in the module to raise. Call `post_stale_signal` with `pr_number=None`. Assert
     it returns `posted: False` and does not raise.
   - (b) A moved-head fixture with a fake `run_stage` returning a SUCCESS result whose payload `calibrated_head` is the
     final head. After `review_calibration_freshness`, assert `context.stage_results["calibrate"]` is that result, and
     that `save_checkpoint` then `load_checkpoint` shows `calibrated_head` equal to the final head, not the superseded
     one.

### Dependencies & Criticality

- **Depends on:** Step 1 (independent of Steps 2–4; can run in parallel on the same branch)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2–§3; flow module budgets.
- **Potential failure modes for this step:**
  - Storing a failed rerun result changes how Stage 5 is reported on a later resume.
  - The existing tests compare `context.stage_results` identity.
- **Guardrails and early‑abort checks:** Run `tests/test_calibrate_rerun.py`, `tests/test_calibrate_final_head_ci.py`
  and `tests/test_calibrate_sync.py` after the change.

### Success & Discard Criteria

- **Success:** Both new tests pass, and the existing calibrate tests pass unchanged. The execution report lists both
  nits as `applied` with the commit SHA.
- **Discard:** Fix (b) breaks the Stage 5 resume semantics in an existing test. In that case record FIND-R1-003-02 as
  rejected with the stated reason "conflicting with a higher-severity finding" plus the failing test name, instead of
  forcing it.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.9   |
| impact_pred         | 35.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 29.93 |

### Step Metrics Rationale

Tiny, well-understood edits, so `p` is high. Impact is moderate: these are nits, but (b) fixes a wrong head in the
checkpoint and report-back. `EV = 0.92 * 35.0 + 0.5 * 2.0 - 0.3 * 0.9 - 3.0 = 32.20 + 1.00 - 0.27 - 3.00 = 29.93`.

---

## Step 6: Append void correction rows for ACT-RES-1794920909-f4cc16ac and ACT-RES-1794920909-d214a869

- **Sub‑intent recommendation:** NO
- **Reasoning:** Two appended rows. The risk is in the ledger discipline, not the size, and it is controlled by the
  append helper and a prefix check.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Goal 4. Make the two false decisions auditable as void without touching the original lines
  (executions.jsonl lines 4540 and 4541 at planning time).
- **Git branch:** `I-1795130255-rescrash/P-1795130272-f4c1d312-claude-agent-claude-opus-5-5/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Snapshot `sha256` and byte length of `holon-knowledge/ledger/executions.jsonl` before writing.
2. Append exactly two rows through the ledger's locked append path (`try_append_action_record` with
   `preserve_shape=True`, run once from a `uv run python` one-off; not committed as code). Each row:
   - `action_id`: a fresh id from `generate_action_id("ACT-RES")`, so readers that filter on the ACT-RES prefix see the
     correction next to the row it voids.
   - `execution_id`: `REV-RES-3-1794920909`.
   - `entry_type`: `action`; `event_type`: `ledger_correction`; `category`: `code`; `action_origin`: `pr_review`;
     `outcome`: `success`.
   - `target`: the same target as the voided row.
   - `parent_action_id` and `voids_action_id`: the voided action id.
   - `review_iteration`: 3; `review_finding_ids`: `["R1-F1"]` or `["R1-F2"]` respectively.
   - `review_disposition`: null; `rejection_reason`: null.
   - `labels`: `["review", "resolver", "iteration-3", "ledger-correction", "void"]`.
   - `error_context`: "void: the Stage 4 resolver exited 1 on a usage limit (You've hit your session limit) at
     2026-11-17T13:08:29Z; no resolver adjudicated this finding; the 'adjudicated: rejected / unspecified' row was a
     crash fallback (fixed by I-1795130255-rescrash). The finding (PR #203 FIND-R1-003-0N) is disposed by this intent:
     applied in flow/calibrate_rerun.py."
   - `created_at`: now.

   Match R1-F1 and R1-F2 to FIND-R1-003-01/02 using the introducing ACT-FIND rows (ACT-FIND-1794920901-52fca8eb and
   ACT-FIND-1794920901-f12759ea) before writing the text.

3. Verify: the first N bytes of the new file equal the snapshot (`assert_ledger_prefix` or a byte comparison), `wc -l`
   grew by exactly 2, both new lines parse as JSON, and `git diff` shows only two added lines.
4. Document the correction shape in `docs/ledger_schema.md` under Discrete Action Records (Step 7).

### Dependencies & Criticality

- **Depends on:** Step 5 (so the error_context can truthfully state both nits were applied; if Step 5 rejects one, the
  text says "rejected: <reason>" instead)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §3 (Append-Only, Zero Modification); `docs/safety.md`
  Invariant 6; `docs/ledger_schema.md` "Corrections are recorded as new events".
- **Potential failure modes for this step:**
  - An editor or formatter re-serialises the file.
  - The append runs twice.
  - A merge rebase reorders lines.
- **Guardrails and early‑abort checks:**
  - Never open the ledger in an editor; only the append helper writes it.
  - Run the prefix check immediately. If it fails, `git checkout -- holon-knowledge/ledger/executions.jsonl` (safe,
    because nothing else in this execution wrote to it yet) and retry once.
  - Grep for `voids_action_id` before writing, so the rows are never duplicated.

### Success & Discard Criteria

- **Success:** Exactly two new rows appended, prefix check passes, original lines 4540–4541 are byte-identical.
- **Discard:** The prefix check fails twice. In that case stop and escalate to a human (constraints §3 is a hard rule).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 0.8   |
| impact_pred         | 40.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 35.96 |

### Step Metrics Rationale

Mechanically simple, with a strong verification check. Impact is moderate-high: it repairs calibration data and sets the
first correction-row convention for ACT rows.
`EV = 0.93 * 40.0 + 0.5 * 2.0 - 0.3 * 0.8 - 2.0 = 37.20 + 1.00 - 0.24 - 2.00 = 35.96`.

---

## Step 7: Document the resolver-failure rule, the rejection-reason rule, carried findings and the correction row

- **Sub‑intent recommendation:** NO
- **Reasoning:** A short documentation update that keeps the docs truthful. Low risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Keep `docs/review_consensus.md` and `docs/ledger_schema.md` in line with the new behaviour.
- **Git branch:** `I-1795130255-rescrash/P-1795130272-f4c1d312-claude-agent-claude-opus-5-5/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `docs/review_consensus.md`: add or adjust a short subsection "Resolver failures and open findings" covering:
   - a failed resolver turn (non-zero exit, usage limit, timeout, no usable output) writes no ACT-RES row and its
     findings stay open;
   - every rejection needs a stated reason from the vocabulary, and an `unspecified` or empty reason leaves the finding
     open;
   - open findings are checkpointed (`open_review_findings`) and resolved first on a resumed Stage 4;
   - consensus is blocked while any earlier finding has no disposition.

   Remove or correct any sentence that says an unadjudicated nit defaults to rejected.

2. `docs/ledger_schema.md`: under Discrete Action Records, document `event_type: ledger_correction` with
   `voids_action_id`, and the rule that readers treat a voided row as not having happened as a decision.
3. Run `npx prettier --write` on the two files, then `uv run task lint-docs`.

### Dependencies & Criticality

- **Depends on:** Steps 2–6
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` "Safety is auditable".
- **Potential failure modes for this step:** Prettier reflow churn; docs drifting from code.
- **Guardrails and early‑abort checks:** Format only the two touched docs; diff-check that no other markdown changed.

### Success & Discard Criteria

- **Success:** `uv run task lint-docs` passes, and both docs describe the implemented behaviour.
- **Discard:** N/A (cheap; fix and rerun).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.5   |
| impact_pred         | 15.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 12.15 |

### Step Metrics Rationale

Routine documentation work. `EV = 0.92 * 15.0 + 0.5 * 1.0 - 0.3 * 0.5 - 2.0 = 13.80 + 0.50 - 0.15 - 2.00 = 12.15`.

---

## Step 8: Verify: affected tests, then the full suite once, lint and lint-docs

- **Sub‑intent recommendation:** NO
- **Reasoning:** A required gate. Its single runs follow the test-run discipline, because this intent has no timing
  requirement.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the acceptance criteria: hermetic tests for goals 1–3 and 5, a green full suite,
  `uv run task lint` and `uv run task lint-docs`.
- **Git branch:** `I-1795130255-rescrash/P-1795130272-f4c1d312-claude-agent-claude-opus-5-5/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run the affected tests first:
   `uv run pytest apps/sandbox-executor/tests/test_review_resolver_failure.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_resolver_evidence.py apps/sandbox-executor/tests/test_calibrate_rerun.py apps/sandbox-executor/tests/test_review_report.py apps/sandbox-executor/tests/test_ledger_append_only.py apps/sandbox-executor/tests/test_review_failure.py`.
2. Then run the full suite once: `uv run task test` (`-n auto --dist worksteal`, as configured). No repeated runs: the
   intent sets no timing or performance target.
3. Run `uv run task lint` (ruff, format, uv.lock check, flow module budgets) and `uv run task lint-docs`.
4. Map each acceptance criterion to its test ID or check in the execution report. Include the Step 6 prefix-check output
   and the disposition of each PR #203 nit with its commit SHA.

### Dependencies & Criticality

- **Depends on:** Steps 2–7
- **Is Bottleneck:** YES (the acceptance gate; p_success 0.82 ≥ 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3; `docs/safety.md` Invariant 4.
- **Potential failure modes for this step:**
  - An unrelated flaky test.
  - A budget check failing because a raise was missed.
  - A ruff complexity error in `run_review_loop`.
- **Guardrails and early‑abort checks:**
  - Fix lint and budget failures in place.
  - A failing test unrelated to the touch set is rerun once alone. If it still fails, it is reported with its output,
    not masked.

### Success & Discard Criteria

- **Success:** All affected tests and the full suite pass, and lint and lint-docs pass. Every acceptance criterion maps
  to evidence.
- **Discard:** A regression in the review loop that cannot be fixed within 1.5x the Step 4 cost. In that case report the
  failure honestly with output, and do not claim completion.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 0.7   |
| impact_pred         | 20.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 13.19 |

### Step Metrics Rationale

The full suite and the strict budget and complexity gates are where small misses surface, so `p` is 0.82.
`EV = 0.82 * 20.0 + 0.5 * 2.0 - 0.3 * 0.7 - 4.0 = 16.40 + 1.00 - 0.21 - 4.00 = 13.19`.

---

## Sub-intent list

- None
