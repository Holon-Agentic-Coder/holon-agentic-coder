# Plan for I-1792765424-incremental-artifacts-budgets-and-local-llm-envelope

- **Plan ID:** P-1792788153-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-23T20:42:34.460Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS (with internal decomposition into ten ordered steps; one step recommended as a
  sub-intent if trust allows)
- **Reframed intent (if applicable):** Not reframed. Two clarifications are recorded instead of a reframe: (a) the
  hard-coded `timeout=300` in `flow.py` (`run_execute_stage`, line ~2374) guards the post-execution `pytest`
  verification subprocess, not an agent turn, so it is replaced by the execute-stage budget remainder rather than by the
  turn budget; (b) no `holon doctor` command exists today, so a minimal `doctor` sub-command is created in
  `sandbox_executor/cli.py`.
- **Exploration stance:** balanced. Beans 0138 and 0140 are mostly exploit work on well-understood code
  (`entrypoint/planner.py`, `local_llm.py`), while Bean 0139 introduces streamed-output stall detection and process
  group termination, which are new mechanisms in this repository and justify one bounded exploratory spike.
- **Safety priority level:** elevated
- **Priority Justification:** The plan touches the append-only ledger (`holon-config/world/constraints.md` §3 Ledger
  Immutability; `docs/safety.md` Invariant 6) through the new ACT-BUDGET action record, and adds process-group
  termination of sandbox processes (`constraints.md` §2 Escape Actions: no unwhitelisted subprocesses). It also changes
  the planner and executor prompts, which govern how every future agent produces artefacts. None of these is a core
  invariant change, so `critical` is not warranted, but each needs explicit guardrails.

## Exploration

- **Proportion of steps that are exploratory:** 0.2 (2 of 10 steps: Step 1 baseline reconnaissance and Step 5 streamed
  turn supervision are BALANCED/EXPLORATORY; the rest are EXPLOIT)
- **Justification:** The unknowns are concentrated in how agent CLIs (pi, claude, antigravity) stream output and report
  truncation (`stopReason: length`, exit code 1) inside the container. Learning these behaviours reduces future NOV for
  every runner-related intent, so a bounded spike is worth its cost; everything else follows existing patterns
  (`append_action_record`, `LedgerLock`, `LocalLLMConfigError`, `parse_metrics`).

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.66  |
| entropy_pred        | 14.6  |
| impact_pred         | 92.0  |
| cost_pred           | 19.5  |
| learning_value_pred | 6.0   |
| ev_pred             | 39.84 |

### Strategy Rationale

The three beans are coupled through a single failure story: a long authoring turn on a local model (Bean 0140 envelope)
runs out of output tokens or wall clock (Bean 0139), and because the artefact is only emitted in the final stdout
message, everything is lost (Bean 0138). Fixing them in the order 0138, then 0140, then 0139 would leave the budgets
without a salvage target, so the plan builds the shared artefact validator first (Step 2), then the pure budget core
(Step 4), then wires supervision into the runner (Step 5), and treats the local-LLM envelope (Steps 7 and 8) as an
independent track that can proceed in parallel.

Aggregation method:

- `entropy_pred` is the sum of step entropies: `0.3 + 2.2 + 1.8 + 1.4 + 3.2 + 1.8 + 1.3 + 1.5 + 0.6 + 0.5 = 14.6`, which
  is inside the 16.0 budget.
- `cost_pred` is the sum of step costs: `0.5 + 3.0 + 2.5 + 1.5 + 4.5 + 2.0 + 1.5 + 2.0 + 0.8 + 1.2 = 19.5`. The scale is
  calibrated to the costs recorded in `holon-knowledge/ledger/plans.jsonl` (2.5 to 14 for single-concern intents); this
  intent is roughly three of those.
- `p_success_pred` is a qualitative bottleneck judgement, not a product. The bottleneck is Step 5 (`0.68`); steps are
  positively correlated (same author, same test harness, review loop can repair lint or test regressions), so a naive
  product (`~0.15`) badly understates success. Applying a 0.97 correlation discount to the bottleneck gives
  `0.68 * 0.97 = 0.66`.
- `impact_pred` is a qualitative cap rather than a sum (step impacts sum to 124 but overlap heavily): the intent removes
  the dominant cause of lost plan runs on local models and gives operators a diagnosable envelope, so `92.0`.
- `learning_value_pred = 6.0` reflects the spike in Step 5 plus the first real measurements of turn and stage elapsed
  times recorded in the ledger.
- `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
- `EV = 0.66 * 92.0 + 0.5 * 6.0 - 0.3 * 14.6 - 19.5 = 60.72 + 3.00 - 4.38 - 19.5 = 39.84`

Strong assumptions: (1) the agent CLIs flush incremental output to stdout or stderr often enough that a 90 second
silence is a real stall signal (validated in Step 1, guarded in Step 5 by treating file growth of the target artefact as
liveness too); (2) the in-container entrypoints are the right place to enforce turn and stage budgets, because `HOLON_*`
variables are already forwarded by `cli.py` into `docker run`; (3) the per-stage defaults (10/35/45/15/10 minutes, 300 s
turn, 90 s stall) come from the real measurements recorded in the beans as the intent states, and are never re-derived
from synthetic data.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1 Git Flow: work stays on plan and execution branches under
    `I-1792765424-incremental-artifacts-budgets-and-local-llm-envelope/`; nothing is committed to `main`.
  - `holon-config/world/constraints.md` §2 Sandbox Containment: predicted entropy 14.6 selects the Container Sandbox;
    process-group termination must only signal processes the runner itself spawned.
  - `holon-config/world/constraints.md` §3 and `docs/safety.md` Invariant 6: ledgers are append-only; the ACT-BUDGET
    record is appended via `append_action_record()` under `LedgerLock`; no new ledger files (intent constraint: only
    `intents.jsonl`, `plans.jsonl`, `executions.jsonl`).
  - `holon-config/world/ruleset.md` §1: Python `==3.13.*`, `uv` workspace, no new dependencies (health probe uses
    `urllib.request` from the standard library).
  - `holon-config/world/ruleset.md` §2: type annotations and docstrings on all new public functions.
  - `holon-config/world/ruleset.md` §3: tests in `apps/sandbox-executor/tests/` matching source modules; test file
    changes are declared explicitly in Steps 2 to 8.
  - `docs/safety.md` Human review boundaries: this is a root intent, so merge to `main` is human-gated.
  - Intent constraint 6: Australian English, `uv run pytest` only, `uv run ruff check` and `ruff format --check`,
    prettier with `.prettierrc.json` (`printWidth 120`, `proseWrap always`).
- **Potential violations or edge cases:**
  - Killing a process group that includes the entrypoint itself (if the agent is not started in a new session) would
    abort the salvage path.
  - Appending ACT-BUDGET records from both the resume attempt and the final failure would double-count.
  - Prompt changes alter the behaviour of every future planner, including calibration plans (`*_calibration.md`).
  - The stdout fallback could silently mask agents that never write files, hiding regressions.
  - `HOLON_REASONING_MODE` values differ by provider; an unknown value could be forwarded verbatim into `models.json`.
  - The gateway probe must never run during tests (intent: no live gateway call) and must never leave loopback or the
    allow list.
- **Mitigations built into the plan:**
  - Agent turns launched with `start_new_session=True`; termination uses `os.killpg` on the child's own pgid after
    asserting it differs from `os.getpgid(0)`; SIGTERM, grace period, then SIGKILL.
  - A single helper owns the ACT-BUDGET write, called exactly once per stage failure after the resume turn fails; tests
    assert one record.
  - Stdout fallback logs `WARNING` and the plan ledger entry is not altered; Step 10 checks the log line.
  - Reasoning mode validated against an allow list (`off`, `minimal`, `low`, `medium`, `high`); invalid values raise
    `LocalLLMConfigError`.
  - Health probe takes an injected fetcher; default fetcher only targets the already-rewritten gateway authority or
    loopback on the host side, with a 2 s timeout.
- **Residual risk accepted (and why):** Stall detection may produce false positives for agents that buffer all output
  until the end of a turn. This is accepted because file growth of the artefact also counts as liveness, the stall
  threshold is configurable, and one resume turn is always attempted before failure is recorded.
- **Allocated Entropy Budget:** 16.0
- **Predicted Plan Entropy:** 14.6
- **Budget Compliance:** The strategy fits within budget (91 per cent utilisation). If Step 5 is promoted to a
  sub-intent its share (3.2) must fit within the baseline sub-intent fraction `16.0 * 0.3 = 4.8`, which it does.

## Plan Description & Strategy

Current-state findings that shape the plan:

- `apps/sandbox-executor/src/sandbox_executor/entrypoint/planner.py` already prefers a directly written plan file, but
  only when `len >= 50` and it contains `# Plan` or `| metric |`; it uses blocking `subprocess.run(cmd)` with no
  timeout, silently falls back to stdout, exits on non-zero codes even when a valid file exists, and back-fills missing
  metrics with defaults (`p_success 0.7` and so on), which hides an invalid plan instead of failing validation.
- The final prompt suffix in `planner.py` explicitly says "Output the complete markdown plan directly", which is the
  opposite of Bean 0138.
- `entrypoint/executor.py` launches the agent via `subprocess.run` (lines ~572 and ~614) with no timeout; execution
  notes live under `executions/E-*.md`.
- `ledger.py` already provides `ActionRecord`, `append_action_record()` and `LedgerLock` (Bean 0127), so ACT-BUDGET
  needs no new storage code.
- `local_llm.py` builds `models.json` without `maxTokens` or reasoning fields; `_synthesized_config` emits bare
  `{"id": ...}` entries.
- `cli.py` forwards every `HOLON_*` environment variable into `docker run`, so new budget variables reach the
  in-container entrypoints without wiring changes. There is no `doctor` sub-command.
- The bean files (0138 to 0140) are not present in this repository snapshot; the defaults are taken verbatim from the
  intent, which states they come from the beans' real measurements.

Ordering: Step 1 (recon) → Step 2 (artefact validator and planner) → Step 3 (executor artefact) → Step 4 (budget core) →
Step 5 (turn supervision, salvage, resume, ACT-BUDGET) → Step 6 (stage ceilings, summary lines, `flow.py` timeout).
Steps 7 and 8 (local-LLM envelope and doctor) depend only on Step 1 and can run in parallel. Step 9 (docs) and Step 10
(verification gate) close the plan.

---

## Step 1: Baseline reconnaissance of runner output, truncation signals and test harness

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only investigation that de-risks Steps 2, 5 and 8 cheaply; no code changes and no branch value.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Each runner in `agent_runner.py` emits some stdout or stderr at least every 90 seconds
  during a healthy turn, and a truncated final message surfaces as `stopReason: length` and/or exit code 1 while the
  plan file is already complete.
- **Learning target:** Exact truncation signatures per runner (pi, claude, antigravity), whether pi can stream JSON
  events, which fixtures in `tests/hermetic_fixtures.py` can be reused, and current test and lint baseline.
- **Maximum acceptable cost for this learning:** 0.5 cost units (about one short session); stop reading once each runner
  is classified.

### Intent & Git Integration

- **Step Intent:** Establish a factual baseline so later steps do not guess at runner behaviour.
- **Git branch:**
  I-1792765424-incremental-artifacts-budgets-and-local-llm-envelope/P-1792788153-claude-agent-claude-opus-5-5/E-{timestamp}-baseline/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run `uv run pytest -q` and `uv run ruff check` plus `uv run ruff format --check` in `apps/sandbox-executor` and
   record pass counts as the baseline.
2. Read `agent_runner.py` `build_cmd` implementations to list flags that control streaming (for example JSON or stream
   modes) and how each runner reports its stop reason.
3. Read `tests/test_planner.py`, `tests/test_planner_integration.py`, `tests/test_local_llm.py`, `tests/test_flow.py`
   and `tests/hermetic_fixtures.py` to identify existing fakes for `subprocess.run` and the repository clone.
4. Read `entrypoint/executor.py` to find where the execution notes `executions/E-*.md` are produced and how the executor
   decides success.
5. Record findings in the execution notes for this branch only (no ledger change).

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` Sandboxing model; intent constraint 6 (tests via `uv run pytest`).
- **Potential failure modes for this step:** Baseline tests already failing; runner binaries not available offline.
- **Guardrails and early‑abort checks:** If the baseline is red, record failing tests and exclude them from the
  regression comparison rather than fixing them in this intent.

### Success & Discard Criteria

- **Success:** Baseline counts recorded; each runner classified as streaming or buffered with its truncation signature.
- **Discard:** Abort the step after 1.5x cost; proceed with the conservative assumption that all runners buffer.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.3   |
| impact_pred         | 5.0   |
| cost_pred           | 0.5   |
| learning_value_pred | 4.0   |
| ev_pred             | 6.16  |

### Step Metrics Rationale

Read-only, so entropy is near zero. Learning value is moderate because it directly sets the stall-detection design.
`EV = 0.95 * 5.0 + 0.5 * 4.0 - 0.3 * 0.3 - 0.5 = 4.75 + 2.00 - 0.09 - 0.5 = 6.16`.

---

## Step 2: Incremental plan artefact — prompt change, shared validator, planner file preference and targeted re-prompt (Bean 0138)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Medium-sized and central, but tightly coupled to Step 3 through the shared validator; keeping it on the
  plan's execution branch avoids a merge chain for a single module.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the plan file written by the agent the source of truth, validate it structurally and numerically
  before commit, and re-prompt once for only the missing sections.
- **Git branch:**
  I-1792765424-incremental-artifacts-budgets-and-local-llm-envelope/P-1792788153-claude-agent-claude-opus-5-5/E-{timestamp}-incremental-artifacts/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. New module `sandbox_executor/artefacts.py` (Australian spelling in the module name and docstrings) with:
   - `ArtefactSpec` dataclass: required heading patterns, size floor in bytes, whether a numeric metrics table is
     required.
   - `PLAN_SPEC` requiring `# Plan for`, `## Overall Plan Metrics`, at least one `## Step`, and `### Strategy Rationale`
     (or the justification heading), size floor 800 bytes.
   - `validate_artefact(path, spec) -> ArtefactReport` returning `exists`, `size`, `missing_sections`, `metrics_ok` and
     parsed metrics; metrics validity reuses `parse_metrics` and requires all six overall metrics to be numeric (parse
     only the first metrics table, i.e. the overall table).
   - `build_reprompt(missing_sections, path) -> str` that lists only the missing section names and instructs the agent
     to append them to the existing file with its write or edit tool.
2. `holon-config/prompts/planner.template.md`: add a "Write protocol" block instructing the agent to create
   `plans/P-{timestamp}-{agent}-{safe_model}.md` with its file-write tool immediately, write the header and overall
   metrics table first, then append each step, then the justification and safety sections, and to keep the final chat
   message short.
3. `entrypoint/planner.py`:
   - Replace the closing "Output the complete markdown plan directly" suffix with an instruction to write the file
     incrementally and reply with a one-line confirmation.
   - Run the agent through the turn supervisor from Step 5 when available (a thin `run_turn` seam; before Step 5 lands
     it wraps `subprocess.run`).
   - After the turn, call `validate_artefact`. If the file exists, exceeds the floor and `metrics_ok` is true, treat a
     non-zero exit or `stopReason: length` as informational (log at INFO) and continue.
   - If sections are missing but metrics are valid, issue exactly one re-prompt turn with `build_reprompt`, then
     re-validate.
   - Only when no usable file exists, fall back to stdout extraction (existing fenced-markdown logic) and log a
     `WARNING` naming the fallback.
   - Remove the silent metric defaults: missing or non-numeric overall metrics after the re-prompt now fail with a clear
     "metrics validation failed" error and non-zero exit, before any ledger append or commit.
4. Tests (declared test changes): new `tests/test_artefacts.py` for the validator and re-prompt builder; extend
   `tests/test_planner.py` with (a) truncated final message (exit 1, `stopReason length` in stdout) after a valid file
   still appends to `plans.jsonl` and commits, (b) no file and empty stdout yields metrics-validation failure, (c) a
   partial file triggers one re-prompt whose text lists only the missing sections, (d) stdout fallback emits the
   WARNING. All subprocess and git calls are faked; no network.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (Steps 3 and 5 reuse the validator and the `run_turn` seam)

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §3 (append to `plans.jsonl` only after validation); `ruleset.md` §2 (typed,
  documented public functions); `docs/safety.md` Human review boundaries (prompt change is not a governance change but
  is flagged in the PR description).
- **Potential failure modes for this step:** Validator too strict for existing calibration plans; agents that cannot
  write files (stdout-only runners) regress; prettier reformatting changes headings used by the validator.
- **Guardrails and early‑abort checks:** Validate before prettier, match headings case-insensitively; run the validator
  against all existing `plans/*.md` in a test to measure false-negative rate and relax only the size floor if needed.

### Success & Discard Criteria

- **Success:** All four new planner tests pass; validator accepts at least 90 per cent of the existing non-stub plans in
  `plans/`; no metric defaults remain in `planner.py`.
- **Discard:** If more than half of existing valid plans fail validation after two relaxation attempts, revert to the
  metrics-only check and record the section list as follow-up.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 2.2   |
| impact_pred         | 22.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 17.04 |

### Step Metrics Rationale

Highest single impact because it stops plan loss on truncation. Entropy reflects one new module, one prompt and one
central entrypoint. `EV = 0.85 * 22.0 + 0.5 * 4.0 - 0.3 * 2.2 - 3.0 = 18.70 + 2.00 - 0.66 - 3.0 = 17.04`.

---

## Step 3: Incremental executor artefact (Bean 0138, executor half)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mirrors Step 2 with the same validator; risk is moderate because `executor.py` is large (1761 lines)
  but the change is localised to the agent invocation and notes handling.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Require the executor agent to create and extend `executions/E-*.md` incrementally and validate it
  before commit, with the same truncation tolerance and single targeted re-prompt.
- **Git branch:**
  I-1792765424-incremental-artifacts-budgets-and-local-llm-envelope/P-1792788153-claude-agent-claude-opus-5-5/E-{timestamp}-incremental-artifacts/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add `EXECUTION_SPEC` to `artefacts.py`: required sections for the execution notes (summary, steps executed, tests
   run, actual metrics table), size floor 400 bytes, numeric actual-metrics table required.
2. `holon-config/prompts/executor.template.md`: add the same write protocol directing the agent to create
   `executions/{exec_id}.md` first with a skeleton (summary and metrics table), append per-step notes as each plan step
   completes, then final verification results.
3. `entrypoint/executor.py`: after the agent turn, validate notes; tolerate truncation when valid; one targeted
   re-prompt for missing sections; stdout fallback with WARNING only when no file exists.
4. Tests (declared): extend `tests/test_executor.py` with valid-file-after-truncation, missing-file failure and targeted
   re-prompt cases using existing fakes.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `ruleset.md` §3 (tests mirror source files); `constraints.md` §1 (commit boundaries).
- **Potential failure modes for this step:** Executor notes format diverges from what review loop expects; re-prompt
  causes the agent to redo code changes.
- **Guardrails and early‑abort checks:** Re-prompt text explicitly says "do not modify code, only append the listed
  sections"; check `review_loop.py` consumers of the notes before changing headings.

### Success & Discard Criteria

- **Success:** New executor tests pass and existing `test_executor.py` stays green.
- **Discard:** If the review loop depends on the old notes shape and adaptation exceeds 1.5x cost, keep validation in
  warn-only mode for the executor and record a follow-up.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.78  |
| entropy_pred        | 1.8   |
| impact_pred         | 12.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 3.5   |
| ev_pred             | 8.07  |

### Step Metrics Rationale

Lower probability than Step 2 because `executor.py` is larger and its notes contract is less explicit.
`EV = 0.78 * 12.0 + 0.5 * 3.5 - 0.3 * 1.8 - 2.5 = 9.36 + 1.75 - 0.54 - 2.5 = 8.07`.

---

## Step 4: Budget core module with injected clock (Bean 0139 foundation)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pure, dependency-free logic that is easy to test in isolation; low risk and reused by Steps 5 and 6.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Provide a single source of truth for turn, stall and stage budgets and their configuration.
- **Git branch:**
  I-1792765424-incremental-artifacts-budgets-and-local-llm-envelope/P-1792788153-claude-agent-claude-opus-5-5/E-{timestamp}-budgets/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. New module `sandbox_executor/budgets.py` with:
   - `StageBudgets` dataclass with defaults intent 600 s, plan 2100 s, execute 2700 s, review iteration 900 s,
     calibration 600 s; `TurnBudgets` with turn 300 s and stall 90 s.
   - `load_budgets(env)` reading `HOLON_TURN_TIMEOUT_SECONDS`, `HOLON_TURN_STALL_SECONDS` and per-stage overrides
     `HOLON_STAGE_BUDGET_INTENT_MINUTES`, `..._PLAN_MINUTES`, `..._EXECUTE_MINUTES`, `..._REVIEW_ITERATION_MINUTES`,
     `..._CALIBRATION_MINUTES`; non-positive or non-numeric values log a warning and keep the default.
   - `BudgetClock` protocol (`now()`), default `time.monotonic`; `StageTimer` that tracks start, `elapsed()`,
     `remaining()`, `breached()`.
   - `format_stage_summary(stage, status, elapsed_s, budget_s)` producing exactly `plan: ok (4.1 min / 35 min)` (one
     decimal for elapsed, integer budget minutes).
   - `BudgetBreach` exception carrying `stage`, `budget_name` (`turn`, `stall` or `stage`), `budget_seconds`,
     `elapsed_seconds`.
2. Tests (declared): new `tests/test_budgets.py` covering defaults, env overrides, invalid values, summary formatting
   and timer arithmetic with a fake clock.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** Intent question answer (no synthetic throughput data; defaults copied from the beans); intent
  constraint 6 (no new dependencies).
- **Potential failure modes for this step:** Env var naming drift between docs and code.
- **Guardrails and early‑abort checks:** Export the env var names as module constants and reference them from docs.

### Success & Discard Criteria

- **Success:** `tests/test_budgets.py` green; summary string matches the intent example exactly.
- **Discard:** Not expected; abort only if cost exceeds 1.5x.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.4   |
| impact_pred         | 14.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 11.90 |

### Step Metrics Rationale

Self-contained new module with deterministic tests.
`EV = 0.88 * 14.0 + 0.5 * 3.0 - 0.3 * 1.4 - 1.5 = 12.32 + 1.50 - 0.42 - 1.5 = 11.90`.

---

## Step 5: Turn supervisor — streamed output, stall detection, process-group termination, salvage, single resume and ACT-BUDGET record (Bean 0139)

- **Sub‑intent recommendation:** YES
- **Reasoning:** Riskiest and most reusable piece (new concurrency, signal handling and ledger write) with its own
  meaningful evaluation; a sub-intent isolates it. If the agent's trust level is still baseline (cannot spawn
  sub-intents per `docs/safety.md` trust table), execute it as a dedicated execution branch with its own review
  iteration instead.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLORATORY

- **Hypothesis being tested:** Combining stdout/stderr activity with artefact file growth as liveness signals detects
  genuine stalls within the 90 s window without false-positive kills of healthy turns.
- **Learning target:** False-positive rate of stall detection per runner, typical turn durations, and whether a single
  resume turn recovers a salvaged partial artefact.
- **Maximum acceptable cost for this learning:** 4.5 cost units; if exceeded by 1.5x, ship turn timeout plus stage
  ceiling only and keep stall detection disabled by default with a follow-up intent.

### Intent & Git Integration

- **Step Intent:** Ensure no single runner turn can hang a stage, partial work is preserved, and every unrecovered
  budget failure leaves an auditable ledger action.
- **Git branch:**
  I-1792765424-incremental-artifacts-budgets-and-local-llm-envelope/P-1792788153-claude-agent-claude-opus-5-5/E-{timestamp}-turn-supervisor/\_
  (or sub-intent branch `I-{timestamp}-turn-supervisor/_` under the root intent if trust permits)
- **Sub‑intent:** NEW

### Implementation Details (No code blocks, only logic/steps)

1. Add `run_turn(cmd, *, turn_budget, stall_budget, stage_timer, watch_path, clock, sleeper, popen_factory)` in
   `budgets.py` (or a sibling `turns.py` if it grows beyond about 200 lines):
   - Launch via `subprocess.Popen` with `start_new_session=True`, pipes for stdout and stderr, text mode.
   - Two daemon reader threads append chunks to buffers and update a shared `last_activity` timestamp using the injected
     clock.
   - Poll loop using the injected sleeper (default 1 s): breach on turn timeout, on `now - last_activity > stall` (where
     `last_activity` also advances when `watch_path` size or mtime increases), or on stage remainder reaching zero.
   - On breach: `os.killpg(child_pgid, SIGTERM)` after asserting `child_pgid != os.getpgid(0)`, wait a 10 s grace via
     the sleeper, then SIGKILL; join readers; return a `TurnResult` with `returncode`, captured output, `breach` (or
     None), elapsed seconds and parsed `stop_reason` if present.
2. Salvage and resume policy in a helper `supervise_authoring_turn(...)` used by planner and executor:
   - After a breach, validate the artefact with Step 2's validator; keep whatever exists on disk (salvage).
   - Attempt exactly one resume turn whose prompt names the file, states it is partial, and lists missing sections
     (reusing `build_reprompt`); the resume turn is bounded by the smaller of the turn budget and the stage remainder.
   - If the resume turn also breaches or the artefact is still invalid, call `append_action_record()` on
     `holon-knowledge/ledger/executions.jsonl` (which takes `LedgerLock`) with `entry_type` `action`, `action_id`
     `ACT-BUDGET-{epoch}-{8 hex}`, `outcome` `failure`, `category` `budget`, `elapsed_seconds`, `budget_name`,
     `budget_seconds`, `stage`, and the plan or execution id; then exit non-zero.
   - Record is written exactly once per stage failure; no other ledger file is touched.
3. Wire into `entrypoint/planner.py` and `entrypoint/executor.py` in place of the blocking `subprocess.run` agent calls,
   keeping `cleanup_ephemeral_credentials()` in a `finally` block so credentials are removed even on breach.
4. Tests (declared): new `tests/test_turn_supervisor.py` using a fake `Popen` (scripted output chunks and exit), fake
   clock and recording sleeper and a fake `killpg`: turn timeout breach, stall breach, stall averted by file growth,
   stage-ceiling breach, salvage plus successful resume (no ledger record), salvage plus failed resume (exactly one
   ACT-BUDGET record with the required fields, appended to a temp `executions.jsonl`). No real processes are signalled.

### Dependencies & Criticality

- **Depends on:** Steps 2 and 4
- **Is Bottleneck:** YES (Bean 0139 acceptance depends on it; p_success 0.68 is above the 0.6 threshold that would
  mandate STRONGLY_YES)

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §2 (only signal processes the runner spawned); `constraints.md` §3 and
  `docs/safety.md` Invariant 6 (append-only, `LedgerLock`); intent constraint 6 (no auxiliary ledgers).
- **Potential failure modes for this step:** Reader threads deadlock on full pipes; `killpg` targets the entrypoint's
  own group; duplicate ACT-BUDGET records; credentials leaked when a breach skips cleanup.
- **Guardrails and early‑abort checks:** Pipes always drained by threads; pgid assertion; single write site covered by a
  test asserting one line; `finally` cleanup; stall detection can be disabled with `HOLON_TURN_STALL_SECONDS=0`.

### Success & Discard Criteria

- **Success:** All supervisor tests pass under `uv run pytest`; planner and executor tests from Steps 2 and 3 remain
  green with the supervisor in place.
- **Discard:** If cost exceeds 1.5x (6.75) or false-positive stall behaviour cannot be bounded in tests, ship turn and
  stage budgets only and defer stall detection.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.68  |
| entropy_pred        | 3.2   |
| impact_pred         | 24.0  |
| cost_pred           | 4.5   |
| learning_value_pred | 6.5   |
| ev_pred             | 14.11 |

### Step Metrics Rationale

Largest entropy and lowest probability: concurrency, signals and a ledger write are novel here. Learning value is high
because it produces the first real turn-duration and stall data.
`EV = 0.68 * 24.0 + 0.5 * 6.5 - 0.3 * 3.2 - 4.5 = 16.32 + 3.25 - 0.96 - 4.5 = 14.11`.

---

## Step 6: Stage ceilings, elapsed-vs-budget summary lines and replacing the hard-coded `flow.py` timeout

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mechanical integration across `flow.py` stage functions once Steps 4 and 5 exist; moderate surface but
  low novelty.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make every stage report `stage: status (elapsed min / budget min)` and bound stage wall-clock by the
  configured ceilings.
- **Git branch:**
  I-1792765424-incremental-artifacts-budgets-and-local-llm-envelope/P-1792788153-claude-agent-claude-opus-5-5/E-{timestamp}-budgets/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `flow.py`, create a `StageTimer` at the start of `run_intent_stage`, `run_plan_stage`, `run_execute_stage`, each
   review iteration in `run_review_loop`, and `run_calibrate_stage`; attach elapsed and budget to `StageResult` and have
   `run_flow_pipeline` print `format_stage_summary` for every stage.
2. Replace `timeout=300` in `run_execute_stage` (the verification `pytest` subprocess) with the execute stage's
   remaining budget (floored at 30 s), and replace the hard-coded `300.0` duration and "300 seconds" log string with the
   actual budget value.
3. Pass the stage ceiling into the container through a `HOLON_STAGE_BUDGET_SECONDS` variable (forwarded automatically by
   the existing `HOLON_*` forwarding in `cli.py`) so in-container entrypoints use the same `StageTimer`.
4. A host-side stage breach marks the `StageResult` failed with the budget that fired; the ACT-BUDGET record is written
   by the in-container supervisor only, to avoid duplicates.
5. Tests (declared): extend `tests/test_flow.py` asserting the summary line format, that the verification subprocess
   receives the configured timeout (not 300), and that a stage breach under a fake clock fails the stage.

### Dependencies & Criticality

- **Depends on:** Steps 4 and 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §1 (scope limited to this branch); intent constraint 6 (Australian English log
  strings).
- **Potential failure modes for this step:** `flow.py` is 3417 lines with many tests; changing `StageResult` could break
  existing assertions.
- **Guardrails and early‑abort checks:** Add fields with defaults only; run `tests/test_flow.py` after each stage
  function is touched.

### Success & Discard Criteria

- **Success:** No literal `timeout=300` remains in `flow.py` for the verification run; every stage prints the summary
  format; `tests/test_flow.py` green.
- **Discard:** If review-iteration wiring breaks the review loop tests beyond 1.5x cost, ship summaries for the other
  stages and record review-iteration ceilings as follow-up.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 1.8   |
| impact_pred         | 12.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 8.56  |

### Step Metrics Rationale

Broad but shallow edit of a large file.
`EV = 0.80 * 12.0 + 0.5 * 3.0 - 0.3 * 1.8 - 2.0 = 9.60 + 1.50 - 0.54 - 2.0 = 8.56`.

---

## Step 7: Local-LLM model envelope — explicit maxTokens and reasoning mode in `prepare_agent_dir()` (Bean 0140)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Contained to `local_llm.py` and its tests, with an existing error type and clear rules.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Guarantee every generated model entry carries a valid integer `maxTokens` and a reasoning mode, each
  with a logged provenance.
- **Git branch:**
  I-1792765424-incremental-artifacts-budgets-and-local-llm-envelope/P-1792788153-claude-agent-claude-opus-5-5/E-{timestamp}-local-llm-envelope/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add `apply_model_envelope(config, env)` called from `prepare_agent_dir()` after `build_container_config` and before
   writing `models.json`:
   - For each model in each provider: if `maxTokens` is absent, synthesise `min(16384, contextWindow // 4)` and log
     model id, value and reason (`synthesised: absent in host config`), recording `source=synthesised`; otherwise record
     `source=host`.
   - Reject with `LocalLLMConfigError` when `maxTokens` is a bool, non-integer, zero, negative, or greater than
     `contextWindow - reserve` (reserve constant `HOLON_CONTEXT_RESERVE_TOKENS`, default 4096, documented).
   - When `contextWindow` is absent, take it from `HOLON_LOCAL_CONTEXT_WINDOW` if set, else raise `LocalLLMConfigError`
     explaining that synthesis needs a context window (never guess).
2. Add `ENV_REASONING_MODE = "HOLON_REASONING_MODE"`; resolution order env, then host model entry (`reasoning` field),
   then default `off` (least-thinking for authoring stages); validate against the allow list; write the resolved value
   into the model entry and log `reasoning=<mode> source=env|host|default`.
3. Return the provenance alongside the config (for example a `ModelEnvelope` list stored in a sidecar attribute or a
   second return from a new helper) so `doctor` can print it without re-deriving; keep `prepare_agent_dir()`'s public
   return type unchanged for existing callers.
4. Leave `rewrite_authority`, allow-list handling, `rewritten_local_hosts`, `container_env` and NO_PROXY behaviour
   untouched; existing `tests/test_local_llm.py` assertions on these stay as-is.
5. Tests (declared): extend `tests/test_local_llm.py` for synthesis (with and without the 16384 cap), pass-through of a
   valid host value, rejection of 0, negative, float, string, bool and oversize values, reasoning mode from env, host
   and default, and an invalid mode raising.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** Bean 0027 safety invariant documented in `local_llm.py` (only loopback and allow-listed
  authorities rewritten); `ruleset.md` §2.
- **Potential failure modes for this step:** Existing host configs without `contextWindow` start failing hard; pi
  ignores or rejects an unknown `reasoning` field.
- **Guardrails and early‑abort checks:** The error message names the exact field and env var to set; check pi's
  `models.json` schema in Step 1 for the reasoning field name and map accordingly.

### Success & Discard Criteria

- **Success:** All new and existing `test_local_llm.py` tests pass; generated `models.json` always contains integer
  `maxTokens` and the reasoning field.
- **Discard:** If pi rejects the reasoning field, carry the mode only in Holon logs and doctor output and record a
  follow-up.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.87  |
| entropy_pred        | 1.3   |
| impact_pred         | 14.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 11.79 |

### Step Metrics Rationale

Clear rules, existing test file, isolated module.
`EV = 0.87 * 14.0 + 0.5 * 3.0 - 0.3 * 1.3 - 1.5 = 12.18 + 1.50 - 0.39 - 1.5 = 11.79`.

---

## Step 8: Minimal `holon doctor` with envelope report and gateway health probe (Bean 0140)

- **Sub‑intent recommendation:** NO
- **Reasoning:** New but small CLI surface; the probe is isolated behind an injected fetcher so risk is low.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Give operators one command that shows the effective model envelope and the local server's cache and
  output limits.
- **Git branch:**
  I-1792765424-incremental-artifacts-budgets-and-local-llm-envelope/P-1792788153-claude-agent-claude-opus-5-5/E-{timestamp}-local-llm-envelope/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add a `doctor` sub-parser in `cli.py` (no `doctor` exists today) that, when `HOLON_LOCAL_LLM` is enabled, builds the
   envelope via Step 7's helper into a temporary directory and prints per model
   `model=<id> context=<n> max_tokens=<n> source=host|synthesised` and `reasoning=<mode> source=env|host|default`.
2. Add `probe_gateway_health(base_url, fetch)` in `local_llm.py`: derive the host-side health URL from the provider's
   original (pre-rewrite, loopback) base URL; default fetcher uses `urllib.request` with a 2 s timeout; parse the
   response into `prefill_cache=off|cold|warm` plus memory figures and `effective_defaults.max_output_tokens`.
   Classification: cache disabled is `off`, enabled with zero cached entries or tokens is `cold`, otherwise `warm`;
   unknown shape prints `prefill_cache=unknown` and never fails the command.
3. Print a WARNING line when emitted `maxTokens` exceeds `effective_defaults.max_output_tokens`.
4. Tests (declared): `tests/test_local_llm.py` gains three frozen JSON health fixtures (off, cold, warm) plus a
   malformed one, parsed through an injected fetcher; `tests/test_cli.py` gains a doctor output test with the fetcher
   patched. No live gateway call anywhere.

### Dependencies & Criticality

- **Depends on:** Step 7
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §2 (no external network from sandboxes; doctor runs on the host and only targets
  loopback or allow-listed authorities); intent constraint 6 (no new dependencies).
- **Potential failure modes for this step:** The real health schema differs from the fixtures; doctor accidentally
  probes a remote host.
- **Guardrails and early‑abort checks:** Refuse to probe any authority that `rewrite_authority` would not rewrite;
  fixtures copied from the schema described in the bean, marked as frozen fixtures, not performance claims.

### Success & Discard Criteria

- **Success:** Doctor prints the two envelope lines and the `prefill_cache` line in tests; mismatch warning covered.
- **Discard:** If the health schema is unknown at execution time, ship the envelope lines and the `unknown` path only.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 1.5   |
| impact_pred         | 10.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 8.05  |

### Step Metrics Rationale

Moderate uncertainty about the gateway schema; learning value from making cache state observable.
`EV = 0.80 * 10.0 + 0.5 * 5.0 - 0.3 * 1.5 - 2.0 = 8.00 + 2.50 - 0.45 - 2.0 = 8.05`.

---

## Step 9: Documentation of budgets, environment variables and local-LLM envelope

- **Sub‑intent recommendation:** NO
- **Reasoning:** Low-risk documentation that must land with the code it describes.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the new behaviour discoverable and auditable.
- **Git branch:**
  I-1792765424-incremental-artifacts-budgets-and-local-llm-envelope/P-1792788153-claude-agent-claude-opus-5-5/E-{timestamp}-docs/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. New `docs/budgets.md`: turn, stall and stage budgets, defaults with their bean provenance, all env var names, salvage
   and single-resume policy, the ACT-BUDGET record fields, and the summary line format.
2. Update `docs/sandbox/create_plan.md` and `docs/sandbox/execute_plan.md` with the incremental write protocol,
   truncation tolerance, re-prompt and stdout fallback warning.
3. New section in `apps/sandbox-executor/README.md` (or `docs/local_llm.md`) for `maxTokens` synthesis and rejection
   rules, `HOLON_REASONING_MODE`, `HOLON_CONTEXT_RESERVE_TOKENS`, `HOLON_LOCAL_CONTEXT_WINDOW`, and `holon doctor`
   output; add `ACT-BUDGET` to `docs/ledger_schema.md` action examples.
4. Format with the repository prettier configuration; formulas in code spans; Australian English.

### Dependencies & Criticality

- **Depends on:** Steps 2 to 8
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** Intent constraint 6 (markdown formatted with repository configuration); `docs/metrics.md` hygiene
  rule on code spans.
- **Potential failure modes for this step:** Docs drift from env var constants; prettier oscillation.
- **Guardrails and early‑abort checks:** Copy names from module constants; run prettier twice and require no diff on the
  second pass.

### Success & Discard Criteria

- **Success:** Prettier check clean; every env var introduced in code appears in docs.
- **Discard:** Not expected.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 0.6   |
| impact_pred         | 5.0   |
| cost_pred           | 0.8   |
| learning_value_pred | 1.0   |
| ev_pred             | 4.17  |

### Step Metrics Rationale

Routine documentation. `EV = 0.93 * 5.0 + 0.5 * 1.0 - 0.3 * 0.6 - 0.8 = 4.65 + 0.50 - 0.18 - 0.8 = 4.17`.

---

## Step 10: Verification gate — tests, lint, format, spelling and ledger hygiene

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mandatory acceptance gate; small and deterministic.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the acceptance criteria and constraints of the intent before requesting human review.
- **Git branch:**
  I-1792765424-incremental-artifacts-budgets-and-local-llm-envelope/P-1792788153-claude-agent-claude-opus-5-5/E-{timestamp}-verify/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor`: `uv run pytest -q` (full suite, compared with the Step 1 baseline), `uv run ruff check`,
   `uv run ruff format --check`; never unittest, never `PYTHONPATH`.
2. Run `scripts/pre_commit_check.py` and prettier check on changed markdown.
3. Grep changed files for American spellings in comments, docstrings and log strings (for example "synthesize",
   "behavior", "normalize") and fix them, leaving existing public identifiers such as `normalize_agent_id` untouched.
4. Confirm `git diff --stat` shows no new files under `holon-knowledge/ledger/` and that existing ledger lines are
   unchanged (append-only).
5. Confirm `pyproject.toml` and `uv.lock` are unchanged (no new dependencies).

### Dependencies & Criticality

- **Depends on:** Steps 2 to 9
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §3; `ruleset.md` §3; `docs/safety.md` Invariant 4 (human review before `main`).
- **Potential failure modes for this step:** Flaky timing tests; pre-existing failures masking new ones.
- **Guardrails and early‑abort checks:** All timing tests use fake clocks and sleepers (no real sleeps); compare against
  baseline failure list.

### Success & Discard Criteria

- **Success:** Full suite green (or equal to baseline failures), ruff and format clean, no ledger rewrites, no new
  dependencies.
- **Discard:** If new failures cannot be fixed within 1.5x cost, return the failing step to its execution branch.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 0.5   |
| impact_pred         | 6.0   |
| cost_pred           | 1.2   |
| learning_value_pred | 2.0   |
| ev_pred             | 4.75  |

### Step Metrics Rationale

Deterministic gate; most of its risk is inherited from earlier steps.
`EV = 0.85 * 6.0 + 0.5 * 2.0 - 0.3 * 0.5 - 1.2 = 5.10 + 1.00 - 0.15 - 1.2 = 4.75`.

---
