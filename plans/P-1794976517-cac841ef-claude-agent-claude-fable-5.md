# Plan for I-1794976501-macgate

- **Plan ID:** P-1794976517-cac841ef-claude-agent-claude-fable-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-fable-5 (version: 2.1.302)
- **Created At:** 2026-11-18T04:35:18.680Z

## Planner Autonomy Summary

- **Intent handling:** REFRAME
- **Reframed intent (if applicable):** Identical goals and approach, with one minimal scope amendment: the touch set
  must also include `apps/sandbox-executor/flow_module_budgets.json`. The three flow modules listed in scope sit exactly
  at their enforced line budgets today (`github/ci_final_head.py` 190/190, `github/ci_reverify.py` 97/97,
  `github/approval_invalidation.py` 137/137), and `scripts/check_flow_module_budgets.py` (run by `uv run task lint`)
  fails any growth. Goal 2 cannot add a single line to those modules without raising their budget entries, so the
  acceptance criteria "no file outside the listed scope is changed" and "`uv run task lint` passes" are mutually
  unsatisfiable as written. Raising a budget alongside a flow change is the established mechanism (the budgets file's
  own description records seven such raises, including one "for the hook of the final-head CI check"). The amendment is
  one JSON file, is not in the off-limits list of the naming change in flight, and is declared here instead of being
  discovered mid-execution.
- **Exploration stance:** balanced — goal 1 reuses an existing, already-tested baseline-comparison gate
  (`scripts/ci_timing_gate.py`, proven in `test-stress.yml`), so the design risk concentrates in goal 2's rerun logic;
  one bounded verification step precedes implementation, the rest is exploitation of measured numbers.
- **Safety priority level:** standard
- **Priority Justification:** No rule in `holon-config/world/ruleset.md`, `constraints.md` or `docs/safety.md` elevates
  this intent: it does not touch estimators, trust, routing, the ledger writer, or core invariants. It does change CI
  gating and the approval-invalidation path, which are merge-adjacent, so the plan keeps both fail-closed (a real
  regression still fails; an undiagnosable failure still withdraws the approval) and adds no auto-merge behaviour —
  merging stays with the human maintainer, preserving safety.md's "human review is the final gate".

## Exploration

- **Proportion of steps that are exploratory:** 0.15
- **Justification:** The intent supplies the measured noise data (235 s, 280 s, 306 s, 312.7 s, 332.0 s, 370.2 s) and
  constrains the design space to two named measurement options. One info-gathering step verifies repo-level assumptions
  (budgets, step-name contract, callers); every other step exploits a design that existing code already demonstrates.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.78  |
| entropy_pred        | 11.0  |
| impact_pred         | 85.0  |
| cost_pred           | 32.0  |
| learning_value_pred | 6.5   |
| ev_pred             | 34.3  |

### Strategy Rationale

**Goal 1 choice (with the measured numbers).** The current gate compares whole-job wall time on the macOS runner against
a fixed 300 s budget. The recorded data shows the same code swinging 235 s → 370.2 s (range 135.2 s, a 57 % swing)
between runs, `main` itself at 306 s, and ubuntu at ~120 s for the same suite: a fixed absolute budget of 300 s on macOS
has zero real headroom and fails on runner speed alone. Simply raising the budget is forbidden by the intent. The chosen
measure is the intent's second option: **comparison with the base branch's time on the same runner class**, implemented
by rewiring `test-unit.yml` onto the existing `scripts/ci_timing_gate.py` (record on `main`, compare on PRs, artefacts
keyed per workflow/job/OS) exactly as `test-stress.yml` already does. Thresholds, justified from the data:

- relative gate: tolerance 50 % AND a per-OS minimum delta — macOS `--min-delta 150` (strictly above the observed 135.2
  s same-code swing), ubuntu `--min-delta 120` (script default; ubuntu shows no comparable swing);
- absolute ceiling `--ceiling 600` on both OSes (cannot be label-overridden), so a hang or runaway suite still fails
  even during cold start.

Against any plausible `main` baseline median (235–306 s), a macOS failure requires `duration > median + 150`, i.e. at
least 385 s: the recorded noise cases 312.7 s, 332.0 s and 370.2 s all pass, while a real slowdown of the suite (for
example +200 s, 280 s → 480 s, which exceeds both `280 * 1.5 = 420` and `280 + 150 = 430`) still fails, and anything
over 600 s fails unconditionally. This stays fail-closed (goal 3) without ever being a budget raise: a fixed 300 s
budget could not distinguish 370 s of noise from a 470 s regression; the relative measure can. The per-OS artefact key
means macOS is only ever compared with macOS `main` runs.

**Goal 2 choice.** The rerun decision must live where the flow already classifies final-head CI, and the only caller of
`check_final_head_ci` is `flow/stages/calibrate.py` — an off-limits file. Therefore the entire rerun behaviour is
implemented inside `ci_final_head.py` behind the existing `check_final_head_ci` seam, so the off-limits caller needs no
edit (confirmed: the record shape it consumes is preserved, with one additive key). `ci_failure_detail.py` (read-only
for us) already tags failed checks with `timing_gate` via the `"CI Timing Gate"` step-name prefix and exposes
`parse_run_id`, so detection needs no out-of-scope code change — the new workflow step is simply named with that prefix.

**Overall metric derivation.** `entropy_pred = 11.0` is the sum of step entropies (0.5 + 2.0 + 3.5 + 1.5 + 2.5 + 1.0);
`cost_pred = 32.0` is the sum of step costs. The bottleneck is Step 3 (rerun logic, `p = 0.85`): Steps 3–6 form the
critical chain, but each is retryable within the run, so overall `p_success_pred = 0.78` is a qualitative joint estimate
rather than a raw product. Impact 85 reflects that the current gate is actively withdrawing valid approvals and blocking
calibration heads on two recent PRs. EV uses the bootstrap coefficients `lambda = 0.3`, `mu = 0.5`:
`EV = 0.78 * 85.0 + 0.5 * 6.5 - 0.3 * 11.0 - 32.0 = 66.3 + 3.25 - 3.3 - 32.0 = 34.25`.

Strong assumptions, checked against the world ruleset: (1) the budgets-file amendment is a convention-following metadata
edit, not a core-invariant change (its own history documents identical raises); (2) `gh run rerun --failed` is available
to the flow's `gh` auth — if not, the dispatch fails and the code falls back to today's behaviour (fail-closed); (3) the
naming change in flight does not touch `.github/`, `scripts/`, or `flow/github/` — the declared off-limits list confirms
this, with a small residual conflict risk on `flow_module_budgets.json` keys noted below.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `world/constraints.md#1` (git flow): work happens on the plan/execution branches under `I-1794976501-macgate/`; no
    commits to `main`.
  - `world/constraints.md#3` and safety.md Invariant 6: ledger files are append-only; this plan never edits them (the
    bookkeeping-only path detection treats them as data, read via `git diff --name-only`).
  - `world/ruleset.md#3` (testing): changes to test files must be declared in planning — Step 5 declares every test file
    added or modified, and no existing assertion is weakened.
  - safety.md "Human review is the final gate": the rerun feature never approves or merges; it only defers the
    withdrawal of an approval until one automatic rerun of a noise-prone check has been observed.
  - Repository ruff complexity limits and the flow module size ratchet (`check_flow_module_budgets.py`).
- **Potential violations or edge cases:**
  - Touching `apps/sandbox-executor/flow_module_budgets.json` is outside the intent's literal scope list (declared and
    justified above; without it the acceptance criteria contradict each other).
  - A rerun that never re-registers on GitHub would leave the record `unknown` → Stage 5 reports "not verified", which
    is already a non-merge-ready outcome (fail-closed, no silent pass).
  - The in-flight naming change could, in principle, also edit `flow_module_budgets.json` (renamed modules re-key
    entries); our edit raises values of three stable `github/*` keys, so a merge conflict is unlikely and trivially
    resolvable.
- **Mitigations built into the plan:**
  - Every new decision path defaults to today's behaviour: unknown approved head, unreadable diff, non-timing failed
    check, mixed failures, code-changing head, failed rerun dispatch, or a second timing failure all produce exactly the
    current withdrawal/blocked outcome.
  - The timing measure keeps an absolute, label-proof 600 s ceiling, so even a cold-start or baseline-fetch failure
    cannot wave through a runaway suite.
  - Off-limits files (`flow/__init__.py`, `flow/stages/`, `flow/calibrate_*.py`, `ledger.py`, `calibration.py`,
    `ref_names.py`, `agent_defaults.py`, entrypoint, cli) are never edited; the design was shaped around the one
    off-limits caller (`stages/calibrate.py`) specifically so it needs no change.
- **Residual risk accepted (and why):** (a) If the GitHub artefact fetch fails on a PR run, the relative check degrades
  to cold start and only the 600 s ceiling applies for that run — this is `ci_timing_gate.py`'s documented, already
  shipped behaviour and self-heals on the next run. (b) A real slowdown smaller than the macOS min-delta (< 150 s)
  passes the relative gate; with an observed 135 s same-code swing, no per-run measure can separate smaller regressions
  from noise, and the per-test growth report still surfaces them for humans.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 11.0
- **Budget Compliance:** The strategy fits within budget (11.0 ≤ 15.0), so there is no sub-intent list and the plan
  executes as one unit.

## Plan Description & Strategy

Two coupled fixes, both fail-closed:

1. **Measurement (goal 1, CONFIG):** `.github/workflows/test-unit.yml` stops using the fixed-budget composite action and
   adopts the repository's existing baseline-comparison gate `scripts/ci_timing_gate.py`, following the proven
   `test-stress.yml` pattern: a start-epoch step first; `record` mode + artefact upload on `main` pushes and manual
   dispatches; `compare` mode on pull requests against the median of the last 5 `main` runs **on the same runner OS**,
   with per-OS `--min-delta` (macOS 150 s — above the observed 135.2 s same-code swing — ubuntu 120 s), tolerance 50 %
   and a label-proof 600 s ceiling. The compare step is named with the `"CI Timing Gate"` prefix so
   `ci_failure_detail.failure_detail` keeps tagging its failures as `timing_gate`. `scripts/check_ci_runtime_budget.py`
   and the composite action are left untouched — four other workflows still use them and are out of this intent's goal.

2. **Rerun-before-withdrawal (goal 2, IMPLEMENTATION):** `check_final_head_ci` in `flow/github/ci_final_head.py` gains
   one post-classification hook: when the final-head record is `failed`, **every** failed check is tagged `timing_gate`,
   and `git diff --name-only <approved-head>..<final-head>` names only paths under `plans/`, `executions/` or
   `holon-knowledge/`, it dispatches `gh run rerun <run-id> --failed` once, waits for the checks to re-register and
   settle, and re-classifies. A passing rerun yields a `success` record, so `ci_reverify` returns GREEN, the approval
   stands and Stage 5 proceeds — with zero change to the off-limits caller. A second failure, any non-timing failure, a
   code-changing diff, an unknown approved head, or a failed rerun dispatch all fall through to today's behaviour (RED
   verdict, once-per-head invalidation comment). The record and the invalidation comment carry a `timing_rerun` note for
   auditability. New collaborators (rerun dispatcher, diff lister) are seams on `FinalHeadSeams`, keeping every new test
   hermetic.

Verification is hermetic pytest plus one full-suite run and the two lint tasks. No timing-performance measurement is
being made by the tests themselves (all clocks and CI probes are fakes), so under the test-run discipline no repeated
runs are planned: affected tests first, then `uv run task test` once.

## Touch Set

- `.github/workflows/test-unit.yml`
- `apps/sandbox-executor/src/sandbox_executor/flow/github/ci_final_head.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/github/ci_reverify.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/github/approval_invalidation.py`
- `apps/sandbox-executor/flow_module_budgets.json`
- `apps/sandbox-executor/tests/test_ci_timing_gate.py`
- `apps/sandbox-executor/tests/test_final_head_timing_rerun.py`
- `apps/sandbox-executor/tests/test_ci_reverify.py`
- `apps/sandbox-executor/tests/test_approval_invalidation.py`
- `apps/sandbox-executor/tests/test_calibrate_final_head_ci.py`

---

## Step 1: Verify repo-level assumptions that the design depends on

- **Sub‑intent recommendation:** NO
- **Reasoning:** Cheap, read-only, de-risks the two implementation steps; most of it was already confirmed during
  planning and only needs re-verification at execution time against the rebased branch.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The design's load-bearing facts hold on the execution branch: `stages/calibrate.py` is
  the only caller of `check_final_head_ci`; `TIMING_GATE_STEP_PREFIX` is `"CI Timing Gate"`; the three scoped flow
  modules are at budgets 190/97/137; `test-stress.yml` is the canonical `ci_timing_gate.py` wiring; no test asserts the
  current contents of `test-unit.yml`.
- **Learning target:** Whether the in-flight naming change has already moved any of these anchors (it rebases under us),
  and the exact seam shapes `test_calibrate_final_head_ci.py` injects, so Step 3 extends `FinalHeadSeams` without
  breaking 17 existing tests.
- **Maximum acceptable cost for this learning:** ≤ 2 cost units (grep/read only); abort the plan for re-planning if
  `check_final_head_ci` has grown a second off-limits caller.

### Intent & Git Integration

- **Step Intent:** Confirm the planning-time survey on the live execution branch before any edit.
- **Git branch:** I-1794976501-macgate/P-1794976517-cac841ef-claude-agent-claude-fable-5/E-{ts}-macgate-fix/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Grep for callers of `check_final_head_ci` and `FinalHeadSeams` outside `ci_final_head.py`; expect exactly
  `flow/stages/calibrate.py` plus tests.
- Re-read `ci_failure_detail.py` constants (`TIMING_GATE_STEP_PREFIX`, `parse_run_id`) and the seam usage in
  `test_calibrate_final_head_ci.py` and `review_fakes.py`/`hermetic_fixtures.py` if referenced.
- Confirm current line counts vs `flow_module_budgets.json` entries for the three modules and note the exact new budgets
  to record in Steps 3–4.
- Confirm no test under `apps/sandbox-executor/tests/` parses `.github/workflows/test-unit.yml` or the composite action
  (planning-time grep found none; re-check `test_workflow_triggers.py`, `test_repository_hygiene.py`).

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO (a surprise here triggers cheap re-planning, not plan failure)

### Safety & Constraint Considerations

- **Relevant rules:** world/constraints.md#1 (work only on this intent's branches), safety.md sandboxing (read-only
  step).
- **Potential failure modes for this step:** The naming change already landed and re-keyed modules; a new caller of
  `check_final_head_ci` exists.
- **Guardrails and early‑abort checks:** Read-only; if any anchor moved, stop and record the discrepancy in the
  execution notes before touching code.

### Success & Discard Criteria

- **Success:** All five anchors confirmed; exact target budget numbers recorded.
- **Discard:** A second off-limits caller of `check_final_head_ci` exists → stop and re-plan (the seam-only design no
  longer suffices).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.5   |
| impact_pred         | 10.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 8.6   |

### Step Metrics Rationale

Read-only verification of facts already observed once; near-certain.
`EV = 0.97 * 10.0 + 0.5 * 2.0 - 0.3 * 0.5 - 2.0 = 9.7 + 1.0 - 0.15 - 2.0 = 8.55`.

---

## Step 2: Rewire test-unit.yml onto the baseline-comparison timing gate

- **Sub‑intent recommendation:** NO
- **Reasoning:** A single workflow file edit copying a pattern already shipped in `test-stress.yml`; low risk, not
  reusable as a separate branch, and cheap.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The macOS unit job's timing measure stops swinging with the runner: PRs are compared with the median
  of recent `main` runs on the same runner OS instead of a fixed 300 s wall-clock budget.
- **Git branch:** I-1794976501-macgate/P-1794976517-cac841ef-claude-agent-claude-fable-5/E-{ts}-macgate-fix/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `.github/workflows/test-unit.yml`, replace the two `./.github/actions/ci-timing-gate` steps:
  - First step of the job records the start epoch into `GITHUB_ENV` (as `test-stress.yml` does, before checkout so the
    whole job is measured consistently with how records are keyed).
  - After the test step, a step named with the `CI Timing Gate` prefix (for example "CI Timing Gate (compare with
    main)") runs `scripts/ci_timing_gate.py` with mode `compare` on `pull_request` events and `record` otherwise, flags
    `--workflow test-unit --job unit --os <matrix.os> --start <epoch> --runs 5 --tolerance 0.5 --min-delta <per-OS> --ceiling 600 --junit unit-report.xml --repo <repository> --output ci-timing/record.json --override-label-present <label check>`,
    with `GITHUB_TOKEN` in the env.
  - Per-OS min-delta comes from a `matrix.include` field: 120 for ubuntu-latest, 150 for macos-latest (150 > the
    observed 135.2 s same-code swing on macOS).
  - Add an upload-artifact step gated on `push` to `main`, artefact name `ci-timing-test-unit-unit-<os>`,
    `retention-days: 30`, `if-no-files-found: error` (mirrors `test-stress.yml`).
- Keep the existing `on:` triggers (labeled/unlabeled are already present, so adding the override label re-runs the
  gate), `permissions` (`actions: read` is required for artefact fetch and already set), and the test step itself
  unchanged.
- Do not touch `.github/actions/ci-timing-gate/action.yml` or `scripts/check_ci_runtime_budget.py`: four other workflows
  still use them and they are outside this intent's goal.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (goal 1 and acceptance criterion 1 live here; `p = 0.92`, well above the 0.6 sub-intent
  threshold)

### Safety & Constraint Considerations

- **Relevant rules:** Intent goal 1's prohibition on "simply raising the budget"; fail-closed requirement (goal 3).
- **Potential failure modes for this step:** YAML/flag mistakes only surface on real runners; the compare step name
  drifting from the `CI Timing Gate` prefix would silently break the `timing_gate` tagging that goal 2 depends on.
- **Guardrails and early‑abort checks:** Mirror `test-stress.yml` field-for-field; assert the step-name prefix in the
  new tests (Step 5) so a future rename fails hermetically; `--ceiling 600` is label-proof so nothing can make the gate
  unable to fail.

### Success & Discard Criteria

- **Success:** `test-unit.yml` parses (yaml load in tests), uses `ci_timing_gate.py` with the per-OS deltas above, and
  by the recorded numbers: 312.7 / 332.0 / 370.2 s pass against any baseline median ≥ 235 s, while 480 s against a 280 s
  median and anything > 600 s fail.
- **Discard:** If the pattern cannot express a per-OS delta cleanly, fall back to a single `--min-delta 150` for both
  OSes (still fail-closed via ceiling + ubuntu's huge relative headroom) rather than abandoning.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 2.0   |
| impact_pred         | 70.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 60.3  |

### Step Metrics Rationale

Copying a shipped pattern with data-derived thresholds; main uncertainty is runner-side behaviour that cannot be
exercised locally (covered by the script's existing 37 hermetic tests plus Step 5 additions).
`EV = 0.92 * 70.0 + 0.5 * 3.0 - 0.3 * 2.0 - 5.0 = 64.4 + 1.5 - 0.6 - 5.0 = 60.3`.

---

## Step 3: One automatic rerun for bookkeeping-only timing failures in ci_final_head.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** The riskiest step (`p = 0.85`) but still a bounded, single-module change behind existing seams; a
  sub-intent's branch/review overhead would exceed its value and the plan fits the entropy budget as one unit.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** A timing-gate failure on a head whose new commits touch only ledger, plan and knowledge-base files
  triggers exactly one automatic rerun of the failed job inside `check_final_head_ci`, so the off-limits caller
  (`flow/stages/calibrate.py`) and record consumers need no change.
- **Git branch:** I-1794976501-macgate/P-1794976517-cac841ef-claude-agent-claude-fable-5/E-{ts}-macgate-fix/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Extend `FinalHeadSeams` (frozen dataclass, all new fields default `None` so every existing construction keeps working)
  with: a rerun dispatcher (default runs `gh run rerun <run-id> --failed` in the repo dir with the proxy env, bounded by
  the existing step-lookup timeout, returns bool), a changed-paths lister (default runs
  `git diff --name-only <approved>..<final>`, returns `None` on any error), and an approved-head provider (default:
  `approved_head_of(context)` imported from `ci_reverify` — no import cycle, `ci_reverify` does not import
  `ci_final_head`).
- New small helpers, each within ruff complexity limits and with docstrings per the world ruleset:
  - bookkeeping-only predicate: a non-empty path list where every path starts with `plans/`, `executions/` or
    `holon-knowledge/` (module constant); `None` or empty → not eligible (fail-closed).
  - timing-gate-only predicate: a non-empty `failed_details` list where every detail has a truthy `timing_gate`.
  - failed-run-id extraction: `parse_run_id` over the failed checks' `link` fields from the probe's check dicts,
    deduplicated; any check without a parsable run id → not eligible.
- In `check_final_head_ci`, after the first classification: if state is `failed`, the head is eligible (timing-only +
  bookkeeping-only diff from the approved head to the published head), and not already rerun in this call, dispatch the
  rerun(s); on dispatch failure return the original record annotated `timing_rerun = {attempted: false, reason: ...}`
  (today's behaviour follows). On success, poll the probe with the policy clock/sleep until the failed timing checks
  leave the failed state or the registration timeout elapses, then reuse the settled-status wait and re-classify;
  annotate the final record `timing_rerun = {attempted: true, run_ids: [...], outcome: <state>}`. Exactly one rerun per
  invocation by construction (no loop); a second failure returns a `failed` record exactly as today.
- Update the module docstring ("It never reruns CI" no longer holds: it reruns a failed job at most once, only for a
  timing-gate-only failure on a bookkeeping-only head) and keep `_record`'s shape, so `classify_final_head` and all
  consumers are unchanged for the ineligible paths.
- Raise `github/ci_final_head.py` in `apps/sandbox-executor/flow_module_budgets.json` to the new size (expected ≈ 300,
  comfortably under the ~400-line module preference) and append the reason to the file's description string, following
  its existing convention. This is the declared scope amendment.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (goal 2 and acceptance criterion 2 live here; `p = 0.85` ≥ 0.6, so no sub-intent is forced)

### Safety & Constraint Considerations

- **Relevant rules:** safety.md "human review is the final gate" (the rerun never approves or merges); world/ruleset.md
  complexity limits; the intent's off-limits file list (satisfied by the seam-only design); ledger append-only (paths
  are only read).
- **Potential failure modes for this step:** rerun dispatched but checks never re-register (record ends `unknown` →
  Stage 5 "not verified", which is already non-merge-ready — fail-closed); `gh` auth lacks rerun permission (dispatch
  fails → today's behaviour); eligibility misclassification (mitigated by fail-closed defaults on every `None`).
- **Guardrails and early‑abort checks:** every undecidable input (no approved head, unreadable diff, missing run id,
  mixed failures) short-circuits to the unmodified record; `check_final_head_ci` keeps its never-raises contract via the
  existing outer try/except; no new imports from entrypoints into the flow engine.

### Success & Discard Criteria

- **Success:** All Step 5 rerun tests pass hermetically; existing `test_calibrate_final_head_ci.py` tests pass
  unmodified except where they construct seams (additive defaults mean none should need edits); module within its new
  recorded budget; ruff clean without noqa.
- **Discard:** If the logic cannot stay within ruff complexity limits inside this one module, stop and record in the
  execution notes that a new `flow/github/` module (a second scope deviation) is required, rather than silently adding
  one.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 3.5   |
| impact_pred         | 75.0  |
| cost_pred           | 9.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 56.2  |

### Step Metrics Rationale

Largest step: new control flow in a never-raises path with GitHub-side timing semantics that only fakes can exercise.
Learning value 5: the rerun-then-reclassify pattern and the bookkeeping-only predicate are reusable for other flaky
checks. `EV = 0.85 * 75.0 + 0.5 * 5.0 - 0.3 * 3.5 - 9.0 = 63.75 + 2.5 - 1.05 - 9.0 = 56.2`.

---

## Step 4: Surface the rerun in the Stage 5 verdict and the invalidation comment

- **Sub‑intent recommendation:** NO
- **Reasoning:** Two small, additive edits to modules already in scope; low risk and cost.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Reports and the withdrawal comment say when a timing failure survived (or passed after) one automatic
  rerun, so a human reading the PR can tell noise-handling from a real regression.
- **Git branch:** I-1794976501-macgate/P-1794976517-cac841ef-claude-agent-claude-fable-5/E-{ts}-macgate-fix/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- `ci_reverify.py`: `reverify_and_report` (or `reverify_final_head`) copies the record's `timing_rerun` note into the
  reverified mapping; the GREEN/RED report line gains a short clause when a rerun was attempted (for example "after one
  automatic rerun of the timing gate"). No verdict mapping changes: `success` → GREEN and `failed` → RED exactly as
  today, so a passing rerun keeps the approval and Stage 5 proceeds with zero logic change here.
- `approval_invalidation.py`: `render_invalidation` accepts the optional rerun note (via the reverified mapping it
  already receives through `invalidate_approval`) and, when a rerun was attempted and failed, adds one line stating the
  failed job was automatically rerun once and failed again. Marker, once-per-head and redaction behaviour unchanged.
- Raise both modules' entries in `flow_module_budgets.json` by the measured growth (expected ≈ +6 and ≈ +8 lines) with
  the convention-following description note.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO (goal 2's behaviour is complete after Step 3; this step is auditability — a failure here
  degrades reporting, not correctness)

### Safety & Constraint Considerations

- **Relevant rules:** once-per-head invalidation contract; credential redaction on comment bodies (reuse the existing
  `CredentialRedactor` path, untouched).
- **Potential failure modes for this step:** exact-string assertions in `test_ci_reverify.py` /
  `test_approval_invalidation.py` break on the new clause.
- **Guardrails and early‑abort checks:** add the clause only when `timing_rerun` is present, so every existing record
  (no key) renders byte-identical output and existing assertions stay valid; any assertion that must change is declared
  in Step 5.

### Success & Discard Criteria

- **Success:** Existing reverify/invalidation tests pass; new assertions show the clause appears exactly when a rerun
  was attempted.
- **Discard:** If threading the note through cleanly requires touching any off-limits caller, drop the report clause
  (keep `timing_rerun` on the record only) — the acceptance criteria do not require it.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 1.5   |
| impact_pred         | 40.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 33.8  |

### Step Metrics Rationale

Additive rendering changes gated on a new optional key; cheap and reversible, with an explicit discard path.
`EV = 0.93 * 40.0 + 0.5 * 2.0 - 0.3 * 1.5 - 4.0 = 37.2 + 1.0 - 0.45 - 4.0 = 33.75`.

---

## Step 5: Hermetic tests for the new measure and the rerun semantics

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tests are the acceptance criteria made executable; they belong with the change on the same branch.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Every acceptance criterion about behaviour is pinned by a hermetic pytest test (fake clocks,
  fetchers, probes, rerunners; no network — the suite's network guard stays satisfied).
- **Git branch:** I-1794976501-macgate/P-1794976517-cac841ef-claude-agent-claude-fable-5/E-{ts}-macgate-fix/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

Declared test-file changes (world/ruleset.md#3; additions only — no existing assertion is weakened, and any exact-string
assertion updated for Step 4's clause is an extension of the expected string, not a relaxation):

- `test_ci_timing_gate.py` (modify, add cases): with macOS unit baselines drawn from the recorded data (medians 235, 280
  and 306 s) and `min-delta 150`, durations 312.7, 332.0 and 370.2 s pass; 480 s against a 280 s median fails; 601 s
  fails on the ceiling even with the override label; cold start passes 370.2 s but still fails 601 s. Also a
  workflow-contract test: parse `.github/workflows/test-unit.yml` (yaml via stdlib/pyyaml as other tests do, or
  string-level assertions consistent with the suite's existing style), asserting the compare step name starts with
  `CI Timing Gate`, the per-OS min-delta values (120/150), the 600 ceiling, and the artefact name
  `ci-timing-test-unit-unit-<os>`.
- `test_final_head_timing_rerun.py` (new): using `FinalHeadSeams` fakes —
  - bookkeeping-only head (diff lists only `plans/…`, `executions/…`, `holon-knowledge/…`), timing-gate-only failure:
    exactly one rerun dispatched, re-probe returns success → record state `success` with
    `timing_rerun.outcome = success`; feeding it to `reverify_and_report` yields GREEN and **no** invalidation call.
  - same setup, rerun fails again: one dispatch only, record `failed`, reverify RED, invalidation posted (fake poster
    called once) — existing withdrawal applies.
  - code-changing head (diff includes a `src/…` path): no dispatch; record identical to today's failed record.
  - non-timing failed check (or mixed timing + unit-test failure): no dispatch.
  - unknown approved head / diff lister returns `None` / missing run id in the check link: no dispatch (fail-closed).
  - dispatch returns false: no re-probe, original failed record with `timing_rerun.attempted = false`.
  - rerun dispatched but checks stay failed past the registration window: no second dispatch (exactly-once).
- `test_ci_reverify.py`, `test_approval_invalidation.py` (modify, additive): report clause and comment line appear
  exactly when `timing_rerun` is present; byte-identical output when absent.
- `test_calibrate_final_head_ci.py` (modify only if needed): expected to pass unchanged thanks to additive seam
  defaults; any edit is limited to constructing seams with the new fields.

### Dependencies & Criticality

- **Depends on:** Steps 2, 3, 4
- **Is Bottleneck:** YES (acceptance criterion 4 — a test that cannot be made to pass means the implementation is wrong;
  `p = 0.88` ≥ 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** world/ruleset.md#3 (declared above); hermetic-testing guard (no network, fakes only — follow
  `hermetic_fixtures.py` / `review_fakes.py` conventions).
- **Potential failure modes for this step:** fixture shape drift against the real `probe_ci_checks` payload; flaky waits
  (avoided — fake clocks advance deterministically).
- **Guardrails and early‑abort checks:** model fake probe payloads on the shapes already used in
  `test_calibrate_final_head_ci.py`; no sleeps, no wall-clock dependence.

### Success & Discard Criteria

- **Success:**
  `uv run pytest apps/sandbox-executor/tests/test_ci_timing_gate.py test_final_head_timing_rerun.py test_ci_reverify.py test_approval_invalidation.py test_calibrate_final_head_ci.py`
  passes (paths relative as the suite requires), with every scenario of acceptance criteria 1–3 represented.
- **Discard:** A scenario that cannot be expressed through the seams signals a design hole in Step 3 → return to Step 3
  rather than weakening the test.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 2.5   |
| impact_pred         | 65.0  |
| cost_pred           | 8.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 50.5  |

### Step Metrics Rationale

Substantial but pattern-following test authoring against seams designed for it in Step 3.
`EV = 0.88 * 65.0 + 0.5 * 4.0 - 0.3 * 2.5 - 8.0 = 57.2 + 2.0 - 0.75 - 8.0 = 50.45`.

---

## Step 6: Full-suite verification, lint and docs lint

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mandatory closing gate of every plan; cheap relative to the suite it protects.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Acceptance criterion 5 holds: the whole suite, lint and docs lint pass on the execution branch.
- **Git branch:** I-1794976501-macgate/P-1794976517-cac841ef-claude-agent-claude-fable-5/E-{ts}-macgate-fix/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Run the affected tests first (Step 5's list) with `uv run pytest <paths>`, then the full suite once with
  `uv run task test` (which runs with `-n auto --dist worksteal`). No repeated timing runs: the intent's timing
  behaviour is exercised through fake clocks, not live measurement, so the test-run discipline's repetition clause does
  not apply. Report pass/fail counts and pytest-reported times for both runs.
- `uv run task lint` — includes ruff, the complexity gates and `check_flow_module_budgets.py` against the updated
  budgets file; `uv run task lint-docs` for the markdown (this plan file included).
- `git status` / diff review: confirm the only changed files are the Touch Set (plus flow-written plan/ledger/KB files),
  i.e. the scope deviation is exactly the one declared budgets-file edit.

### Dependencies & Criticality

- **Depends on:** Steps 2, 3, 4, 5
- **Is Bottleneck:** YES (acceptance criterion 5; `p = 0.90` ≥ 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** acceptance criteria 4–5; world/constraints.md#1 (commit scoped to this branch).
- **Potential failure modes for this step:** an unrelated test coupling to the modules' exact output; a budgets mismatch
  from miscounted lines.
- **Guardrails and early‑abort checks:** fix forward only within the Touch Set; any failure that demands an edit outside
  it stops the step and is reported as blocked, never silently widened.

### Success & Discard Criteria

- **Success:** Full suite green once, both lint tasks green, diff confined to the Touch Set.
- **Discard:** A failure fixable only outside the Touch Set → stop, report blocked with the file named (per the intent's
  instruction to say so rather than edit).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.0   |
| impact_pred         | 30.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 23.2  |

### Step Metrics Rationale

Routine verification; the suite is large (4,287 tests) but hermetic and parallel.
`EV = 0.90 * 30.0 + 0.5 * 1.0 - 0.3 * 1.0 - 4.0 = 27.0 + 0.5 - 0.3 - 4.0 = 23.2`.

---
