# Plan for I-1793246276-prompts-repeat-test-runs-only-for-performance-under-xdist

- **Plan ID:** P-1793246292-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-29T03:58:12.999Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A
- **Exploration stance:** conservative. The change is prose in two prompt templates and one doc page plus one test
  module; the intent fixes the wording, the touch set and the assertions, so there is little to explore and the main
  risk is lint or prettier drift.
- **Safety priority level:** standard
- **Priority Justification:** No sandbox, ledger or git-flow mechanism changes. The only safety-relevant content is the
  new commit-as-you-go directive, which must stay inside `holon-config/world/constraints.md` section 1 (commit only on
  the current branch, never touch `main` or other branches); the directive explicitly forbids push, rebase, amend,
  squash and branch switching, so standard priority is sufficient.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** The intent is fully specified (files, wording targets, assertions, touch set). Exploration would
  only add cost; the learning value comes from later Stage 3 run timings, not from this change.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.7   |
| impact_pred         | 70.0  |
| cost_pred           | 8.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 54.59 |

### Strategy Rationale

The intent is one coherent, low-entropy change: the same test-run rule goes into both templates and the testing guide,
the executor gains one commit-per-step directive, and one test module locks the wording in. Splitting would multiply
pipeline overhead without reducing risk, and the intent's own Q and A says it is one intent, so there is no sub-intent
list.

Order: templates first (Steps 1 and 2), then the doc (Step 3), then tests that assert on the shipped text (Step 4), then
one verification pass (Step 5). The tests depend on the exact phrases chosen in Steps 1 and 2, so Step 4 must reuse the
same key phrases verbatim; the plan fixes those phrases up front (see Plan Description) so the steps cannot drift apart.

Aggregation: cost and entropy are sums of step values (`1.5 + 2.0 + 1.0 + 2.0 + 1.5 = 8.0` and
`0.3 + 0.4 + 0.3 + 0.5 + 0.2 = 1.7`). Impact is a qualitative judgement (70): the change targets a measured waste of
about 27 of 75.7 minutes in one Stage 3 attempt and protects completed work against a late budget breach, but it is
prompt guidance, so agent compliance is not guaranteed. Overall `p_success` is set just below the bottleneck step (Step
4, 0.90) at 0.88 rather than the full product of step probabilities, because the steps are strongly correlated, and to
account for prettier rewrapping and a possible lint-docs failure on the templates.
`EV = 0.88 * 70.0 + 0.5 * 3.0 - 0.3 * 1.7 - 8.0 = 61.60 + 1.50 - 0.51 - 8.0 = 54.59`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` section 1 (commit boundaries: commit only on the current branch).
  - `holon-config/world/constraints.md` section 3 (ledgers under `holon-knowledge/ledger/` are append-only).
  - `holon-config/world/ruleset.md` section 3 (pytest as the runner; test changes must be declared in the plan, which
    Step 4 does).
  - `holon-config/world/ruleset.md` section 2 (docstrings and typing on new test helpers).
  - `docs/safety.md` principle 1 (git is the safety boundary; push and merge stay with the entrypoint and humans).
  - Intent TOUCH SET: only the four named files plus the flow's own ledger rows, `plans/` and `executions/` records.
- **Potential violations or edge cases:**
  - Curly braces in new template prose would break `test_shipped_templates_have_no_unmatched_placeholders` or be
    substituted by the renderer.
  - Prettier (`uv run task lint-docs`) may rewrap the new prose, splitting asserted phrases across lines.
  - The executor might be tempted to also edit `review_loop.template.md`, `docs/budgets.md` or an entrypoint module.
  - Harness paths (`.beans/`, `todo/`, `.agents/`) or review ids leaking into target files.
  - The commit directive could be read as permission to push or rebase.
- **Mitigations built into the plan:**
  - Explicit no-brace rule in Steps 1 and 2; Step 4 keeps the existing unmatched-placeholder test and asserts the
    rendered text still contains no leftover template names.
  - Step 4 normalises whitespace (collapse every run of whitespace to one space) before asserting, so prettier wrapping
    cannot break the tests.
  - Every step names its single file; any need for another file is dropped and reported in the execution notes.
  - The directive text itself says never push, rebase, amend, squash or switch branches.
- **Residual risk accepted (and why):** Agents may still ignore prompt guidance; that is inherent to a prompt-level fix
  and is cheaper than an enforcement mechanism, which would need entrypoint changes outside the touch set.
- **Allocated Entropy Budget:** 5.0
- **Predicted Plan Entropy:** 1.7
- **Budget Compliance:** The strategy fits within budget (`1.7 <= 5.0`).

## Plan Description & Strategy

Add a short, target-native "test-run discipline" rule to both prompt templates and the testing guide, add a
commit-as-you-go directive to the executor template, and pin the wording with tests that render the shipped templates.

Fixed key phrases (Steps 1 to 4 must use these verbatim, Australian English, no curly braces, prettier-safe):

- Performance-only condition: `only when the intent requires measuring performance or timing behaviour` (followed by the
  examples: test-suite speed, a timing-sensitive or flaky test, a stated performance target).
- Default order: run the affected tests first with `uv run pytest <paths>`, then the full suite once with
  `uv run task test`.
- Explicit N: `take N from the intent, or state N and the reason`, and `never open-ended or "many" runs`.
- Flag: `-n auto --dist worksteal` for every repetition, including runs under CPU load; serial runs only when serial
  behaviour is itself the measurement, and then once.
- Reporting: each run's pass/fail count and pytest-reported time, plus the `median`, from real runs only; never
  re-measure a commit that already has a recorded figure.
- Commit directive (executor only): `After each completed plan step, stage and commit the work on the current branch`
  with a short message naming the step; `Never push, rebase, amend, squash or switch branches`; the entrypoint pushes
  and records the execution.

Commit as you go during execution: one commit per completed step on the current branch, never push.

---

## Step 1: Add the test-run rule to the planner template

- **Sub‑intent recommendation:** NO
- **Reasoning:** A few lines of prose in one file; trivially reversible and tightly coupled to Steps 2 and 4.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Tell the in-container planner when repeated test runs may be planned and how they must run.
- **Git branch:** current execution branch (no new branch)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `holon-config/prompts/planner.template.md`, add a new bold-titled block, for example `**Test-Run Discipline:**`,
   directly after the "Entropy Budget and Decomposition Contract" bullets and before "You will be eliminated ...".
2. Write three or four short bullets using the fixed key phrases: plan repeated test runs only when the intent requires
   measuring performance or timing behaviour (list the three examples); otherwise plan the affected tests first with
   `uv run pytest <paths>` and then one full `uv run task test`; when repetition is required, take N from the intent, or
   state N and the reason in the plan, never open-ended or "many" runs; each repetition runs with
   `-n auto --dist worksteal`, including runs under CPU load, and serial runs happen only when serial behaviour is
   itself the measurement, and then once; report each run and the median.
3. Use no curly braces anywhere in the new text; put commands and flags in code spans; keep lines at or under the
   repository's prettier width.
4. Do not touch the plan-format code block or any existing placeholder.
5. Commit: `Step 1: add test-run rule to planner template`.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent TOUCH SET; `holon-config/world/ruleset.md` section 3.
- **Potential failure modes for this step:** braces introduced; harness paths mentioned; prettier reformat of the block.
- **Guardrails and early‑abort checks:** search the edited file for new `{` characters; run
  `uv run pytest apps/sandbox-executor/tests/test_prompt_templates.py` after the edit.

### Success & Discard Criteria

- **Success:** The template contains all planner key phrases and the existing template test still passes.
- **Discard:** If the rule cannot be expressed without editing another file, drop that part and report it.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.3   |
| impact_pred         | 20.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 0.5   |
| ev_pred             | 17.66 |

### Step Metrics Rationale

Pure prose addition with known wording.
`EV = 0.95 * 20.0 + 0.5 * 0.5 - 0.3 * 0.3 - 1.5 = 19.00 + 0.25 - 0.09 - 1.5 = 17.66`.

---

## Step 2: Replace the executor's bare-runner directive and add commit-as-you-go

- **Sub‑intent recommendation:** NO
- **Reasoning:** Single-file prose edit; the highest-impact part of the change but low risk and coupled to Step 4.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the executor run affected tests then one full xdist suite, apply the repetition rule, record
  runs honestly, and commit each completed step without pushing.
- **Git branch:** current execution branch (no new branch)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `holon-config/prompts/executor.template.md`, replace directive 3 (lines 10 to 11, the `pytest` or
   `.venv/bin/pytest` sentence) with: run the affected tests with `uv run pytest <paths>` first, then the full suite
   once with `uv run task test`, from the repository root.
2. Add the repetition rule as an indented sub-list or a following directive, using the same fixed phrases as Step 1
   (performance-only condition with examples, N from the plan or intent with stated reason, never open-ended or "many",
   `-n auto --dist worksteal` for each repetition including under CPU load, serial once only when serial behaviour is
   the measurement).
3. Add a recording directive: record every run's pass/fail count and pytest-reported time, plus the median, in the
   `## Tests Run` section of the execution notes, from real runs only; never re-measure a commit that already has a
   recorded figure.
4. Add a commit directive (new numbered directive, or a new item in the Write Protocol after "Append to a
   `## Steps Executed` section"): after each completed plan step, stage and commit the work on the current branch with a
   short message naming the step; never push, rebase, amend, squash or switch branches, because the entrypoint pushes
   and records the execution.
5. Renumber the directives consistently; ensure `.venv/bin/pytest` no longer appears anywhere in the file; no curly
   braces in new prose; keep all existing placeholders (`{repo_dir}`, `{plan_id}`, `{notes_path}`, `{exec_id}`,
   `{plan_content}`, `{intent_json}`, `{intent_id}`) untouched.
6. Commit: `Step 2: add test-run and commit-per-step rules to executor template`.

### Dependencies & Criticality

- **Depends on:** Step 1 (same wording)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` section 1; `docs/safety.md` principle 1; intent TOUCH SET
  (entrypoint modules off-limits).
- **Potential failure modes for this step:** directive implies push or rebase; placeholder accidentally altered;
  `.venv/bin/pytest` left in an example.
- **Guardrails and early‑abort checks:** search the file for `.venv/bin/pytest` and for every original placeholder after
  editing; re-run the template test module.

### Success & Discard Criteria

- **Success:** Template contains `uv run task test`, the fixed phrases, the commit-per-step and never-push wording, and
  all original placeholders; no `.venv/bin/pytest`.
- **Discard:** If honouring the directive appears to need an entrypoint change (for example git identity), do not edit
  the entrypoint; report it in the notes (a concurrent change owns it).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 0.4   |
| impact_pred         | 25.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 21.63 |

### Step Metrics Rationale

Slightly larger edit than Step 1 with placeholders to preserve, hence marginally lower `p_success`. Highest step impact
because the executor is where the 27 minutes of serial runs were spent.
`EV = 0.93 * 25.0 + 0.5 * 1.0 - 0.3 * 0.4 - 2.0 = 23.25 + 0.50 - 0.12 - 2.0 = 21.63`.

---

## Step 3: Document the repetition rule in the testing guide

- **Sub‑intent recommendation:** NO
- **Reasoning:** One short doc subsection in a single file, no code impact.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the human-facing testing guide state the same rule as the prompts.
- **Git branch:** current execution branch (no new branch)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `docs/testing/testing_guide.md`, directly after the "Run the unit selection the way CI does" bullet (around lines
   47 to 52) and before "Run a specific test file", add a short "Repeated runs" bullet or subsection.
2. State: default is affected tests first (`uv run pytest <paths>`) then one `uv run task test`; repeat only when
   measuring performance or timing behaviour (suite speed, a timing-sensitive or flaky test, a stated performance
   target); N comes from the intent or is stated with its reason, never open-ended; every repetition uses
   `-n auto --dist worksteal`, including under CPU load; serial only when serial behaviour is the measurement, once;
   report each run's pass/fail count and pytest-reported time plus the median, from real runs only, and do not
   re-measure a commit that already has a figure.
3. Optionally cite the motivating figures in neutral terms (about 30 s per serial run under load versus 16.97 s under
   `-n auto`) without any harness path or id.
4. Do not repair the pre-existing odd fence around line 41 unless `lint-docs` fails on it; it is in the touch set but
   unrelated, so leave it unless required.
5. Commit: `Step 3: document repeated-run rule in testing guide`.

### Dependencies & Criticality

- **Depends on:** Step 1 (wording)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent constraint 5 (Australian English, target-native paths, lint-docs).
- **Potential failure modes for this step:** prettier failure from list indentation; American spellings.
- **Guardrails and early‑abort checks:** run `uv run task lint-docs` after the edit (or `npx prettier --check` on the
  file via that task).

### Success & Discard Criteria

- **Success:** Guide contains the rule next to the xdist bullet and `uv run task lint-docs` passes.
- **Discard:** Prettier churn that keeps reformatting after two attempts: simplify to a plain paragraph.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.3   |
| impact_pred         | 8.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 6.76  |

### Step Metrics Rationale

Low-risk documentation. `EV = 0.95 * 8.0 + 0.5 * 0.5 - 0.3 * 0.3 - 1.0 = 7.60 + 0.25 - 0.09 - 1.0 = 6.76`.

---

## Step 4: Pin the rendered template wording with tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small test additions in one existing module; they are the acceptance gate for Steps 1 and 2.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Render the shipped planner and executor templates with the repository's own renderers and assert the
  required rule and directive text is present (and `.venv/bin/pytest` is absent).
- **Git branch:** current execution branch (no new branch)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_prompt_templates.py`, import `render_placeholders` from
   `sandbox_executor.entrypoint.planner` and `build_execution_prompt` from `sandbox_executor.entrypoint.executor`
   (import only; no source module is modified).
2. Add a small helper `rendered_planner() -> str`: read `holon-config/prompts/planner.template.md` from `REPO_ROOT` and
   render it with `render_placeholders`, mapping every name in `PLANNER_KEYS` to a dummy value.
3. Add `rendered_executor() -> str`: call `build_execution_prompt` with `repo_dir=str(REPO_ROOT)`, a dummy plan branch,
   `plan_data` with a dummy `plan_id`, a short dummy `plan_content`, `intent_data` with a dummy `intent_id`, and a dummy
   `exec_id`. This uses the real shipped template because `REPO_ROOT` contains it. If that import proves heavy or
   side-effecting at collection, fall back to `render_placeholders` with the executor's placeholder names and note why.
4. Add `normalised(text: str) -> str` that collapses all whitespace runs to a single space and lower-cases, so prettier
   rewrapping cannot break assertions.
5. Add a module constant tuple of shared rule phrases (lower-case): the performance-only condition phrase, the
   explicit-N phrase (`state n and the reason`), `-n auto --dist worksteal`, and `median`.
6. Add a parametrised test over both renderers asserting every shared phrase is in the normalised rendered text.
7. Add one executor test asserting `uv run task test` and `uv run pytest` are present, `.venv/bin/pytest` is absent, the
   commit-per-step phrase (`after each completed plan step, stage and commit`) is present and `never push` is present.
8. Add a check that rendering left no known placeholder unexpanded (for example `{plan_id}` and `{repo_dir}` absent in
   the rendered executor text).
9. Keep each test function short with no branching; docstrings in Australian English; type annotations on helpers; leave
   all four existing tests unchanged.
10. Run `uv run pytest apps/sandbox-executor/tests/test_prompt_templates.py` and record pass/fail count and time.
11. Commit: `Step 4: test planner and executor test-run wording`.

### Dependencies & Criticality

- **Depends on:** Steps 1 and 2
- **Is Bottleneck:** YES (it is the acceptance gate; failure means the wording does not match the intent)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` section 3 (declared test change, pytest only); intent constraint 5
  (no PYTHONPATH, no other runner).
- **Potential failure modes for this step:** phrase mismatch between template and test; `build_execution_prompt`
  signature differs from expectation; ruff format differences.
- **Guardrails and early‑abort checks:** if a phrase assertion fails, fix the template wording to the fixed key phrase
  rather than weakening the assertion; run `uv run task lint` after editing.

### Success & Discard Criteria

- **Success:** All tests in the module pass, including the existing unmatched-placeholder test.
- **Discard:** If the executor renderer cannot be called without touching a non-touch-set file, use the
  `render_placeholders` fallback and report it.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.5   |
| impact_pred         | 12.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 9.15  |

### Step Metrics Rationale

Bottleneck step: most failure modes (phrase drift, signature surprises) surface here, though all are cheap to fix.
`EV = 0.90 * 12.0 + 0.5 * 1.0 - 0.3 * 0.5 - 2.0 = 10.80 + 0.50 - 0.15 - 2.0 = 9.15`.

---

## Step 5: Verify once and record

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard single verification pass, following the very rule this change introduces.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the change passes the full unit suite and both lint tasks, with no repeated runs.
- **Git branch:** current execution branch (no new branch)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. From the repository root, run `uv run task test` exactly once (this intent does not measure performance, so no
   repetition).
2. Run `uv run task lint` and `uv run task lint-docs`; if prettier reports any of the four touched files, apply prettier
   to those files only and re-check; never reformat files outside the touch set.
3. Confirm with `git diff --name-only main...HEAD` (or the parent branch) that only the four touch-set files plus the
   flow's own `plans/`, `executions/` and appended ledger rows changed.
4. Record each run's pass/fail count and pytest-reported time in `## Tests Run` of the execution notes (one full-suite
   run, so its time is also the median).
5. Commit any final lint fix: `Step 5: lint fixes` (only if needed). Never push.

### Dependencies & Criticality

- **Depends on:** Steps 1 to 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent constraint 5; `holon-config/world/constraints.md` section 3.
- **Potential failure modes for this step:** unrelated pre-existing failures from concurrent changes; lint-docs flagging
  files outside the touch set.
- **Guardrails and early‑abort checks:** if a failure is outside the touch set, do not fix it; report it in the notes.

### Success & Discard Criteria

- **Success:** `uv run task test`, `uv run task lint` and `uv run task lint-docs` pass, and the diff is confined to the
  touch set.
- **Discard:** Failures attributable only to files outside the touch set are reported, not fixed.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.2   |
| impact_pred         | 5.0   |
| cost_pred           | 1.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 3.44  |

### Step Metrics Rationale

One xdist suite run plus two lint tasks; the main uncertainty is pre-existing breakage from concurrent work.
`EV = 0.90 * 5.0 + 0.5 * 1.0 - 0.3 * 0.2 - 1.5 = 4.50 + 0.50 - 0.06 - 1.5 = 3.44`.

---
