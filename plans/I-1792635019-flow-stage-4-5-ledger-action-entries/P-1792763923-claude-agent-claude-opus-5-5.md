I couldn't write `plans/P-1792763923-claude-agent-claude-opus-5-5.md` because the write was blocked by a permission
prompt. I didn't retry or try another route, and I made no code changes. The full plan is below. Once you grant write
access to `plans/`, I can save it to that file unchanged.

Code reading changed some of the intent's premises:

- **Premise (a) is only half right.** `atomic_ledger_append()` is already a wrapper around `LedgerLock`, so the Stage 4
  rows are locked. The real gaps are that they skip validation and that failures are only logged at debug level.
- **Moving to `append_action_record()` as it stands would break byte-compatibility.** It reorders keys, adds six fields
  and drops unknown keys, so ACT-CAL couldn't carry its metrics. The plan adds an opt-in mode that validates the record
  and then writes it exactly as given.
- **Premise (d) is mostly false.** All three native Stage 4 entry points call `run_review_loop()`. The only gap is the
  prompt-driven review in `review_loop.template.md`.
- **Two bugs the intent didn't name.** ACT-POST is skipped whenever posting fails (the ledger has 78 ACT-REV rows but
  only 6 ACT-POST). The calibration commit only includes the report file, so an ACT-CAL row would be left uncommitted.
- **One suspected cause is deferred.** The review loop never commits `executions.jsonl`, so rows written in a
  container's throwaway clone are probably lost. Step 7 checks this cheaply. Fixing it would mean committing to the PR
  head during review, so I've proposed it as a separate follow-up intent.

---

# Plan for I-1792635019-flow-stage-4-5-ledger-action-entries

- **Plan ID:** P-1792763923-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-23T13:58:44.168Z

## Planner Autonomy Summary

- **Intent handling:** REFRAME
- **Reframed intent (if applicable):**
  `{"keep": ["route ACT-REV/ACT-FIND/ACT-RES/ACT-POST through append_action_record", "new ACT-CAL record from the calibration engine", "WARNING-level, non-aborting ledger failures", "regression tests", "docs"], "correct": ["premise (a): atomic_ledger_append already holds LedgerLock, so the real defect is missing schema validation and silent failure, not missing locking", "premise (d): every native Stage 4 entry point already funnels into run_review_loop(); the only unprovenanced path is the prompt-driven review in holon-config/prompts/review_loop.template.md"], "add": ["decouple ACT-POST from the post_pr_review_consensus try block", "opt-in shape-preserving mode in append_action_record so records stay byte-compatible and ACT-CAL can carry metrics", "stage executions.jsonl in the calibration commit so ACT-CAL is not lost on branch switch", "mirror ledger warnings into context.log so they reach operator output"], "defer": ["committing/pushing Stage 4 ledger rows from the review loop (new follow-up intent; see Step 7)"]}`
- **Exploration stance:** balanced. The core work is exploitation of a well-understood code path (5 files, existing
  helpers, existing test patterns), but one cheap exploratory probe (Step 7) is worth running because it tests a
  suspected sixth root cause that the intent did not name and that changes what "lands in every target repository"
  actually means.
- **Safety priority level:** elevated
- **Priority Justification:** `holon-config/world/constraints.md` §3 (Ledger Immutability) and `docs/safety.md`
  Invariant 6 make the ledger append-only and audited. This plan changes the code that writes to it, and tests must
  never touch the repository's real `holon-knowledge/ledger/executions.jsonl`. A bug here could corrupt or falsify
  provenance, but no secrets, network or governance surfaces are involved, so the level is elevated rather than
  critical.

## Exploration

- **Proportion of steps that are exploratory:** 0.125 (1 of 8 steps)
- **Justification:** The root causes are already confirmed by reading code and ledger evidence (see Strategy Rationale).
  Exploration is limited to one probe, Step 7. It checks whether Stage 4 rows survive an ephemeral container clone. That
  answer decides whether a follow-up intent is needed, which is high learning value for little cost. Doing more
  exploration would only add entropy to a provenance-critical module.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.75  |
| entropy_pred        | 9.9   |
| impact_pred         | 85.0  |
| cost_pred           | 45.0  |
| learning_value_pred | 5.5   |
| ev_pred             | 18.53 |

### Strategy Rationale

The intent is accepted in substance but **reframed**. Code inspection showed that two of its four premises are only
partly true. Building fixes on those premises as written would cost effort with no gain, or would break byte
compatibility.

**Root-cause findings (verified against the code, not guessed):**

1. **(a) "raw `atomic_ledger_append()` instead of LedgerLock": PARTLY TRUE.** In `ledger.py`, `atomic_ledger_append()`
   (line ~285) is a thin `@contextmanager` around `LedgerLock`. The four Stage 4 writers in `flow.py`
   (`run_review_loop`, lines ~2600–2778) **are** therefore already serialised under `flock(LOCK_EX)`. The real defect is
   that they skip `ActionRecord.validate()`. That is why the intent's ask to use `append_action_record()` is still
   right: it buys validation.
2. **Byte-compatibility conflict (not named in the intent).** `append_action_record()` with a dict serialises through
   `ActionRecord.from_dict(...).to_dict()`. That **reorders keys and adds six fields** that are absent from the current
   rows (`parent_action_id`, `event_type: "command_execution"`, `mistake_category`, `review_disposition`,
   `rejection_reason`, `introduced_by_action_id`). It also **drops unknown keys**, so an ACT-CAL record could not carry
   `plan_branch`, metrics or a report path. Routing through the helper unchanged would break goal 2 and goal 3 at once.
   Step 2 fixes this with an opt-in, validate-then-write-verbatim mode.
3. **(b) "silent `logger.debug`": TRUE.** All five Stage 4 append sites and the `append_action_event` call log at DEBUG.
   So do the execute-stage `append_action_event`, the calibrate-stage reconcile, and the plans-ledger append. Also, the
   intent and execute primary appends (`flow.py` ~2040, ~2420) are not wrapped at all, so an `OSError` aborts the stage.
4. **ACT-POST coupling bug (not named in the intent): TRUE.** The ACT-POST append sits in the **same** `try` as
   `post_pr_review_consensus()`. Any posting exception skips the ACT-POST row, and the skip is logged only at DEBUG.
   This matters in practice: the repository ledger holds 78 ACT-REV rows but only 6 ACT-POST rows.
5. **(c) "skipped under `effective_dry_run`": TRUE BUT INTENDED.** Dry-run is defined as "evaluate without applying git
   commits or mutations". Appending to an audited ledger during a dry run would record provenance for work that never
   happened. **Decision: keep the skip, but make it visible** in `context.log`.
6. **(d) "only reached via the native review loop": MOSTLY FALSE.** `run_review_stage`, `run_review_loop_cli` and
   `entrypoint/review_loop.py` (the Docker `review-loop` role) all call `run_review_loop()`. The one real gap is the
   prompt-driven review in `holon-config/prompts/review_loop.template.md`. Its line ~170 tells an LLM agent to append
   records itself, with no validation and no LedgerLock. Step 3 exposes a public `record_review_action()` helper, and
   Step 6 points the template at it.
7. **Stage 5 gap: TRUE.** `calibration.py` writes no ledger row. The ledger has 0 `ACT-CAL` and 0
   `action_origin: "calibration"` rows, although `"calibration"` is already in `VALID_ORIGINS`. **Correction:** there is
   **no** existing Stage 5 summary writer, so the requirement becomes "write exactly one ACT-CAL and no summary". Also,
   `run_calibrate()` commits with pathspec `-- report_rel` only. An appended ACT-CAL would stay uncommitted and be left
   behind when `run_calibrate_stage` checks out the execution branch. Step 4 adds `executions.jsonl` to that commit.
8. **Suspected sixth cause (to verify in Step 7):** neither `run_review_loop()` nor `entrypoint/review_loop.py` ever
   commits `executions.jsonl`. In container mode the entrypoint clones the target into an ephemeral workspace, so Stage
   4 rows would exist only in a discarded working tree. Fixing it means committing to the PR head during review, which
   changes CI and consensus semantics. It is **deferred** to a follow-up intent.

**Aggregation:** the steps form one dependency chain (2 → 3/4 → 5 → 8), and their failure modes are correlated. The
naive product `0.90 * 0.85 * 0.80 * 0.85 * 0.88 = 0.46` would under-state success, so `p_success` is a qualitative
judgement anchored just below the bottleneck, Step 4 (`p = 0.80`): **0.75**. Entropy is the sum of step entropies
(`9.9`). Cost is the sum of step costs (`45.0`). Impact is a normalised judgement (`85`). Learning value (`5.5`) is
weighted towards Step 7. Overall EV:
`EV = 0.75 * 85.0 + 0.5 * 5.5 - 0.3 * 9.9 - 45.0 = 63.75 + 2.75 - 2.97 - 45.0 = 18.53`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `constraints.md` §3 Ledger Immutability (append-only) and `safety.md` Invariant 6.
  - `constraints.md` §1 Git Flow: prefix branches, rebase before execution, no commits to `main`.
  - `ruleset.md` §2: typing and docstrings; §3: `tests/test_<module>.py`, and test changes must be declared (Step 5).
  - `ruleset.md` §1: `uv` only, no new dependencies, Python `==3.13.*`.
  - `safety.md` sandbox selection: entropy `9.9` with baseline trust leads to a **container sandbox**.
  - Intent constraints: Australian English; no new ledger files; no backfill; no schema change for existing record
    types; `uv run pytest` only.
- **Potential violations or edge cases:**
  - Tests writing to the real repo ledger (`repo_dir` defaulting to `.`).
  - Changing the default output of `append_action_record()` would silently affect other callers.
  - Warn-and-continue on Stage 1–3 primary appends could hide a missing row.
  - Any fallback ledger file would breach intent Q&A 3.
  - The review template edit edges towards governance.
- **Mitigations built into the plan:**
  - Tests run only in `tmp_path` repos. A guard fixture asserts the real ledger's line count and SHA-256 are unchanged.
  - Shape-preserving mode is opt-in, keyword-only and `False` by default.
  - Helpers never fall back to another path. Warnings are mirrored into `context.log`.
  - Golden key-order tests compare new rows against historical row shapes.
  - Only `open(..., "a")` under `LedgerLock` is used, with no read-then-rewrite.
- **Residual risk accepted (and why):** Stage 4 rows remain working-tree-only until the deferred follow-up lands.
  Committing during review changes PR head SHAs and CI semantics, so it needs its own evaluation.
- **Allocated Entropy Budget:** 12.0
- **Predicted Plan Entropy:** 9.9
- **Budget Compliance:** The strategy fits within budget (`9.9 / 12.0 = 0.825`, leaving 2.1 headroom for one retry of
  Step 3 or Step 4).

## Plan Description & Strategy

All work runs on the plan branch
`I-1792635019-flow-stage-4-5-ledger-action-entries/P-1792763923-claude-agent-claude-opus-5-5/_`, with execution branches
of the form `.../E-{timestamp}-{action}/_`. No step justifies its own sub-intent; the changes are coupled through one
helper. The deferred review-loop ledger commit is proposed as a **new root intent** for human triage.

Order of work:

1. Confirm the root causes with a failing probe test (red).
2. Harden `ledger.py`.
3. Rewire the `flow.py` writers.
4. Add the Stage 5 ACT-CAL writer.
5. Write regression tests (green).
6. Update docs and the template pointer.
7. Run the exploratory container-persistence probe.
8. Run the full validation gate.

Expected surface area: `ledger.py`, `flow.py`, `calibration.py`, `tests/test_flow.py`, `tests/test_calibration.py`,
`tests/test_ledger_lock.py`, `docs/ledger_schema.md`, optionally `docs/architecture.md`, and
`holon-config/prompts/review_loop.template.md`. No dependency manifests and no existing ledger lines are touched.

---

## Step 1: Confirm root causes with a red probe

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small and read-mostly. It locks the findings into executable evidence before any change.
- **Step Type:** INFO_GATHERING
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Turn root-cause findings 1–7 into recorded evidence: red tests plus a findings section in the
  execution notes.
- **Git branch:**
  `I-1792635019-flow-stage-4-5-ledger-action-entries/P-1792763923-claude-agent-claude-opus-5-5/E-{timestamp}-root-cause-probe/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Rebase on the plan branch.
2. In a `tmp_path` git repo, call `run_review_loop()` (non-dry-run, `auto_pr=False`). Patch `check_pr_mergeability` and
   `probe_ci_checks` to return a dirty state for iteration 1 and clean for iteration 2. Make `post_pr_review_consensus`
   raise. Expected: ACT-REV, ACT-FIND and ACT-RES land; ACT-POST is missing; the failure is DEBUG-only.
3. Make the ledger directory read-only. Expect no WARNING in `caplog`.
4. Run `run_calibrate(skip_commit=False)` on a `tmp_path` repo. Expect zero ACT-CAL rows and `executions.jsonl` absent
   from the commit.
5. Write the verdicts (TRUE / PARTLY TRUE / INTENDED / FALSE) into the execution notes.
6. Reuse the probes as the skeleton for Step 5. Do not commit red tests.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §3; `safety.md` Invariant 6; `ruleset.md` §3.
- **Potential failure modes for this step:** the probe writes to the real ledger; a real `gh` call escapes.
- **Guardrails and early‑abort checks:** always pass an explicit `repo_dir=tmp_path`; patch `shutil.which("gh")` to
  return `None`; abort on any `git status` change under `holon-knowledge/`.

### Success & Discard Criteria

- **Success:** Every finding has a probe outcome that matches the prediction.
- **Discard:** If a contradiction appears, re-plan before Step 2. Discard if the cost exceeds `1.5 * 2.0 = 3.0`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.3   |
| impact_pred         | 10.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 10.11 |

### Step Metrics Rationale

This step only reads production paths and uses existing fixtures. Its moderate learning value comes from turning the two
corrected premises into evidence. `EV = 0.97 * 10.0 + 0.5 * 5.0 - 0.3 * 0.3 - 2.0 = 10.11`.

---

## Step 2: Harden `ledger.py`: shape-preserving validated append and fail-loud wrapper

- **Sub‑intent recommendation:** NO
- **Reasoning:** About 40 additive, opt-in lines. It is the foundation for Steps 3 and 4.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Allow validated appends under LedgerLock without changing the serialised shape, and provide one
  fail-loud, non-raising entry point.
- **Git branch:**
  `I-1792635019-flow-stage-4-5-ledger-action-entries/P-1792763923-claude-agent-claude-opus-5-5/E-{timestamp}-ledger-helpers/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add a keyword-only `preserve_shape: bool = False` to `append_action_record`. The default behaviour stays
   byte-identical.
2. When `preserve_shape=True`:
   - Copy the dict and force `entry_type = "action"`.
   - Fill `action_id`/`action_origin` only if absent.
   - Validate through `ActionRecord.from_dict(copy).validate()`.
   - Write `json.dumps(copy)` verbatim under `LedgerLock`, keeping key order and extra keys.
   - Raise `ValueError` before taking the lock, so nothing partial is written.
3. Add `try_append_action_record(ledger_path, record, *, label, preserve_shape=True, log_fn=None) -> dict | None`. It
   catches `Exception`, logs a WARNING naming the label, action id, ledger path and exception, and mirrors the message
   to `log_fn`. It returns `None` and never falls back to another path.
4. Add `try_append_ledger_line(ledger_path, record, *, label, log_fn=None) -> bool` for summary-type rows, wrapping
   `atomic_ledger_append` with the same contract.
5. Write full docstrings in Australian English with strict typing. Use the standard library only.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (`p_success` 0.90 is above 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §3; intent "no schema change"; `ruleset.md` §2.
- **Potential failure modes for this step:** the default path regresses for existing callers.
- **Guardrails and early‑abort checks:** run `uv run pytest tests/test_ledger_query.py tests/test_ledger_lock.py`
  immediately.

### Success & Discard Criteria

- **Success:** Existing tests pass unchanged; historical ACT-REV key order is preserved; invalid input raises before any
  write.
- **Discard:** If the `ActionRecord` fields would need changing, escalate (that is a schema change). Discard if the cost
  exceeds `1.5 * 6.0 = 9.0`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.6   |
| impact_pred         | 25.0  |
| cost_pred           | 6.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 17.52 |

### Step Metrics Rationale

Small, additive and well-tested; entropy comes mainly from SSA plus some CL on a hot file.
`EV = 0.90 * 25.0 + 0.5 * 3.0 - 0.3 * 1.6 - 6.0 = 17.52`.

---

## Step 3: Rewire the Stage 4 writers and every other `flow.py` ledger append to fail loud

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mechanical substitutions at known lines plus one `try` split. Keeping them here avoids rebase churn on
  a hot file.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the four ACT-\* rows validated, LedgerLock-serialised and byte-compatible. Make ACT-POST
  independent of posting. Make every ledger failure visible without aborting the stage.
- **Git branch:**
  `I-1792635019-flow-stage-4-5-ledger-action-entries/P-1792763923-claude-agent-claude-opus-5-5/E-{timestamp}-stage4-writers/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add a public `record_review_action(context, ledger_path, record)` that delegates to `try_append_action_record` with
   `log_fn=context.log`. It is the canonical writer for any integration point.
2. Replace the four raw appends (~2615, ~2676, ~2700, ~2775) with it. Leave the record literals unchanged: same keys and
   order, same prefixes, same `REV-RES-`/`REV-ITR-`/`REV-FIND-`/`REV-POST-` ids, same labels and same timestamp format.
3. Split posting into its own `try`. On error, log a WARNING and set `posting_res = {"error": ...}`. Always append
   ACT-POST (non-dry-run); its outcome becomes `"failure"` when posting raised.
4. Route the REV summary (~2804) through `try_append_ledger_line`, with its shape unchanged.
5. Promote the ledger-related DEBUG logs (~2439, ~2831, ~3204, ~2238) to WARNING plus `context.log`.
6. Wrap the unguarded intent (~2040) and execution (~2420) appends, keeping the duplicate-ID checks.
7. Under dry-run, emit one `context.log` line with the count of suppressed records.
8. Clean up imports with ruff. Use Australian English in log strings.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES (`p_success` 0.85 is above 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §3; intent goal 2; Bean 0034 (merge logic untouched).
- **Potential failure modes for this step:** literal reordering; `posting` payload drift; existing tests that patch
  `flow.atomic_ledger_append` break.
- **Guardrails and early‑abort checks:** diff the literals; run `tests/test_flow.py` and `tests/test_review_loop.py`
  after each edit; abort if more than 3 existing assertions need changing.

### Success & Discard Criteria

- **Success:** Rows are validated and byte-compatible; ACT-POST is present even when posting raises; no ledger-failure
  `logger.debug` remains; existing tests pass.
- **Discard:** If byte-compatibility is unattainable, or the cost exceeds `1.5 * 10.0 = 15.0`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 2.8   |
| impact_pred         | 35.0  |
| cost_pred           | 10.0  |
| learning_value_pred | 3.0   |
| ev_pred             | 20.41 |

### Step Metrics Rationale

The highest-impact step, delivering goals 2 and 4 for the most-used stage. The hot-file SSA/CL is offset by the edits
being substitutions. `EV = 0.85 * 35.0 + 0.5 * 3.0 - 0.3 * 2.8 - 10.0 = 20.41`.

---

## Step 4: Add the Stage 5 ACT-CAL action record and commit it with the calibration report

- **Sub‑intent recommendation:** NO
- **Reasoning:** One new record type and one widened pathspec, with moderate git-interplay risk that tests isolate well.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Append exactly one validated ACT-CAL record under LedgerLock and persist it in the calibration
  commit.
- **Git branch:**
  `I-1792635019-flow-stage-4-5-ledger-action-entries/P-1792763923-claude-agent-claude-opus-5-5/E-{timestamp}-stage5-act-cal/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add `record_calibration_action(report, repo_dir, report_rel, *, outcome="success", log_fn=None)`. Fields, in fixed
   order:
   - `action_id` with prefix `ACT-CAL`
   - `execution_id` of the form `CAL-{plan_id}-{ts}`
   - `action`
   - `category` `"doc"`
   - `target` (the calibrated branch)
   - `outcome`
   - `entry_type` `"action"`
   - `action_origin` `"calibration"` (already valid)
   - `labels` `["calibration", "stage-5", plan_id]`
   - `error_context`
   - `plan_branch`, `execution_branch`, `evaluated_commit_sha`
   - `predicted_metrics`, `actual_metrics`, `calibration_deltas`
   - `calibration_report_path`
   - `created_at`

   It is written through `try_append_action_record(preserve_shape=True)`.

2. Call it in `run_calibrate()` after the report is written and before the commit, only when `skip_commit` is False. Log
   at INFO when it is skipped.
3. Stage and commit `holon-knowledge/ledger/executions.jsonl` alongside `report_rel`.
4. Promote the commit-error DEBUG log (~1386) to WARNING.
5. In the `run_calibrate_stage` exception fallback (non-dry-run), record ACT-CAL with `outcome="failure"`.
6. Write no summary record. Confirm that `parse_actual_metrics` ignores action rows.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES (`p_success` 0.80 is above 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §3; intent goals 3 and 5; Q&A 1.
- **Potential failure modes for this step:** metric-parser contamination; unrelated files staged; re-runs append a new
  ACT-CAL. Re-runs are legitimate new calibration events and are documented as such.
- **Guardrails and early‑abort checks:** a parity test on `parse_actual_metrics`; abort if the staged diff contains
  extra files.

### Success & Discard Criteria

- **Success:** One committed ACT-CAL per non-skip run, with all fields present and metrics unchanged.
- **Discard:** If the parser needs a schema change, escalate. Discard if the cost exceeds `1.5 * 9.0 = 13.5`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 2.4   |
| impact_pred         | 30.0  |
| cost_pred           | 9.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 16.28 |

### Step Metrics Rationale

The bottleneck step: new behaviour plus git commit scope and parser-contamination risk.
`EV = 0.80 * 30.0 + 0.5 * 4.0 - 0.3 * 2.4 - 9.0 = 16.28`.

---

## Step 5: Regression tests (declared test-file changes)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Coupled to Steps 2–4, and the concurrency and ledger test patterns already exist.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Cover goal 6 (a)–(d), byte-compatibility, the ACT-POST decoupling and the real-ledger guard.
- **Git branch:**
  `I-1792635019-flow-stage-4-5-ledger-action-entries/P-1792763923-claude-agent-claude-opus-5-5/E-{timestamp}-ledger-regression-tests/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

Declared files: `tests/test_flow.py`, `tests/test_calibration.py`, `tests/test_ledger_lock.py`. Only new tests are
added.

1. **(a)** A two-iteration `run_review_loop` in `tmp_path`. Assert ACT-REV ×2, ACT-FIND ×1, ACT-RES ×1 and ACT-POST ×1,
   all with `entry_type` `action` and `action_origin` `pr_review`, in chronological order. Assert that no
   `actions.jsonl` or `reviews.jsonl` exists.
2. **Byte-compatibility:** key lists match historical shapes, and `execution_id` regexes match.
3. **ACT-POST decoupling:** posting raises; assert an ACT-POST row with outcome `failure` and a WARNING.
4. **(b) ACT-CAL:** run `run_calibrate(skip_commit=False)`. Assert one `ACT-CAL-` row with all fields, present in
   `git show HEAD:`. Assert `skip_commit=True` appends nothing and metrics are unchanged.
5. **(c)** Unwritable ledger: a `chmod` variant (skipped for root) and a variant that patches `LedgerLock.__enter__` to
   raise `PermissionError`. Assert a WARNING naming the path and exception, a normal return, the `context.log` mirror,
   and no other file created.
6. **(d)** 8 processes × 50 `append_action_record(preserve_shape=True)` calls with records of about 8 KiB. Assert 400
   valid JSON lines with unique ids. Add a thread variant.
7. An autouse guard asserts that the real `executions.jsonl` line count and SHA-256 are unchanged.
8. Use pytest idioms only, with no `unittest.TestCase` and no `PYTHONPATH`.

### Dependencies & Criticality

- **Depends on:** Steps 2, 3, 4
- **Is Bottleneck:** YES (`p_success` 0.85 is above 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** `ruleset.md` §3; intent goal 6; `constraints.md` §3.
- **Potential failure modes for this step:** root makes `chmod` ineffective; multiprocessing is flaky on 2 CPUs; git
  identity is missing in `tmp_path` repos.
- **Guardrails and early‑abort checks:** use the root-safe variant; keep the concurrency test under 10 seconds; set
  `GIT_*` env vars in fixtures; fall back to threads if flaky.

### Success & Discard Criteria

- **Success:** New tests pass 3 consecutive runs and the suite stays green.
- **Discard:** If multiprocessing stays nondeterministic, keep the thread variant and note it. Discard if the cost
  exceeds `1.5 * 9.0 = 13.5`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.5   |
| impact_pred         | 25.0  |
| cost_pred           | 9.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 13.80 |

### Step Metrics Rationale

The risk is test flakiness, which the fallback variants mitigate. These tests are the durable guard for an audited path.
`EV = 0.85 * 25.0 + 0.5 * 4.0 - 0.3 * 1.5 - 9.0 = 13.80`.

---

## Step 6: Documentation and review-template pointer

- **Sub‑intent recommendation:** NO
- **Reasoning:** Low-risk prose that must ship with the behaviour it describes.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Document the per-stage records in `executions.jsonl` and the LedgerLock serialisation, and point
  prompt-driven reviewers at the canonical writer.
- **Git branch:**
  `I-1792635019-flow-stage-4-5-ledger-action-entries/P-1792763923-claude-agent-claude-opus-5-5/E-{timestamp}-ledger-docs/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add a "Flow stage record matrix" to `docs/ledger_schema.md`:
   - Stage 1: intent row in `intents.jsonl`.
   - Stage 2: plan row in `plans.jsonl`.
   - Stage 3: execution summary plus action in `executions.jsonl`.
   - Stage 4: ACT-REV, ACT-FIND, ACT-RES and ACT-POST, the REV summary and the review action event, in
     `executions.jsonl`.
   - Stage 5: ACT-CAL, in `executions.jsonl`.

   Then state that all appends go through LedgerLock, that failures are WARNING-logged with no fallback, that dry-run
   and skip-commit suppress appends, and that there is no backfill.

2. Document the residual gap (Stage 4 rows are working-tree-only pending the follow-up).
3. Align `docs/architecture.md` and the executor spec if they enumerate records. Keep paths target-native.
4. Make the `review_loop.template.md` lines ~41 and ~170 point to `record_review_action` and the native loop, with no
   policy change.
5. Run Prettier with `.prettierrc.json` until stable.

### Dependencies & Criticality

- **Depends on:** Steps 3, 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 7 and constraint 8; `safety.md` governance (the template edit is flagged for the
  reviewer).
- **Potential failure modes for this step:** Prettier oscillation; field-name drift.
- **Guardrails and early‑abort checks:** copy field names from the code; use code spans; a second Prettier run must be a
  no-op.

### Success & Discard Criteria

- **Success:** The matrix is accurate and Prettier passes.
- **Discard:** Drop the template edit if it is judged to be governance. Discard if the cost exceeds `1.5 * 3.0 = 4.5`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.6   |
| impact_pred         | 10.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 7.02  |

### Step Metrics Rationale

Routine documentation with a deterministic formatter. `EV = 0.92 * 10.0 + 0.5 * 2.0 - 0.3 * 0.6 - 3.0 = 7.02`.

---

## Step 7: Exploratory probe: do Stage 4 rows survive an ephemeral container clone?

- **Sub‑intent recommendation:** YES (for the **fix**, as a new follow-up root intent; the probe runs here)
- **Reasoning:** The probe is cheap and informative. The fix changes PR-head and CI semantics and exceeds this intent's
  headroom.
- **Step Type:** EXPLORATION
- **Exploration level:** EXPLORATORY

- **Hypothesis being tested:** Through `entrypoint/review_loop.py` in a fresh clone, Stage 4 rows are written only to
  the working tree and never committed or pushed, so the target remote never gains them.
- **Learning target:** a binary answer with row counts (working tree versus HEAD versus remote). If confirmed, a drafted
  follow-up intent for a post-consensus ledger-only commit, with its CI and Bean 0034 interactions.
- **Maximum acceptable cost for this learning:** 3.0 cost units. Stop if Docker or network is required.

### Intent & Git Integration

- **Step Intent:** Confirm or refute finding 8 without changing production code.
- **Git branch:**
  `I-1792635019-flow-stage-4-5-ledger-action-entries/P-1792763923-claude-agent-claude-opus-5-5/E-{timestamp}-container-persistence-probe/_`
- **Sub‑intent:** NONE (the follow-up is a proposal only)

### Implementation Details (No code blocks, only logic/steps)

1. Create a bare `file://` remote in `tmp_path` and run the entrypoint `main()` in-process with an empty repo dir,
   `--skip-push`, no auto-PR and patched network helpers.
2. Compare ACT-\* counts in the working tree, in clone HEAD and on the remote branch.
3. Record the outcome. If confirmed, draft a follow-up intent (`review-loop-ledger-persistence`) with its open
   questions. Do not create the branch.
4. Keep the probe as `xfail(strict=True)` only with reviewer agreement; otherwise discard it.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `safety.md` (no network; baseline trust cannot spawn intents); `constraints.md` §2.
- **Potential failure modes for this step:** a network clone; `sys.exit`.
- **Guardrails and early‑abort checks:** use a `file://` remote only; catch `SystemExit`; abort on any `gh` or network
  attempt.

### Success & Discard Criteria

- **Success:** A reproducible yes or no with counts, plus a draft intent if the answer is yes.
- **Discard:** If Docker or network is needed, or the cost exceeds `1.5 * 3.0 = 4.5`, record "unverified".

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.70  |
| entropy_pred        | 0.5   |
| impact_pred         | 5.0   |
| cost_pred           | 3.0   |
| learning_value_pred | 7.0   |
| ev_pred             | 3.85  |

### Step Metrics Rationale

The harness may need unknown patching, which lowers the probability. The high learning value decides whether the
headline symptom is fully closed. `EV = 0.70 * 5.0 + 0.5 * 7.0 - 0.3 * 0.5 - 3.0 = 3.85`.

---

## Step 8: Validation gate

- **Sub‑intent recommendation:** NO
- **Reasoning:** A deterministic acceptance check with no design content.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove that all acceptance criteria pass with zero errors.
- **Git branch:**
  `I-1792635019-flow-stage-4-5-ledger-action-entries/P-1792763923-claude-agent-claude-opus-5-5/E-{timestamp}-validation/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Rebase, then run `uv run pytest` in `apps/sandbox-executor`.
2. Run `uv run ruff check` and `uv run ruff format --check`.
3. Run `npx prettier --check` on the changed markdown.
4. Run the constraint greps: no ledger-failure `logger.debug`; no `actions.jsonl` or `reviews.jsonl`; no
   `unittest.TestCase` in new tests; no `PYTHONPATH`; no American spellings in new strings.
5. Confirm `git diff main -- holon-knowledge/ledger/` is empty (no backfill).
6. Run `scripts/pre_commit_check.py` if required.

### Dependencies & Criticality

- **Depends on:** Steps 1–7
- **Is Bottleneck:** YES (`p_success` 0.88 is above 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** `metrics.md` §1.3; `ruleset.md` §1 and §3; intent constraint 8.
- **Potential failure modes for this step:** pre-existing suite failures; ruff format drift.
- **Guardrails and early‑abort checks:** baseline the suite on the plan-branch base first and report pre-existing
  failures separately.

### Success & Discard Criteria

- **Success:** All commands exit 0, the ledger diff is empty and the greps are clean.
- **Discard:** More than 2 failed fix-and-rerun cycles on in-scope items.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 0.2   |
| impact_pred         | 10.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 6.24  |

### Step Metrics Rationale

A deterministic gate whose only risk is pre-existing drift. `EV = 0.88 * 10.0 + 0.5 * 1.0 - 0.3 * 0.2 - 3.0 = 6.24`.

---
