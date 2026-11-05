# Plan for I-1793869839-stage-4-resolver-review-loop-converges

- **Plan ID:** P-1793869856-antigravity-agent-gemini-4-argon
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-4-argon (version: 1.5.1)
- **Created At:** 2026-11-05T09:10:57.315Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A
- **Exploration stance:** conservative with a targeted validation pass; the Stage 4 flow loop, review report schema, and
  review_loop entrypoint already exist and only require wiring the resolver pass, disposition tracking, 3-iteration
  no-convergence guard, and exit-only consensus posting.
- **Safety priority level:** elevated
- **Priority Justification:** Modifying Stage 4 review loop execution, git push behaviour in the resolver container, and
  append-only ledger recording touches core safety invariants in `docs/safety.md` and
  `holon-config/world/constraints.md` (no force-push, human-only merge, bounded loop convergence, and hermetic testing).

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** All target modules and transport schemas are explicitly specified in the scope and existing
  codebase; deterministic implementation and hermetic unit testing provide maximum Expected Value within the 15.0
  entropy budget.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 8.80  |
| impact_pred         | 85.00 |
| cost_pred           | 9.50  |
| learning_value_pred | 4.50  |
| ev_pred             | 64.91 |

### Strategy Rationale

The intent `I-1793869839-stage-4-resolver-review-loop-converges` is accepted as-is (`ACCEPT_AS_IS`) because the Stage 4
review loop in `apps/sandbox-executor/src/sandbox_executor/flow.py` already manages the iteration counter
(`review_iterations_done`), per-iteration and stage budgets, checkpoint persistence, and containerised single-pass
ensemble dispatch via `_dispatch_review_ensemble`. Completing the loop by dispatching a containerised resolver pass on
`CHANGES_REQUESTED`, recording finding dispositions in `holon-knowledge/ledger/executions.jsonl`, passing rejected
findings forward so they are not re-raised, enforcing the 3-iteration unchanged-finding `no-convergence` guard, and
posting consensus only at loop exits delivers high utility within a single cohesive plan.

Overall metrics are derived from the five sequential steps as follows:

- `p_success_pred = 0.88`: Bottleneck probability across the pipeline (minimum step probability, governed by Step 2
  where the resolver pass, pre-push lint/test verification, and disposition parsing are wired).
- `entropy_pred = 8.80`: Additive sum of step entropies (`1.80 + 2.60 + 2.40 + 1.40 + 0.60 = 8.80`), well within the
  `15.0` allocated entropy budget, requiring no sub-intent decomposition.
- `impact_pred = 85.00`: Maximum step impact (Step 3, which turns Stage 4 from a single-shot reviewer into a converging
  review-and-resolve loop).
- `cost_pred = 9.50`: Additive sum of step costs (`1.80 + 2.70 + 2.60 + 1.60 + 0.80 = 9.50`).
- `learning_value_pred = 4.50`: Maximum epistemic gain across steps (Step 2, establishing the structured resolver
  adjudication transport and pre-push gate pattern).
- `ev_pred = 64.91`: Derived from the bootstrap EV formula
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost` with `mu = 0.5` and `lambda = 0.3`:
  `EV = 0.88 * 85.00 + 0.5 * 4.50 - 0.3 * 8.80 - 9.50 = 74.80 + 2.25 - 2.64 - 9.50 = 64.91`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `docs/safety.md` (Invariant 1 & Invariant 4 — Git discipline & Human review is the final gate): The resolver commits
    and pushes only to the active execution branch, never force-pushes or rewrites history, and Stage 4 halts for human
    merge upon consensus approval.
  - `docs/safety.md` (Invariant 2 — Sandboxing): Both reviewer ensemble passes and resolver passes execute inside
    isolated role containers (`review-loop` entrypoint) on a fresh clone of the execution branch; host-side execution
    refuses unsandboxed agent turns.
  - `docs/safety.md` (Invariant 6) & `holon-config/world/constraints.md` (§3 — Ledger Immutability): All `ACT-REV`,
    `ACT-FIND`, `ACT-RES`, and `ACT-POST` rows are appended exclusively via `record_review_action` /
    `try_append_action_record` to the existing `holon-knowledge/ledger/executions.jsonl` ledger without modifying
    historical lines or creating new ledger files.
  - `holon-config/world/ruleset.md` (§2 & §3 — Coding Conventions & Testing Constraints): Strict Python `3.13.*` typing,
    short low-branching functions, Australian English prose and docstrings, and hermetic tests run via `uv run pytest`
    and `uv run task test`.
- **Potential violations or edge cases:**
  - Resolver attempting to push broken code or rewrite branch history.
  - Reviewer re-raising previously rejected findings on subsequent iterations, causing infinite oscillation.
  - Unchanged finding sets spinning until the 25-iteration cap when no diff change occurs.
  - Intermediate consensus comments spamming the PR thread on every iteration instead of posting once at exit.
- **Mitigations built into the plan:**
  - Mandatory pre-push `uv run task lint` and `uv run task test` check inside the resolver pass, plus strict
    fast-forward `git push origin <execution_branch>` (forbidding `--force` or `--force-with-lease` or
    `git commit --amend`).
  - Passing prior adjudicated rejected findings (ID, file:line, summary, and `rejection_reason`) into the next
    iteration's review prompt and filtering any duplicate re-raised findings that match already-rejected keys.
  - Tracking normalized finding signatures and HEAD SHAs across consecutive iterations so any finding set surviving
    unchanged across 3 consecutive iterations with no diff change is automatically adjudicated `rejected` with
    `rejection_reason="no-convergence"`.
  - Moving `post_pr_review_consensus` strictly outside the iteration loop so it executes once at loop exit (consensus
    approval or budget/no-convergence termination).
- **Residual risk accepted (and why):** Low residual risk (`0.12` failure probability) from complex mock interactions in
  existing Stage 4 tests expecting single-iteration termination when no resolver callback is configured; mitigated by
  keeping clean injectable seams and clear fallback defaults.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 8.80
- **Budget Compliance:** The strategy fits within budget (`8.80 <= 15.0`) and requires no sub-intent decomposition.

## Plan Description & Strategy

We decompose the work into five sequential, low-branching steps restricted strictly to the 8 files in the intent's
`scope` array:

1. **Step 1 (Entrypoint Flag, Prompt Template & Transport Seam):** Document the checkpoint-continued resume semantics on
   `--iteration` in `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py`, add resolver mode CLI flags
   (`--resolve`, `--findings-file`) to `review_loop.py`, update `holon-config/prompts/review_loop.template.md` and
   `apps/sandbox-executor/src/sandbox_executor/scaffold.py` with adjudicated-findings context and resolver disposition
   trailer rules, and add disposition rendering/transport helpers in
   `apps/sandbox-executor/src/sandbox_executor/review_report.py`.
2. **Step 2 (Containerised Resolver Pass & Pre-Push Verification):** Implement the resolver execution helpers in
   `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py` and
   `apps/sandbox-executor/src/sandbox_executor/flow.py` that dispatch the `review-loop` role container on a fresh clone
   of the execution branch when `ensemble_verdict` is `CHANGES_REQUESTED`, adjudicate every finding (critical,
   important, and nit) to `applied` or `rejected` (with one of the valid one-line reasons: `not diff-grounded`,
   `inaccurate`, `out of scope`, or `conflicting with a higher-severity finding`), run `uv run task lint` and
   `uv run task test`, commit applied fixes with `Review-Finding-Ids` trailers, and push without force-pushing.
3. **Step 3 (Convergence Loop, Ledger Dispositions, 3-Iteration Guard & Exit-Only Posting):** Update `run_review_loop`
   and `run_review_stage` in `apps/sandbox-executor/src/sandbox_executor/flow.py` so the loop iterates across resolver
   pushes, populates `review_disposition`, `rejection_reason`, `review_finding_ids`, and `introduced_by_action_id` on
   ledger action rows, hands rejected findings to subsequent iterations so they are not re-raised, adjudicates any
   finding set unchanged across 3 consecutive iterations with no diff change as `rejected` (`no-convergence`), and posts
   the consensus report once at exit.
4. **Step 4 (Hermetic Unit Test Suite):** Add comprehensive hermetic `pytest` tests in
   `apps/sandbox-executor/tests/test_review_report.py`, `apps/sandbox-executor/tests/test_review_loop.py`, and
   `apps/sandbox-executor/tests/test_flow.py` covering all acceptance criteria without real Docker, `gh`, git remotes,
   or LLM calls.
5. **Step 5 (Verification & Linting Gate):** Run the targeted `pytest` files, the full unit suite via
   `uv run task test`, and the code/documentation linters via `uv run task lint` and `uv run task lint-docs`.

---

## Step 1: Extend `--iteration` documentation, prompt templates, and review report disposition helpers

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, self-contained CLI, template, and report-rendering updates with low entropy (`1.80`) and zero
  external risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Document the engine-owned checkpoint-continued `--iteration` resume semantics in `review_loop.py`,
  add CLI transport flags for resolver mode, synchronize `holon-config/prompts/review_loop.template.md` and
  `scaffold.py`, and add finding disposition structures and formatting helpers in `review_report.py`.
- **Git branch:** `I-1793869839-stage-4-resolver-review-loop-converges`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py`, update the `--iteration` argument `help`
   string to explicitly state the resume semantics: the engine owns the counter, saves it in the flow checkpoint
   (`review_iterations_done`), and on a resume from the review stage passes the continued count as this label
   (`done + 1`), so ledger `review_iteration` values and posted `Iteration {n}` headings form one monotonic sequence
   across halts and resumes; it is never a loop limit and is never set by operators.
2. In `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py`, add `--resolve` (boolean flag) and
   `--findings-file` (path to JSON input payload containing the iteration's findings and prior adjudicated rejections)
   so the same `review-loop` role container entrypoint can run a single-shot resolver pass on a fresh clone of the
   execution branch and return structured dispositions through `--result-file`.
3. In `holon-config/prompts/review_loop.template.md` and the mirrored `REVIEW_LOOP_TEMPLATE` constant in
   `apps/sandbox-executor/src/sandbox_executor/scaffold.py`, document:
   - The `--iteration` checkpoint-continued monotonic label semantics.
   - How already-adjudicated rejected findings (with their `id`, `file:line`, `summary`, and `rejection_reason`) are
     supplied to reviewers so they are not re-raised in subsequent iterations.
   - The resolver disposition contract requiring every finding (critical, important, and nit) to receive a disposition
     of `applied` or `rejected` with a one-line reason (`not diff-grounded`, `inaccurate`, `out of scope`,
     `conflicting with a higher-severity finding`, or `no-convergence`).
4. In `apps/sandbox-executor/src/sandbox_executor/review_report.py`, add a frozen dataclass `FindingDisposition`
   (holding `id`, `severity`, `path`, `line`, `summary`, `recommendation`, `disposition`, `rejection_reason`, and
   `commit_sha`) and helper functions to serialise/deserialise finding payloads and render disposition summaries in the
   exit consensus report when budget exhaustion or `no-convergence` leaves open or rejected findings.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2` (Typing, docstrings, PEP 8),
  `holon-config/prompts/review_loop.template.md` (Australian English and backtick-wrapped formulas).
- **Potential failure modes for this step:**
  - Drift between `holon-config/prompts/review_loop.template.md` and `REVIEW_LOOP_TEMPLATE` in `scaffold.py` causing
    `test_prompt_templates.py` or `test_init.py` to fail.
- **Guardrails and early‑abort checks:**
  - Keep `holon-config/prompts/review_loop.template.md` and `scaffold.py`'s `REVIEW_LOOP_TEMPLATE` byte-identical in
    shared sections and verify with `npx prettier --check`.

### Success & Discard Criteria

- **Success:** `review_loop.py --help` displays the full checkpoint-continued resume semantics for `--iteration`,
  `review_report.py` exports the disposition data structures and serialisers, and `review_loop.template.md` matches
  `scaffold.py`.
- **Discard:** Cost exceeds `1.5 * 1.80 = 2.70` or template parity tests fail after two edits.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 1.80  |
| impact_pred         | 40.00 |
| cost_pred           | 1.80  |
| learning_value_pred | 2.50  |
| ev_pred             | 36.51 |

### Step Metrics Rationale

Routine schema, prompt template, and CLI help-text extensions across well-structured modules yield
`p_success_pred = 0.94` and low entropy `Delta_S = 1.80`. Using
`EV = 0.94 * 40.00 + 0.5 * 2.50 - 0.3 * 1.80 - 1.80 = 37.60 + 1.25 - 0.54 - 1.80 = 36.51`.

---

## Step 2: Implement containerised resolver pass and pre-push verification gate

- **Sub‑intent recommendation:** NO
- **Reasoning:** Reuses the existing `_dispatch_role` and `review_loop.py` workspace preparation machinery on a fresh
  clone of the execution branch; fits well within the single-plan entropy budget (`2.60`).
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Implement the in-container resolver execution path in `entrypoint/review_loop.py` and the
  host/sandbox dispatch seam in `flow.py` so `CHANGES_REQUESTED` findings are adjudicated to `applied` or `rejected`,
  verified with `uv run task lint` and `uv run task test`, committed with `Review-Finding-Ids` trailers, and pushed
  cleanly without force-pushing.
- **Git branch:** `I-1793869839-stage-4-resolver-review-loop-converges`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py`, add short helper functions
   (`_run_resolver_mode`, `_verify_before_push`, `_commit_and_push_resolution`) invoked when `--resolve` is passed:
   - Prepare a fresh clone/checkout of the target execution branch using `_prepare_workspace` and configure local git
     author identity.
   - Read the iteration's findings payload from `--findings-file` (containing `id`, `severity`, `path`, `line`,
     `summary`, `recommendation` for every finding, nits included).
   - Invoke the agent runner (or injected resolver callable in tests) with the resolver prompt instructing it to
     adjudicate every finding to either `applied` (with code changes in the working tree) or `rejected` (with a one-line
     reason from the allowed set: `not diff-grounded`, `inaccurate`, `out of scope`, or
     `conflicting with a higher-severity finding`) and write a structured JSON disposition block.
   - Ensure every input finding receives exactly one disposition; if any finding is omitted by the agent output, fail
     closed or mark its disposition explicitly so no finding is silently dropped.
   - When at least one finding is `applied` and the working tree has modifications: run `uv run task lint` and
     `uv run task test` from the repository root (`repo_dir`) before committing. If either command fails, record the
     failure reason and do not push broken changes.
   - Stage changes and commit to the execution branch with a commit message that includes the applied finding IDs and
     the standard RFC 2822 review trailers (`Action-Origin: pr_review`, `Review-Iteration: {iteration}`,
     `Review-Finding-Ids: {ids}`, `Review-Severity: {severity}`).
   - Push to `origin` with a fast-forward `git push origin {head_branch}` (unless `--skip-push` or `--dry-run` is set);
     never pass `--force`, `-f`, or `--force-with-lease`, and never run `git commit --amend` or `git rebase` that
     rewrites published history.
   - Write the resolver outcome JSON (including `dispositions`, `head_sha`, `pushed`, `lint_passed`, `tests_passed`, and
     any `error`) to `--result-file`.
2. In `apps/sandbox-executor/src/sandbox_executor/flow.py`, add `_dispatch_review_resolver` (mirroring
   `_dispatch_review_ensemble`) and an injectable `run_resolver_fn` parameter on `run_review_loop`:
   - Serialise the current iteration's merged `FindingRow` items (plus any infrastructure findings) and write them to a
     temporary JSON file or pass via the result-file transport into
     `_dispatch_role(context, "review-loop", container_args, result_file=result_file)`.
   - After a successful resolver push in container mode, fetch the updated `origin/{execution_branch}` in the host
     checkout and update `resolved_target.head_sha` so the next iteration reviews the new head in full.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` (Invariant 1 — Git discipline; Invariant 2 — Sandboxing),
  `holon-config/world/ruleset.md#3` (Tests via `uv run task test` / `uv run pytest` from repo root).
- **Potential failure modes for this step:**
  - Resolver agent leaves dirty untracked files or fails `uv run task lint` / `uv run task test`.
  - Git push rejected as non-fast-forward if history was altered.
- **Guardrails and early‑abort checks:**
  - Strictly forbid `--force` / `--force-with-lease` in git push arguments; verify return codes of `uv run task lint`
    and `uv run task test` before creating or pushing the commit.

### Success & Discard Criteria

- **Success:** Resolver mode in `review_loop.py` and `_dispatch_review_resolver` in `flow.py` adjudicate all findings,
  enforce lint and test gates before pushing, include finding IDs in commit messages, never force-push, and return
  structured dispositions via the result file.
- **Discard:** Cost exceeds `1.5 * 2.70 = 4.05` or `p_success` drops below `0.50`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 2.60  |
| impact_pred         | 80.00 |
| cost_pred           | 2.70  |
| learning_value_pred | 4.50  |
| ev_pred             | 69.17 |

### Step Metrics Rationale

This is the bottleneck step (`p_success_pred = 0.88`, `entropy_pred = 2.60`) because it coordinates subprocess execution
(`uv run task lint`, `uv run task test`, `git commit`, `git push`) and result-file transport across the container
boundary. High impact (`80.00`) and learning value (`4.50`) yield
`EV = 0.88 * 80.00 + 0.5 * 4.50 - 0.3 * 2.60 - 2.70 = 70.40 + 2.25 - 0.78 - 2.70 = 69.17`.

---

## Step 3: Wire multi-iteration convergence, ledger dispositions, 3-iteration guard, and exit-only posting in `flow.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Modifies `flow.py` directly where `run_review_loop` already owns the iteration loop, ledger writers,
  and consensus posting; keeping this in the same execution branch avoids cross-branch merge overhead.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Transform `run_review_loop` in `apps/sandbox-executor/src/sandbox_executor/flow.py` so it iterates
  across resolver passes until consensus approval or budget/no-convergence exit, populates ledger disposition fields,
  suppresses re-raising of rejected findings, enforces the 3-iteration unchanged-finding `no-convergence` guard, and
  posts consensus only once at exit.
- **Git branch:** `I-1793869839-stage-4-resolver-review-loop-converges`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. **Carry Adjudicated Rejections Across Iterations:**
   - Maintain a list/mapping of adjudicated rejected findings (`rejected_registry`) across iterations in
     `run_review_loop`, keyed by normalized `(severity, path, line, summary)`.
   - Pass `rejected_registry` into `_ensemble_iteration` / `build_review_request` (or the container transport) so
     reviewers see already-adjudicated rejected findings with their `rejection_reason`, and filter out any reviewer
     finding in iteration `N + 1` that matches an already-rejected finding from an earlier iteration so rejected
     findings are never re-raised.
2. **3-Iteration Unchanged-Finding No-Convergence Guard:**
   - Track the normalized open finding set signature (frozenset of `(severity, path, line, summary)`) and `head_sha` at
     each iteration.
   - When a non-empty finding set survives unchanged across 3 consecutive iterations with no diff change (identical
     `head_sha` across all 3 passes), adjudicate every finding in that set as `rejected` with
     `rejection_reason="no-convergence"`, record the `ACT-RES` disposition rows in `executions.jsonl`, log the
     `no-convergence` termination, and exit the loop to report/post the final outcome rather than spinning further.
3. **Resolver Dispatch & Ledger Provenance (`ACT-RES` / `ACT-FIND`):**
   - When an iteration's `ensemble_verdict` is `ConsensusVerdict.CHANGES_REQUESTED` (and the 3-iteration guard has not
     tripped and remaining iteration/time budgets allow):
     - Dispatch the resolver pass via `run_resolver_fn` (when injected or sandboxed) or `_dispatch_review_resolver`.
     - For every finding adjudicated by the resolver, append an `ACT-RES` action record via `record_review_action` to
       `holon-knowledge/ledger/executions.jsonl` populating `review_disposition` (`"applied"` or `"rejected"`),
       `rejection_reason` (the one-line reason when rejected, `None` when applied), `review_finding_ids`
       (`[finding_id]`), `review_severity`, `review_iteration`, and `introduced_by_action_id` (linking back to the
       `ACT-FIND` `action_id` that raised the finding).
     - If the resolver pushed a new commit (`new_head_sha != old_head_sha`), update `resolved_target` to the new
       `head_sha` and continue to the next iteration (re-reviewing the new head in full, as any push invalidates earlier
       votes).
4. **Budget Exhaustion & Exit-Only Consensus Posting:**
   - If the iteration budget (`last_label`) or per-iteration time budget is reached while findings remain unadjudicated,
     ensure every remaining open finding has its disposition recorded (or adjudicated on the final resolver/exit pass)
     and is included in the loop result's `dispositions` summary.
   - Keep `post_pr_review_consensus` strictly after the `while` loop so it executes at most once per `run_review_loop`
     invocation (either on unanimous `APPROVED` or at a non-approval exit with findings and dispositions), never on
     intermediate iterations.
   - Update `run_review_stage` error wording so a failed review stage accurately reports the convergence outcome (e.g.
     `no-convergence`, budget exhaustion with open findings/dispositions, or resolver failure) instead of claiming no
     automatic resolver exists.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md#3` (Append-only `executions.jsonl`), `docs/safety.md`
  (Invariant 4 — Human review is the final gate; no mid-loop pause).
- **Potential failure modes for this step:**
  - `ActionRecord.validate()` rejecting an invalid `review_disposition` or `review_severity`.
  - Infinite loop if `iteration` counter or `no-convergence` counter is not incremented monotonically.
- **Guardrails and early‑abort checks:**
  - Validate that `review_disposition` is strictly in `{"applied", "rejected"}` and `review_iteration >= 1` before
    calling `record_review_action`.
  - Enforce `iteration < last_label` and `StageTimer` checks on every pass.

### Success & Discard Criteria

- **Success:** `run_review_loop` iterates across `CHANGES_REQUESTED` passes by invoking the resolver, records
  `review_disposition`, `rejection_reason`, `review_finding_ids`, and `introduced_by_action_id` in `executions.jsonl`,
  suppresses re-raised rejected findings, triggers `rejected: no-convergence` after 3 identical passes with no diff
  change, and posts consensus only once at exit.
- **Discard:** Cost exceeds `1.5 * 2.60 = 3.90` or `p_success` drops below `0.50`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.89  |
| entropy_pred        | 2.40  |
| impact_pred         | 85.00 |
| cost_pred           | 2.60  |
| learning_value_pred | 4.20  |
| ev_pred             | 74.43 |

### Step Metrics Rationale

Core state-machine wiring in `flow.py` delivers the primary intent impact (`85.00`) with moderate complexity
(`entropy_pred = 2.40`, `p_success_pred = 0.89`). Using the EV formula:
`EV = 0.89 * 85.00 + 0.5 * 4.20 - 0.3 * 2.40 - 2.60 = 75.65 + 2.10 - 0.72 - 2.60 = 74.43`.

---

## Step 4: Add hermetic unit tests in `test_flow.py`, `test_review_loop.py`, and `test_review_report.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Hermetic unit tests in the three scoped test files validate Steps 1–3 directly without external
  dependencies.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add hermetic unit tests in `apps/sandbox-executor/tests/test_flow.py`,
  `apps/sandbox-executor/tests/test_review_loop.py`, and `apps/sandbox-executor/tests/test_review_report.py` covering
  resolver dispatch, disposition recording, non-re-raising of rejected findings, the 3-iteration `no-convergence` guard,
  single exit posting, budget exhaustion disposition recording, pre-push lint/test verification, no force-pushing, and
  `--iteration` help text documentation.
- **Git branch:** `I-1793869839-stage-4-resolver-review-loop-converges`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_review_report.py`:
   - Test `FindingDisposition` serialisation, deserialisation, and rendering of open/applied/rejected dispositions in
     the consensus report.
   - Verify that `holon-config/prompts/review_loop.template.md` and `REVIEW_LOOP_TEMPLATE` in `scaffold.py` remain
     synchronised and include the adjudicated-findings and `--iteration` resume semantics.
2. In `apps/sandbox-executor/tests/test_review_loop.py`:
   - Test that `review_loop.py --help` documents the checkpoint-continued `--iteration` resume semantics (engine-owned
     counter saved in flow checkpoint, monotonic across halts and resumes, never a loop limit, never operator-set).
   - Test `--resolve` mode on a hermetic local git checkout: verifies fresh clone/checkout preparation, running
     `uv run task lint` and `uv run task test` before pushing, committing applied findings with `Review-Finding-Ids` in
     the commit message, pushing without `--force` or history rewriting, and writing the structured dispositions to
     `--result-file`.
3. In `apps/sandbox-executor/tests/test_flow.py`:
   - Test that an iteration whose ensemble verdict is `CHANGES_REQUESTED` dispatches the resolver with that iteration's
     findings (including nits) and that the next iteration reviews the new pushed head SHA.
   - Test that every finding (critical, important, and nit) ends with exactly one recorded disposition (`applied` or
     `rejected` with a one-line reason) in `holon-knowledge/ledger/executions.jsonl` (`review_disposition`,
     `rejection_reason`, `review_finding_ids`, and `introduced_by_action_id` linking to the `ACT-FIND` `action_id`).
   - Test that rejected findings are passed to the next iteration's reviewers as already adjudicated and are not
     re-raised even if a mock reviewer attempts to emit the same finding again.
   - Test the 3-iteration `no-convergence` guard: when an unchanged finding set survives across 3 consecutive iterations
     with no diff change (`head_sha` unchanged), all findings in the set are adjudicated `rejected` with
     `rejection_reason="no-convergence"` and the loop exits cleanly without spinning further.
   - Test that consensus posting (`post_pr_review_consensus`) is called exactly once at loop exit (both on
     multi-iteration convergence to `APPROVED` and on budget exhaustion / `no-convergence` exit), never on intermediate
     iterations.
   - Test that iteration/time budget exhaustion records all open findings and their dispositions in the ledger and
     result payload.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3` (`pytest` only, test files explicitly declared in the plan),
  `apps/sandbox-executor/docs/hermetic_testing.md` (no real Docker, `gh`, git remotes, or LLM calls).
- **Potential failure modes for this step:**
  - Tests inadvertently invoking real `gh` or `docker` subprocesses if hermetic fixtures are bypassed.
- **Guardrails and early‑abort checks:**
  - Use existing hermetic fixtures (`hermetic_fixtures.py`, `review_fakes.py`, `gh_write_guard.py`) and mock
    subprocess/container seams.

### Success & Discard Criteria

- **Success:** All new and existing unit tests in `test_review_report.py`, `test_review_loop.py`, and `test_flow.py`
  pass hermetically via `uv run pytest`.
- **Discard:** Cost exceeds `1.5 * 1.60 = 2.40`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 1.40  |
| impact_pred         | 65.00 |
| cost_pred           | 1.60  |
| learning_value_pred | 3.50  |
| ev_pred             | 60.18 |

### Step Metrics Rationale

Hermetic testing using established fakes in `review_fakes.py` has high reliability (`p_success_pred = 0.93`) and low
entropy (`1.40`). Using the EV formula:
`EV = 0.93 * 65.00 + 0.5 * 3.50 - 0.3 * 1.40 - 1.60 = 60.45 + 1.75 - 0.42 - 1.60 = 60.18`.

---

## Step 5: Execute test suites and repository lint gates

- **Sub‑intent recommendation:** NO
- **Reasoning:** Final verification gate required by the repository ruleset and intent acceptance criteria.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Run the affected test files first, then the full unit test suite once, followed by code and
  documentation linters from the repository root.
- **Git branch:** `I-1793869839-stage-4-resolver-review-loop-converges`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run the affected test files first from the repository root:
   `uv run pytest apps/sandbox-executor/tests/test_review_report.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_flow.py`.
2. Run template and init parity tests to verify `scaffold.py` and `holon-config/prompts/review_loop.template.md`
   alignment:
   `uv run pytest apps/sandbox-executor/tests/test_prompt_templates.py apps/sandbox-executor/tests/test_init.py`.
3. Run the full unit test suite once from the repository root: `uv run task test`.
4. Run `uv run task lint` and `uv run task lint-docs` from the repository root and fix any formatting or complexity
   issues (ensuring all functions remain short and low in branching, and all markdown files pass Prettier).
5. Verify via `git status --short` and `git diff --name-only` that only files in the intent's `scope` array (plus
   `plans/`, `executions/`, and the three ledgers under `holon-knowledge/ledger/`) were modified.

### Dependencies & Criticality

- **Depends on:** Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `.agents/AGENTS.md` (Single root virtual environment, `uv run task test`, `uv run task lint`,
  `uv run task lint-docs`, touch-set discipline).
- **Potential failure modes for this step:**
  - Ruff complexity check (`C901`, `PLR0912`, `PLR0915`) or Prettier markdown check failing.
- **Guardrails and early‑abort checks:**
  - Decompose any helper function approaching complexity limits into small, single-purpose helpers before running the
    final gate.

### Success & Discard Criteria

- **Success:** `uv run pytest` on the scoped test files, `uv run task test`, `uv run task lint`, and
  `uv run task lint-docs` all exit `0` with zero warnings or untouched-file diffs.
- **Discard:** Cost exceeds `1.5 * 0.80 = 1.20`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.60  |
| impact_pred         | 50.00 |
| cost_pred           | 0.80  |
| learning_value_pred | 2.00  |
| ev_pred             | 48.02 |

### Step Metrics Rationale

Deterministic verification step with `p_success_pred = 0.96` and minimal entropy (`0.60`). Using the EV formula:
`EV = 0.96 * 50.00 + 0.5 * 2.00 - 0.3 * 0.60 - 0.80 = 48.00 + 1.00 - 0.18 - 0.80 = 48.02`.
