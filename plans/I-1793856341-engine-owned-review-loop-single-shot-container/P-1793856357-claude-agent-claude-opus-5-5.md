# Plan for I-1793856341-engine-owned-review-loop-single-shot-container

- **Plan ID:** P-1793856357-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-05T05:25:58.560Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A. One clarification is recorded instead of a reframe: today the loop in
  `run_review_loop` already breaks after the first pass on every verdict (no resolver runs), so the coupling bug is
  latent rather than observed as N passes. The fix is still worth it because the in-loop resolver the rationale mentions
  would make the latent coupling real, and because the engine does not yet resume its count from the checkpoint at all
  (it restarts at iteration 1 on every `--from-stage review`).
- **Exploration stance:** balanced. The change is small and well located (one shared loop function, one dispatcher, one
  entrypoint, one context dataclass), so most steps exploit. One short information-gathering step confirms where the
  container writes ledger rows and when the engine saves a checkpoint, because both decide where the label must flow.
- **Safety priority level:** standard
- **Priority Justification:** The change touches no credentials, sandbox boundary, merge gate or ledger schema. It keeps
  Bean 0034 (halt at approval, never merge) untouched, writes no ledger lines outside normal flow behaviour and stays
  inside the intent's five-file scope. `holon-config/world/constraints.md` §3 (ledger immutability) and
  `holon-config/world/ruleset.md` §3 (pytest, declared test changes) are respected by construction.

## Exploration

- **Proportion of steps that are exploratory:** 0.14 (1 of 7 steps is BALANCED information gathering; none are fully
  EXPLORATORY)
- **Justification:** The design is clear from reading the code, but two facts (container ledger destination and
  checkpoint timing on a failed Stage 4) cannot be fully confirmed without tracing; a cheap read-only step removes that
  uncertainty before any edit.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 6.2   |
| impact_pred         | 70.0  |
| cost_pred           | 13.5  |
| learning_value_pred | 4.0   |
| ev_pred             | 42.64 |

## Plan Description & Strategy

Current state (verified by reading the code on this branch):

- `run_review_loop` in `apps/sandbox-executor/src/sandbox_executor/flow.py` is one shared function used by three
  callers: the engine's Stage 4 (`run_review_stage`, host side), the container entrypoint (`entrypoint/review_loop.py`,
  inside the sandbox) and the local standalone CLI path (`run_review_loop_cli`). Its counter always starts at
  `iteration = 0` and its cap is `budget = max(1, max_iterations ...)`.
- On the host, each loop pass calls `_dispatch_review_ensemble(context, resolved_target, iteration, ensemble_size)`,
  which builds container arguments with `--max-iterations={iteration}`. That is the coupling to remove.
- Inside the container, the entrypoint passes `--max-iterations` straight into `run_review_loop`, whose own counter
  starts at 1 again. The ledger rows written there (`review_iteration`) and the consensus `iterations` value (which
  `review_report.py` renders as the `Iteration {n}` heading) therefore always say 1 inside the container, whatever the
  engine's pass is.
- `FlowContext` checkpoints `max_review_iterations` but no count of iterations already run, so a `--from-stage review`
  resume restarts at 1.

Target design (one loop, the engine's):

1. `run_review_loop` gains one keyword-only parameter, `first_iteration: int = 1`. The loop counts labels from
   `first_iteration` and runs at most `max_iterations` passes. Every existing caller keeps the default, so standalone
   behaviour is byte-for-byte the same.
2. `_dispatch_review_ensemble` always sends `--max-iterations=1` and a separate `--iteration={iteration}` label.
3. The entrypoint gains `--iteration` (default 1, must be at least 1), passed as `first_iteration`. `--max-iterations`
   keeps its standalone meaning and default 25.
4. `FlowContext` gains `review_iterations_done: int = 0`, checkpointed via `to_dict`/`from_dict` with a default so old
   checkpoints load unchanged (no schema bump). `run_review_stage` starts at `review_iterations_done + 1`, runs only the
   remaining engine budget, and records the last label back into the context, so the engine's existing checkpoint save
   after Stage 4 persists it. An exhausted budget fails the stage without dispatching.
5. The standalone `review` CLI command is left functionally unchanged; it is covered by a regression test that its
   container arguments still carry the user's `--max-iterations` and never carry `--iteration`.

No sub-intents: the whole change is about 6 entropy units, well under the 15.0 budget, and the steps share one small
code area where splitting would add merge overhead without reducing risk.

---

## Step 1: Confirm label destinations and checkpoint timing (read-only)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Cheap read-only tracing that removes the two remaining design uncertainties; no reuse value as a
  separate branch.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** (a) the container's ledger rows and its consensus `iterations` value are what reach the
  host's posted review body and ledger via the result file; (b) `PipelineEngine.run` saves the checkpoint after Stage 4
  whether the stage succeeds or fails, so a context field set at the end of `run_review_stage` is persisted.
- **Learning target:** exactly which values the host reads from the container result (only `reviewers` and the verdict
  via `deserialize_review_outcome`, or also `consensus`), and whether a failed review stage still checkpoints.
- **Maximum acceptable cost for this learning:** 1.5 cost units; stop reading once both answers are known.

### Intent & Git Integration

- **Step Intent:** Pin down where the iteration label must be honoured before editing anything.
- **Git branch:**
  I-1793856341-engine-owned-review-loop-single-shot-container/P-1793856357-claude-agent-claude-opus-5-5/E-{timestamp}-single-shot-review/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Read `deserialize_review_outcome` in `review_ensemble.py` (read only, not in scope) to list which result-file keys the
  host consumes.
- Read the posting path in `flow.py` (the code after the loop that builds `consensus_data` and posts) to confirm the
  host posts with its own `iteration` and the container posts nothing because of `--skip-post`.
- Read `PipelineEngine.run` (around the `save_checkpoint` calls near lines 5100 to 5165) to confirm the save happens
  after a FAILED review stage, and how `--from-stage review` re-enters `run_review_stage`.
- Record the findings in the execution record; if (b) is false, Step 4 adds an explicit save-friendly update before each
  return of `run_review_stage` rather than one at the end.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §1 (commit boundaries); intent TOUCH SET.
- **Potential failure modes for this step:** spending too long tracing unrelated Stage 4 code.
- **Guardrails and early‑abort checks:** no edits in this step; stop at the cost cap.

### Success & Discard Criteria

- **Success:** both hypotheses answered with file and line references in the execution record.
- **Discard:** cost exceeds 1.5 units; proceed with the defensive variant of Step 4 (update the count before every
  return).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.3   |
| impact_pred         | 10.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 4.0   |
| ev_pred             | 9.91  |

### Step Metrics Rationale

Read-only, so near-certain and almost no entropy. Learning value is the highest of the plan because it decides the shape
of Step 4. `EV = 0.95 * 10.0 + 0.5 * 4.0 - 0.3 * 0.3 - 1.5 = 9.50 + 2.00 - 0.09 - 1.5 = 9.91`.

---

## Step 2: Add a labelling start (`first_iteration`) to `run_review_loop`

- **Sub‑intent recommendation:** NO
- **Reasoning:** A small, additive keyword parameter on one function with a default that preserves every existing
  caller.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Separate "which pass is this" (label) from "how many passes may run" (budget) inside the shared loop.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `flow.py`, add a keyword-only parameter `first_iteration: int = 1` to `run_review_loop` and document it in the
  docstring as a label only (ledger `review_iteration`, finding IDs, trailers, `Iteration {n}` heading).
- Keep `budget` as the number of passes. Introduce `last_label = first_iteration + budget - 1` (or equivalent), start
  the counter at `first_iteration - 1`, and loop while the counter is below `last_label`. Every existing use of
  `iteration` inside the loop then carries the label without further edits.
- Keep `consensus_data["iterations"]` and `total_iterations` reporting the last label reached (this is what the heading
  renders), and keep `max_review_iterations`/`max_iterations` reporting the pass budget, so standalone output is
  unchanged when `first_iteration` is 1.
- Adjust the "Iteration budget exhausted" condition to compare against the last label, and the dry-run `not_measured`
  check (`iteration > 0`) to compare against `first_iteration - 1`, so a labelled run with no pass is not mistaken for
  one that ran.
- Clamp `first_iteration` to at least 1 with `max(1, ...)` to keep branching low; do not add new noqa codes beyond the
  existing ones on the function.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (Steps 3, 4 and 6 build on it)

### Safety & Constraint Considerations

- **Relevant rules:** ruleset §2 (typing, docstrings); intent constraint "keep functions short and low in branching".
- **Potential failure modes for this step:** off-by-one in the loop bound; the "budget exhausted" log firing wrongly; a
  ruff complexity rule tripping.
- **Guardrails and early‑abort checks:** run
  `uv run pytest apps/sandbox-executor/tests/test_flow.py apps/sandbox-executor/tests/test_review_loop.py` right after
  the edit; all existing tests must still pass unchanged because the default is 1.

### Success & Discard Criteria

- **Success:** existing flow and review-loop tests pass untouched; with `first_iteration=4, max_iterations=1` exactly
  one pass runs and every label reads 4.
- **Discard:** existing tests need assertion changes to pass (signals a behaviour change for standalone callers); stop
  and re-plan.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.2   |
| impact_pred         | 20.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 15.74 |

### Step Metrics Rationale

Small edit in a long, heavily tested function; the main risk is off-by-one. `EV = 0.88 _ 20.0 + 0.5 _ 2.0 - 0.3 \* 1.2

- 2.5 = 17.60 + 1.00 - 0.36 - 2.5 = 15.74`.

---

## Step 3: Single-shot dispatch and the container's `--iteration` label flag

- **Sub‑intent recommendation:** NO
- **Reasoning:** Two tiny, tightly coupled edits (dispatcher arguments and entrypoint parser) that must land together.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The engine requests exactly one ensemble pass per dispatch and passes its iteration only as a label.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `_dispatch_review_ensemble` (`flow.py`), replace `--max-iterations={iteration}` with a constant
  `--max-iterations=1` and add `--iteration={iteration}`. Name the constant (for example
  `_ENGINE_PASSES_PER_DISPATCH = 1`) with a one-line comment that the engine owns the loop.
- In `entrypoint/review_loop.py`, add `--iteration` (type int, default 1, help text in Australian English: the label
  used for ledger `review_iteration` fields and the posted `Iteration {n}` heading, never a loop limit).
- Validate `--iteration` is at least 1 next to any existing argument validation (exit non-zero with a clear message
  otherwise); keep it one short guard.
- Pass `first_iteration=args.iteration` into `run_review_loop`; leave `max_iterations=args.max_iterations` and
  `max_review_iterations=args.max_iterations` as they are.
- Leave the summary print (`Iterations : x / y`) as is; with a label of N and one pass it prints `N / 1`, which is
  accurate (label of the pass, passes allowed).

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES (acceptance criteria 1 and 2)

### Safety & Constraint Considerations

- **Relevant rules:** intent TOUCH SET; `docs/safety.md` sandboxing (the role container contract is unchanged apart from
  one new optional flag).
- **Potential failure modes for this step:** the role dispatcher shell (`entrypoint/role_dispatcher.sh`, out of scope)
  filters unknown flags; an older image without `--iteration` would reject it.
- **Guardrails and early‑abort checks:** Step 1 or a quick read of `role_dispatcher.sh` confirms arguments pass through
  verbatim; if it filters, stop and report, since that file is outside the touch set.

### Success & Discard Criteria

- **Success:** the dispatched argument list contains `--max-iterations=1` and `--iteration=N` for any N, and never
  `--max-iterations=N` for N greater than 1.
- **Discard:** the dispatcher shell drops the new flag (requires an out-of-scope change; defer to a follow-up intent).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.0   |
| impact_pred         | 25.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 20.95 |

### Step Metrics Rationale

Directly delivers the headline acceptance criteria with minimal code. Residual risk is the out-of-scope shell
dispatcher. `EV = 0.90 * 25.0 + 0.5 * 1.5 - 0.3 * 1.0 - 2.0 = 22.50 + 0.75 - 0.30 - 2.0 = 20.95`.

---

## Step 4: Checkpoint the engine's review count and resume from it

- **Sub‑intent recommendation:** NO
- **Reasoning:** Moderately risky (touches checkpoint serialisation) but small; it shares the same files and tests, so a
  separate branch adds overhead without isolation benefit.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the engine's iteration budget the sole Stage 4 cap and continue its count across a
  `--from-stage review` resume.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add `review_iterations_done: int = 0` to `FlowContext` with a short comment (engine-owned Stage 4 count, persisted so
  a resume continues it). Serialise it in `to_dict` and read it in `from_dict` with `int(data.get(...) or 0)`, so older
  checkpoints load as 0 and no schema version bump or migration is needed.
- Do not copy it into `_child_context`: a child intent has its own Stage 4 and must start at 1.
- Add a small helper (for example `_review_window(context) -> tuple[int, int]`) returning the first label (`done + 1`)
  and the remaining pass budget (`max_review_iterations - done`).
- In `run_review_stage`, call the helper. If the remaining budget is 0 or less, fail the stage through the existing
  `_stage_failure` path with a clear message (budget exhausted after N iterations) and dispatch nothing. Otherwise call
  `run_review_loop` with `max_iterations=remaining` and `first_iteration=first`.
- After the loop, set `context.review_iterations_done` to the larger of its current value and the loop's
  `total_iterations` (the last label reached); a run that refused before any pass leaves the count unchanged.
- Keep the recorded `max_review_iterations` in the stage payload equal to the engine's configured budget (the existing
  test at `test_flow.py` line ~1080 asserts 7 for a configured 7), so pass `max_review_iterations` through unchanged in
  the payload rather than the remaining count; adjust only where the payload is built if the loop's own value differs.
- If Step 1 found that a failed Stage 4 is checkpointed only in some paths, set the count before each return instead of
  once at the end.

### Dependencies & Criticality

- **Depends on:** Steps 1 and 2
- **Is Bottleneck:** YES (acceptance criterion 4); it is the lowest-probability step

### Safety & Constraint Considerations

- **Relevant rules:** constraints §3 (no ledger rewriting; this changes only the checkpoint file under `.holon/flow/`,
  not a ledger); ruleset §2 typing and docstrings.
- **Potential failure modes for this step:** the payload's `max_review_iterations` silently changes to the remaining
  budget; a checkpoint round-trip test asserting the exact `to_dict` key set breaks; the count is never persisted
  because the checkpoint is saved before the field is set.
- **Guardrails and early‑abort checks:** run the checkpoint and review-stage tests in `test_flow.py` after the edit; if
  a test enumerates `to_dict` keys exactly, extend that test in Step 6 (declared change) rather than weakening it.

### Success & Discard Criteria

- **Success:** a context with `review_iterations_done=3` and budget 25 resumes Stage 4 with first label 4 and a
  remaining budget of 22; the checkpoint written afterwards holds the new count; an old checkpoint without the key loads
  as 0.
- **Discard:** persisting the count requires a checkpoint schema version bump or a migration transform (scope and risk
  growth); stop and re-plan.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 1.8   |
| impact_pred         | 20.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 13.96 |

### Step Metrics Rationale

Highest entropy of the plan because it changes persisted state and interacts with engine resume logic; still additive
with safe defaults. `EV = 0.80 * 20.0 + 0.5 * 3.0 - 0.3 * 1.8 - 3.0 = 16.00 + 1.50 - 0.54 - 3.0 = 13.96`.

---

## Step 5: Keep the standalone `review` command's loop untouched

- **Sub‑intent recommendation:** NO
- **Reasoning:** Verification of non-change; trivial.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Guarantee the standalone `review` command keeps its own multi-iteration loop and `--max-iterations`
  meaning.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Confirm `cli/commands/review.py` `_build_container_review_args` still forwards the user's `--max-iterations` and does
  not add `--iteration` (the container default of 1 applies).
- Confirm `run_review_loop_cli` (local path) does not pass `first_iteration`.
- Make no functional edit to `review.py`. At most, tighten the `--max-iterations` help text to say it caps the
  standalone loop (Australian English), only if it reads ambiguously next to the new container flag.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent acceptance criterion 3.
- **Potential failure modes for this step:** an accidental behaviour change through a shared helper.
- **Guardrails and early‑abort checks:** the regression test in Step 6 pins the argument list.

### Success & Discard Criteria

- **Success:** `git diff` shows no logic change in `review.py`; existing CLI tests pass.
- **Discard:** N/A (a non-change cannot fail beyond a test regression, which returns to Step 2 or 3).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.3   |
| impact_pred         | 5.0   |
| cost_pred           | 0.5   |
| learning_value_pred | 0.5   |
| ev_pred             | 4.41  |

### Step Metrics Rationale

Almost no work; value is protection of an acceptance criterion. `EV = 0.95 _ 5.0 + 0.5 _ 0.5 - 0.3 \* 0.3 - 0.5 = 4.75

- 0.25 - 0.09 - 0.5 = 4.41`.

---

## Step 6: Add focused pytest coverage

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tests belong with the change they pin; no external effects.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Pin all four behaviours the intent asks for, without real Docker, gh, git remotes or LLM calls.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `tests/test_flow.py` (declared test change):
  - Single-shot dispatch: patch `_dispatch_role` to capture the argument list and write a minimal result file; call
    `_dispatch_review_ensemble` with iteration values 1, 4 and 25 (parametrised); assert `--max-iterations=1` and
    `--iteration=<n>` each appear exactly once and no other `--max-iterations` value appears.
  - Label flow on the host: with `reviewers_sandboxed` patched false and `_dispatch_review_ensemble` patched to return
    one fake reviewer, run `run_review_loop` with `first_iteration=4, max_iterations=1` (gh, git and mergeability probes
    patched as the neighbouring tests already do) and assert the ledger rows' `review_iteration` is 4, the dispatcher
    received 4, and `consensus["iterations"]` is 4.
  - Resume: build a `FlowContext` with `review_iterations_done=3`, `max_review_iterations=5`, round-trip it through
    `save_checkpoint`/`load_checkpoint`, run `run_review_stage` with `run_review_loop` patched to record its keyword
    arguments and return one pass, and assert `first_iteration=4`, `max_iterations=2` and the saved count becomes 4. Add
    a case with `review_iterations_done=5` asserting the stage fails without calling the loop.
  - Back-compatibility: an old checkpoint dict without the new key loads with `review_iterations_done == 0`; a child
    context starts at 0.
- In `tests/test_review_loop.py` (declared test change):
  - Entrypoint: parse `--iteration=7 --max-iterations=1` and assert `run_review_loop` (patched) receives
    `first_iteration=7, max_iterations=1`; without `--iteration` it receives `first_iteration=1` and the default 25.
  - Posted body: feed a consensus with `iterations` equal to the label into the existing report renderer used by
    neighbouring tests and assert the `Iteration 7` heading.
  - Standalone regression: `_build_container_review_args` with `--max-iterations=5` still yields `--max-iterations=5`
    and no `--iteration`; a standalone `run_review_loop` with `max_iterations=3` and default `first_iteration` keeps
    today's labels starting at 1.
- Reuse `review_fakes.py` and `hermetic_fixtures.py` helpers already imported by these files; add no new fixtures files.

### Dependencies & Criticality

- **Depends on:** Steps 2, 3, 4, 5
- **Is Bottleneck:** NO (failures feed back to the implementation steps)

### Safety & Constraint Considerations

- **Relevant rules:** ruleset §3 (pytest only, tests declared in the plan); intent item 4 (no real Docker, gh, remotes
  or LLM calls); `tests/gh_write_guard.py` and the hermetic guard already enforce this.
- **Potential failure modes for this step:** a test accidentally reaching a real `gh` or Docker; brittle assertions on
  whole argument lists.
- **Guardrails and early‑abort checks:** assert on presence of specific flags rather than full list equality; rely on
  the existing hermetic guard fixtures to fail loudly on any external call.

### Success & Discard Criteria

- **Success:** new tests fail on the pre-change code (verify by reasoning or by stashing locally) and pass after it.
- **Discard:** a test needs a real external tool to pass; replace it with a patch or drop it and note why.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.2   |
| impact_pred         | 15.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 1.5   |
| ev_pred             | 10.64 |

### Step Metrics Rationale

`run_review_loop` has many probes to patch, which is the main cost; the neighbouring tests already show the pattern.
`EV = 0.85 * 15.0 + 0.5 * 1.5 - 0.3 * 1.2 - 2.5 = 12.75 + 0.75 - 0.36 - 2.5 = 10.64`.

---

## Step 7: Verify, lint and record the execution

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard closing gate.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the acceptance criteria with the project's own commands and record the run.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- From the repository root, run the affected tests first:
  `uv run pytest apps/sandbox-executor/tests/test_flow.py apps/sandbox-executor/tests/test_review_loop.py`.
- Then run the full suite once with `uv run task test` (no repetition: the intent sets no timing target). Report the
  pass/fail count and pytest-reported time.
- Run `uv run task lint` and `uv run task lint-docs`; fix only files in the touch set (the plan and execution markdown
  included).
- Write the execution record under `executions/` and append ledger rows through the normal flow only; never edit
  existing ledger lines. Never set `PYTHONPATH`.

### Dependencies & Criticality

- **Depends on:** Step 6
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** constraints §3 (append-only ledgers); intent constraint on test commands.
- **Potential failure modes for this step:** unrelated pre-existing failures in the full suite; prettier reformatting
  the plan file.
- **Guardrails and early‑abort checks:** compare any full-suite failure against `main`; report pre-existing failures
  rather than editing out-of-scope files.

### Success & Discard Criteria

- **Success:** affected tests and full suite pass; both lint tasks pass.
- **Discard:** a failure needs an out-of-scope file change; stop and report it as a follow-up.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.4   |
| impact_pred         | 5.0   |
| cost_pred           | 1.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 3.38  |

### Step Metrics Rationale

Routine gate; the risk is environmental rather than design.
`EV = 0.90 * 5.0 + 0.5 * 1.0 - 0.3 * 0.4 - 1.5 = 4.50 + 0.50 - 0.12 - 1.5 = 3.38`.

---

### Strategy Rationale

The intent is accepted as is: it is well scoped, its five files contain every touch point, and it removes a real (though
currently latent) coupling while adding a resume capability the engine lacks today. The design reuses the one shared
loop rather than forking a host-only variant: a single keyword-only `first_iteration` label separates labelling from the
pass budget, so the container honours the engine's number in its ledger rows and in the consensus `iterations` value
that `review_report.py` already renders as the heading, with no change to the out-of-scope report module.

Aggregation:

- `entropy_pred` is the sum of step entropies: `0.3 + 1.2 + 1.0 + 1.8 + 0.3 + 1.2 + 0.4 = 6.2`.
- `cost_pred` is the sum of step costs: `1.5 + 2.5 + 2.0 + 3.0 + 0.5 + 2.5 + 1.5 = 13.5`.
- `p_success_pred` is a qualitative judgement bounded by the bottleneck Step 4 (0.80). The naive product
  `0.95 * 0.88 * 0.90 * 0.80 * 0.95 * 0.85 * 0.90 = 0.44` is too pessimistic because step failures are correlated and
  cheaply retryable within one execution branch (tests point straight at the faulty step).
- `impact_pred` 70 is a qualitative judgement below the raw step sum (110), because Steps 1, 5 and 7 enable rather than
  add impact.
- `learning_value_pred` 4.0 is the maximum step value (Step 1).
- `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost` with `mu = 0.5` and `lambda = 0.3`
  from `holon-config/metrics/ev_config.json`:
  `EV = 0.80 * 70.0 + 0.5 * 4.0 - 0.3 * 6.2 - 13.5 = 56.00 + 2.00 - 1.86 - 13.5 = 42.64`.

Strong assumptions: the role dispatcher shell forwards container arguments verbatim (checked in Step 3), and adding an
optional key to the checkpoint needs no schema bump because `from_dict` already tolerates missing keys.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1: work only on this plan's execution branch; never commit to `main`.
  - `holon-config/world/constraints.md` §3: the three ledgers stay append-only; the new count lives in the flow
    checkpoint, not a ledger.
  - `holon-config/world/ruleset.md` §2: typed signatures and docstrings for the new parameter, field and helper.
  - `holon-config/world/ruleset.md` §3: pytest only; the test changes in Step 6 are declared here.
  - `docs/safety.md` §5 and Bean 0034: Stage 4 still halts at approval and never merges.
  - Intent constraints: Australian English, no new dependencies, short low-branching functions, `uv run` from the
    repository root without `PYTHONPATH`, touch set limited to the five scoped files plus plans, executions and ledgers.
- **Potential violations or edge cases:**
  - The shell role dispatcher (out of scope) could filter the new `--iteration` flag.
  - An old container image without `--iteration` would reject the flag until rebuilt.
  - A checkpoint written mid-loop by a crash would not hold the in-flight pass, so that pass is re-run under the same
    label on resume (acceptable: no ledger line is rewritten, a new row is appended).
- **Mitigations built into the plan:** Step 1 and Step 3 check the dispatcher; defaults keep every existing caller's
  behaviour; the checkpoint key is optional with a 0 default; tests are hermetic via existing guards.
- **Residual risk accepted (and why):** image staleness for `--iteration` is accepted because images are rebuilt from
  the same source in the normal flow; a re-run pass after a crash is accepted because the ledgers stay append-only and
  the engine budget still caps the total.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 6.2
- **Budget Compliance:** The strategy fits within budget, so no sub-intent list is used.

## Sub-intent list

None
