# Plan for I-1793319411-calibration-report-passes-repository-prettier-check

- **Plan ID:** P-1793319426-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-30T00:17:07.222Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A. The root cause was located during planning (see "Findings from planning"),
  so the FIND goal is reduced to a cheap re-verification step and the budget goes to FIX, GUARD and TESTS.
- **Exploration stance:** balanced. The defect is reproduced and understood, so implementation is exploit-mode; one
  small exploratory step checks how `npx` resolves an unversioned `prettier` (registry latest versus a stale npx cache),
  which is the only remaining way the calibration-time formatter could drift from CI again.
- **Safety priority level:** elevated
- **Priority Justification:** The change gates a git commit and push in Stage 5 (`holon-config/world/constraints.md`
  section 1, git flow) and the calibrate stage appends ACT-CAL rows to an append-only ledger
  (`holon-config/world/constraints.md` section 3). A wrong guard could either keep pushing red heads or strand ledger
  rows, so the guard must fail closed without touching the ledger contract.

## Exploration

- **Proportion of steps that are exploratory:** 0.17 (1 of 6 steps)
- **Justification:** Root cause is already reproduced; a single bounded probe of npx version resolution is enough to
  make the fix robust against the same class of drift, and it costs one or two shell commands.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 4.7   |
| impact_pred         | 80.0  |
| cost_pred           | 14.0  |
| learning_value_pred | 5.0   |
| ev_pred             | 52.69 |

### Strategy Rationale

Overall EV uses the repository formula with `lambda = 0.3` and `mu = 0.5` from `holon-config/metrics/ev_config.json`:
`EV = 0.82 * 80.0 + 0.5 * 5.0 - 0.3 * 4.7 - 14.0 = 65.60 + 2.50 - 1.41 - 14.0 = 52.69`.

- `p_success_pred` is a qualitative product of the critical-path steps (2, 3, 4, 6), roughly
  `0.95 * 0.92 * 0.90 * 0.95 = 0.75`, lifted to 0.82 because steps 2 to 4 are tightly coupled (one executor pass, same
  files, failures caught and fixed by step 6 rather than independent). Bottleneck: Step 4 (tests), because the calibrate
  stage test needs careful faking of `run_calibrate`, `run_git` and the formatter seam inside an already large
  `test_flow.py`.
- `entropy_pred` is the sum of step entropies: `0.3 + 1.4 + 1.0 + 1.2 + 0.5 + 0.3 = 4.7`, below the 7.0 budget.
- `cost_pred` is the sum of step costs: `1.0 + 4.0 + 2.5 + 4.5 + 1.0 + 1.0 = 14.0`.
- `impact_pred` is high: every calibrated flow PR (#99 to #111) went red after Stage 5 and needed a manual commit.
- `learning_value_pred` is modest: the root cause is known; learning comes from the npx resolution probe and from
  establishing a reusable "same invocation as lint-docs" guard pattern.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` section 1: work only on this plan/execution branch prefix; never commit to
    `main`.
  - `holon-config/world/constraints.md` section 3: the ledgers stay append-only; exactly the three ledgers
    (`intents.jsonl`, `plans.jsonl`, `executions.jsonl`) remain; no new ledger file.
  - `holon-config/world/ruleset.md` section 2: typed public functions with docstrings (inputs, outputs, exceptions, side
    effects).
  - `holon-config/world/ruleset.md` section 3: pytest only; new or changed test files are declared here (Step 4).
  - `docs/safety.md`: sandboxed execution, whitelisted subprocesses; tests must not touch Docker, gh, git remotes or LLM
    runners.
  - Intent constraints: Australian English, no new dependencies, short low-branching functions, `uv run task lint` and
    `uv run task lint-docs` pass, touch set limited to `calibration.py`, the calibrate-stage section of `flow.py`, their
    tests and docs that describe Stage 5 formatting.
- **Potential violations or edge cases:**
  - Editing `formatting.py` (shared with planner and executor) would leave the touch set; it is NOT edited.
  - Failing closed when `npx` is missing changes behaviour on machines without Node; this is the requested behaviour.
  - A failed guard inside `run_calibrate` happens after the ACT-CAL ledger row could be written; ordering must keep the
    check before the ledger append so no orphan row is created.
  - Tests that accidentally spawn real `npx` (network) would break hermetic testing.
- **Mitigations built into the plan:**
  - The format-and-check runs immediately after the final write and before `record_calibration_action`, `git add` and
    `git commit`.
  - A single module-level subprocess seam in `calibration.py` is patched by every new test; an assertion in the tests
    fails if the real `subprocess.run` is reached.
  - A drift test reads `lint-docs` from the root `pyproject.toml` and asserts the calibration invocation uses the same
    executable and package spec, so a future pin in either place is caught by pytest.
- **Residual risk accepted (and why):** `npx prettier` without a version can resolve differently on a machine with a
  stale npx cache than in fresh CI. Step 5 measures this; the guard still uses exactly the lint-docs invocation, so the
  report always passes the local lint-docs, and the resolved version is logged for diagnosis.
- **Allocated Entropy Budget:** 7.0
- **Predicted Plan Entropy:** 4.7
- **Budget Compliance:** The strategy fits within budget (4.7 of 7.0), executed as one plan without sub-intents, as the
  intent's answered question requires.

## Plan Description & Strategy

### Findings from planning (goal 1, FIND)

- Stage 5 writes the report in `run_calibrate` (`calibration.py` about lines 1461 to 1469) and then calls
  `converge_prettier([report_rel], repo_dir=repo_dir)` from `sandbox_executor.formatting`. The fallback path in
  `run_calibrate_stage` (`flow.py` about lines 4615 to 4630) calls the same helper.
- `converge_prettier` runs `npx --yes prettier@3.8.4 --write` then `npx --yes prettier@3.8.4 --check` with
  `cwd=repo_dir`. Config resolution is correct (the repository `.prettierrc.json` with `printWidth: 120` and
  `proseWrap: always` is found from the report's directory) and the format happens after the final write of the report.
  The ACT-CAL step only touches `executions.jsonl`, never the report.
- `uv run task lint-docs` runs `npx prettier --check "**/*.md"` and the CI hygiene job runs `make lint` (target
  `lint-md`, the same `npx prettier --check "**/*.md"`) on Node 24 with no `package.json`, so it resolves the registry
  latest Prettier, currently 3.9.10.
- Reproduced in planning: `npx --yes prettier@3.8.4 --check "plans/*_calibration.md"` flags 20 committed (manually
  fixed) calibration reports, while `npx prettier --check` (3.9.10) passes all of them. The difference is prose wrapping
  around inline maths such as `$p\_success\_error = 0.20$`: 3.8.4 breaks the line inside the `$...$` span, 3.9.10 keeps
  the repository style. "Prettier converged" was logged truthfully, but against the wrong Prettier version.
- Root cause: **Prettier version skew** (pinned 3.8.4 at calibration time versus unpinned latest in lint-docs and CI),
  not the config file and not the write ordering.

### Strategy

Add a small calibration-local formatter in `calibration.py` that uses exactly the lint-docs invocation (`npx prettier`
with the repository config discovered from `repo_dir`; `--yes` only suppresses the interactive install prompt and does
not change version resolution), run `--write` then `--check` on the report after the final write, and raise a dedicated
`CalibrationFormatError` before any ledger append, `git add` or commit when the check fails or cannot run. The calibrate
stage in `flow.py` turns that error into a FAILED Stage 5 with a clear message and never pushes. `formatting.py` stays
untouched because it is outside the touch set and is shared by planner and executor. No existing doc describes Stage 5
formatting (checked with a search for Prettier mentions in `docs/` and `apps/sandbox-executor/`), so no doc is edited;
the behaviour is documented in the new docstrings.

---

## Step 1: Re-verify the version-skew root cause on the execution branch

- **Sub‑intent recommendation:** NO
- **Reasoning:** Two read-only shell commands; confirms the planning finding still holds before code changes.
- **Step Type:** INFO_GATHERING
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Confirm that the pinned 3.8.4 formatter and the lint-docs Prettier disagree on an existing
  calibration report, so the fix targets the right cause.
- **Git branch:**
  I-1793319411-calibration-report-passes-repository-prettier-check/P-1793319426-claude-agent-claude-opus-5-5/E-{timestamp}-fix/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. From the repository root run `npx prettier --version` and `npx --yes prettier@3.8.4 --check` on one committed report,
   for example `plans/P-1793275710-claude-agent-claude-opus-5-5_calibration.md`.
2. Copy that report and `.prettierrc.json` into a temporary directory outside the repository, run the 3.8.4 `--write` on
   the copy and diff it, to see the re-wrap around inline maths. Use the diffed pair as the shape of the test fixture in
   Step 4 (a paragraph wrapped by 3.8.4 that the repository style keeps on one line, and vice versa).
3. Make no repository changes in this step.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` (whitelisted subprocesses), `holon-config/world/constraints.md` section 2.
- **Potential failure modes for this step:** no network for `npx`; both versions agree on the sample.
- **Guardrails and early‑abort checks:** if `npx` cannot reach the registry, record that and continue on the planning
  finding; if both versions agree, fall back to checking config and write order before Step 2.

### Success & Discard Criteria

- **Success:** 3.8.4 reports the sample as unformatted while the lint-docs invocation passes it.
- **Discard:** more than 10 minutes spent, or the skew cannot be reproduced and no other cause is found.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.3   |
| impact_pred         | 10.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 9.41  |

### Step Metrics Rationale

Already reproduced during planning, so success is very likely and entropy is minimal.
`EV = 0.95 * 10.0 + 0.5 * 2.0 - 0.3 * 0.3 - 1.0 = 9.50 + 1.00 - 0.09 - 1.0 = 9.41`.

---

## Step 2: Format and guard the report in calibration.py with the lint-docs invocation

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, contained change in one module; it is the core fix and shares context with Steps 3 and 4.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Format the calibration report after its final write with exactly the lint-docs Prettier invocation
  and fail closed before any ledger append or commit when the check does not pass.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add a module constant in `calibration.py`, for example `LINT_DOCS_PRETTIER`, holding the argv prefix `npx`, `--yes`,
   `prettier` (no version pin), with a comment in Australian English stating that it mirrors `lint-docs` in the root
   `pyproject.toml` and the `lint-md` Makefile target, and why the old pin caused red CI.
2. Add `class CalibrationFormatError(CalibrationCommitError)` with a docstring: raised when the calibration report does
   not pass the repository Prettier check; subclassing keeps every existing caller that already treats
   `CalibrationCommitError` as "nothing committed, nothing pushed" correct.
3. Add a private seam `_run_prettier(mode, report_rel, repo_dir)` that runs `LINT_DOCS_PRETTIER` plus `--write` or
   `--check` plus the relative report path with `cwd=repo_dir`, `capture_output=True`, `text=True` and a bounded
   timeout, returning the `CompletedProcess`. This is the single function tests patch.
4. Add `format_calibration_report(report_rel, repo_dir) -> None` (public, typed, full docstring): run the write pass,
   then the check pass; when `npx` is absent (`shutil.which`), the subprocess raises `OSError` or `TimeoutExpired`, or
   the check exits non-zero, raise `CalibrationFormatError` with a redacted message naming the report, the exact command
   to reproduce (`npx prettier --check <report>`) and the first lines of Prettier output. Keep branching low by
   collecting the failure reason in one helper and raising once. On success log at info level that the report passes the
   repository Prettier check (Australian English).
5. In `run_calibrate`, replace the `converge_prettier` try/except right after the report write with:
   `format_calibration_report` when `skip_commit` is false (errors propagate before `record_calibration_action`,
   `git add` and `git commit`); when `skip_commit` is true, call it inside a narrow `except CalibrationFormatError` that
   only logs a warning, because nothing is committed in a dry run.
6. Remove the now-unused `converge_prettier` import from `calibration.py` if no other caller in the module remains;
   leave `formatting.py` untouched.
7. Update the `run_calibrate` docstring `Raises:` section with `CalibrationFormatError`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` section 2 (typing, docstrings), intent constraint "keep functions
  short and low in branching", `holon-config/world/constraints.md` section 3 (no orphan ledger row).
- **Potential failure modes for this step:** the guard placed after `record_calibration_action` leaves an uncommitted
  ACT-CAL row; `ruff` complexity or line-length failures; message text leaking secrets.
- **Guardrails and early‑abort checks:** place the call strictly before the ledger append; pass the message through
  `CredentialRedactor.redact_text`; run `uv run task lint` right after the edit.

### Success & Discard Criteria

- **Success:** `run_calibrate` either commits a report that passed `npx prettier --check` or raises
  `CalibrationFormatError` before any ledger append or git command; lint passes.
- **Discard:** the change needs edits to `formatting.py` or callers outside the touch set to work.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.4   |
| impact_pred         | 45.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 38.83 |

### Step Metrics Rationale

Well-understood change in a single function plus two small helpers.
`EV = 0.95 * 45.0 + 0.5 * 1.0 - 0.3 * 1.4 - 4.0 = 42.75 + 0.50 - 0.42 - 4.0 = 38.83`.

---

## Step 3: Fail Stage 5 instead of pushing when the guard trips (flow.py calibrate stage)

- **Sub‑intent recommendation:** NO
- **Reasoning:** A few lines in one function, tightly coupled to Step 2; separating it would add branch overhead only.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make `run_calibrate_stage` return a FAILED Stage 5 with a clear message, and never push or
  fast-forward, when `run_calibrate` raises `CalibrationFormatError`.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE
- **Touch limit:** only the calibrate-stage section of `flow.py` where the report is formatted and committed.

### Implementation Details (No code blocks, only logic/steps)

1. Import `CalibrationFormatError` and `format_calibration_report` from `sandbox_executor.calibration` in `flow.py`.
2. In `run_calibrate_stage`, add an `except CalibrationFormatError as e` clause after the existing `LedgerRewriteError`
   clause and before the generic `Exception` clause. It returns
   `_stage_failure(context, FlowStage.CALIBRATE, start_time, message)` where the message (Australian English) says the
   calibration report failed the repository Prettier check, that nothing was committed or pushed, and includes the
   redacted reason. This bypasses the fallback report, the reconciliation commit and the push block.
3. In the fallback path, replace `converge_prettier([report_rel], repo_dir=repo_dir)` with `format_calibration_report`,
   keeping the existing warning-only handling (the fallback report is never committed or pushed), so both Stage 5 paths
   use the same invocation. Remove the `converge_prettier` import from `flow.py` only if no other use remains in the
   module.
4. Do not change the push block itself: the existing `calibration_succeeded` gate already prevents pushes on failure;
   the new early return makes the format failure explicit.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` section 1 (no red heads pushed to shared branches), intent
  touch set.
- **Potential failure modes for this step:** the exception being caught by the generic clause because of order; the
  early return skipping a cleanup the stage relied on (for example returning to the execution branch).
- **Guardrails and early‑abort checks:** check what the existing `CalibrationCommitError` path leaves behind (HEAD on
  the `/calibrated` branch, uncommitted files) and mirror it; Step 4 asserts the clause order through behaviour.

### Success & Discard Criteria

- **Success:** with `run_calibrate` raising `CalibrationFormatError`, Stage 5 status is FAILED, the error names the
  report and the Prettier check, and `run_git` never receives `push`.
- **Discard:** the stage needs restructuring beyond the calibrate-stage section to fail cleanly.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.0   |
| impact_pred         | 25.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 20.70 |

### Step Metrics Rationale

Small, clearly located change; slightly lower success than Step 2 because `flow.py` is large and the stage has several
side-effect paths. `EV = 0.92 * 25.0 + 0.5 * 1.0 - 0.3 * 1.0 - 2.5 = 23.00 + 0.50 - 0.30 - 2.5 = 20.70`.

---

## Step 4: Add hermetic pytest coverage for formatting, guard and calibrate stage

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tests are the bottleneck but belong with the code they cover; a sub-intent would only add merge
  overhead.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the three required behaviours without real Docker, gh, git remotes, LLM runners or `npx`.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE
- **Declared test file changes:** `apps/sandbox-executor/tests/test_calibration.py` and
  `apps/sandbox-executor/tests/test_flow.py` (new tests only; existing assertions are kept; existing tests that patch
  `sandbox_executor.calibration.converge_prettier` are switched to patch `format_calibration_report` only where the old
  patch target no longer exists).

### Implementation Details (No code blocks, only logic/steps)

1. In `test_calibration.py`, add a fixture report string whose prose needs re-wrapping (shape from Step 1: an over-long
   line with inline maths, wrapped the 3.8.4 way) and its expected repository-style form at width 120. Fake
   `_run_prettier` so the `--write` call writes the expected form to the file in `tmp_path` and the `--check` call
   returns exit 0. Assert the file content equals the repository-style form and that both recorded argv lists start with
   `npx`, `--yes`, `prettier` and end with the relative report path, with `cwd` equal to the repository directory.
2. Add a drift test: parse the root `pyproject.toml` with `tomllib`, read `tool.taskipy.tasks.lint-docs`, and assert its
   executable and package spec (`npx`, `prettier`, no `@version`) match `LINT_DOCS_PRETTIER` once `--yes` is ignored.
3. Guard fails closed, parametrised over: check exits 1; `_run_prettier` raises `TimeoutExpired`; `_run_prettier` raises
   `FileNotFoundError`; `shutil.which("npx")` returns None. Each case asserts `CalibrationFormatError` is raised and the
   message names the report and the reproduce command.
4. In `run_calibrate` with `skip_commit=False`, patch `generate_calibration`, `run_git`, `record_calibration_action` and
   make the check fail: assert `CalibrationFormatError` propagates, `record_calibration_action` is never called and no
   `run_git` call contains `add` or `commit`. With `skip_commit=True` the same failure only logs a warning and returns
   the report.
5. In `test_flow.py`, add a calibrate-stage test: build a `FlowContext` the way neighbouring Stage 5 tests do, patch
   `run_calibrate` to raise `CalibrationFormatError`, patch `run_git` and the checkout helper. Assert the StageResult is
   FAILED, the error mentions the Prettier check, and no `run_git` call contains `push` or `merge`. Add a companion case
   where `run_calibrate` succeeds to show the push still happens on the happy path (regression guard).
6. Add an autouse safety assertion in the new test class (or reuse `hermetic_fixtures.py`) that fails if real
   `subprocess.run` is invoked with `npx`.
7. Run the affected files first:
   `uv run pytest apps/sandbox-executor/tests/test_calibration.py apps/sandbox-executor/tests/test_flow.py apps/sandbox-executor/tests/test_formatting.py`.

### Dependencies & Criticality

- **Depends on:** Steps 2 and 3
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` section 3 (pytest, declared test changes),
  `apps/sandbox-executor/docs/hermetic_testing.md`, intent rule "never name any other test runner".
- **Potential failure modes for this step:** existing tests patching the removed `converge_prettier` import break;
  `FlowContext` construction in `test_flow.py` is heavy; a test accidentally reaches real `npx`.
- **Guardrails and early‑abort checks:** search for every patch of `sandbox_executor.calibration.converge_prettier` and
  `sandbox_executor.flow.converge_prettier` before editing; reuse existing Stage 5 test helpers; the autouse assertion
  catches real subprocess calls.

### Success & Discard Criteria

- **Success:** new tests pass, existing tests in the three files pass, no real subprocess or network access.
- **Discard:** the calibrate-stage test needs more than about 80 lines of new scaffolding; then prefer a narrower test
  that patches at the `run_calibrate` boundary only.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.2   |
| impact_pred         | 20.0  |
| cost_pred           | 4.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 13.64 |

### Step Metrics Rationale

Most effort and most moving parts of the plan, hence the bottleneck.
`EV = 0.90 * 20.0 + 0.5 * 1.0 - 0.3 * 1.2 - 4.5 = 18.00 + 0.50 - 0.36 - 4.5 = 13.64`.

---

## Step 5: Probe how npx resolves an unversioned prettier (exploratory)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Two shell commands with no repository change; cheap insurance against a repeat of the same drift.
- **Step Type:** EXPLORATION
- **Exploration level:** EXPLORATORY

- **Hypothesis being tested:** On the calibration host, `npx --yes prettier` resolves the same version as fresh CI
  (registry latest), not an older copy from the npx cache.
- **Learning target:** whether the unpinned lint-docs invocation is stable across hosts, and whether a follow-up intent
  should pin Prettier repository-wide (for example in both `lint-docs` and `lint-md`, which is outside this touch set).
- **Maximum acceptable cost for this learning:** 1.0 cost units (about five minutes); never more than two commands.

### Intent & Git Integration

- **Step Intent:** Measure version resolution and run the new formatter once end to end on a temporary copy, outside the
  test suite.
- **Git branch:** same execution branch as Step 1 (no commit from this step)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Compare `npx --yes prettier --version` with `npm view prettier version`.
2. In a temporary directory holding a copy of `.prettierrc.json` and the 3.8.4-wrapped report from Step 1, call
   `format_calibration_report` once from a `uv run python -I -c` one-liner, then run `npx prettier --check` on the copy.
3. Record the result in the execution report; if versions differ, note a follow-up intent to pin Prettier in
   `pyproject.toml` and the Makefile together, without doing it here.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` (whitelisted subprocesses, network), intent touch set.
- **Potential failure modes for this step:** no registry access; temporary files left in the repository.
- **Guardrails and early‑abort checks:** work only in a system temporary directory; skip the step if the network is
  unavailable and say so.

### Success & Discard Criteria

- **Success:** versions recorded and the end-to-end copy passes `npx prettier --check`.
- **Discard:** network unavailable or cost above 1.5 units.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 0.5   |
| impact_pred         | 5.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 4.85  |

### Step Metrics Rationale

Low impact on the fix itself, but it resolves the one remaining uncertainty and informs a possible follow-up.
`EV = 0.80 * 5.0 + 0.5 * 4.0 - 0.3 * 0.5 - 1.0 = 4.00 + 2.00 - 0.15 - 1.0 = 4.85`.

---

## Step 6: Validate with the full suite, lint and lint-docs

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard closing gate; no design decisions.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Confirm nothing regressed and the branch passes the same checks as CI hygiene.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run the full suite once from the repository root with `uv run task test` (no `PYTHONPATH`, no repetition: the intent
   has no timing goal).
2. Run `uv run task lint` and `uv run task lint-docs`; fix any finding inside the touch set only.
3. Confirm `holon-knowledge/ledger/` still holds exactly `intents.jsonl`, `plans.jsonl` and `executions.jsonl`, with
   only appended lines.
4. Grep the diff for references to the coordinating harness, its directories or task-tracker ids, and for American
   spellings in new comments, docstrings and log strings (for example "normalize", "behavior", "color").

### Dependencies & Criticality

- **Depends on:** Steps 2, 3 and 4
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` section 3, intent constraints on lint, lint-docs and
  Australian English.
- **Potential failure modes for this step:** unrelated pre-existing failures; lint-docs failing on files owned by the
  concurrent documentation change.
- **Guardrails and early‑abort checks:** compare any failure against `main`; do not fix files outside the touch set.

### Success & Discard Criteria

- **Success:** full suite green, `uv run task lint` and `uv run task lint-docs` exit 0, ledger set unchanged.
- **Discard:** failures that originate outside the touch set are reported, not fixed.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.3   |
| impact_pred         | 10.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 8.66  |

### Step Metrics Rationale

Mechanical gate with high success probability.
`EV = 0.95 * 10.0 + 0.5 * 0.5 - 0.3 * 0.3 - 1.0 = 9.50 + 0.25 - 0.09 - 1.0 = 8.66`.

---

## Sub-intent list

None
