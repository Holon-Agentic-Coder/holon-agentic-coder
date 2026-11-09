# Plan for I-1794220515-run-ci-on-pull-requests-into-any-branch

- **Plan ID:** P-1794220535-37b66e75-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-09T10:35:35.796Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A. The intent is small, well scoped and directly unblocks every decomposed
  flow. One clarification is made, not a reframe: the planning audit already found that the Stage 4 check and
  mergeability resolution is base-agnostic (see Plan Description), so goal 4 is expected to land as tests that pin that
  behaviour, with source changes only if the executor finds a concrete `main` assumption that the planning audit missed.
- **Exploration stance:** balanced, leaning conservative. The workflow edit is pure exploitation; the one exploratory
  element is a hermetic, real-git mergeability test in a repository that has no `main` branch at all, which is the
  cheapest way to prove empirically that nothing on the Stage 4 path silently falls back to `main`.
- **Safety priority level:** elevated
- **Priority Justification:** The change touches CI triggers, which are the evidence source for the human review gate
  (`docs/safety.md` "Human review is the final gate" and Invariant 4). A mistake could either silence checks (a filter
  returning) or double-run them; both degrade the evidence a human maintainer merges on. No ledger, sandbox or trust
  boundary is touched, so `critical` is not warranted.

## Exploration

- **Proportion of steps that are exploratory:** 0.2
- **Justification:** One of five steps (Step 3) carries real uncertainty: whether any `main` assumption hides in the
  Stage 4 path. The planning audit says no, and the step converts that belief into an executable, hermetic proof. The
  remaining steps are deterministic edits with clear success signals.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 5.8   |
| impact_pred         | 85.0  |
| cost_pred           | 15.0  |
| learning_value_pred | 5.0   |
| ev_pred             | 53.76 |

## Plan Description & Strategy

### Findings from the planning audit (read-only)

- **Workflows.** `docker-build.yml`, `make.yml`, `test-hygiene.yml`, `test-integration.yml`, `test-stress.yml` and
  `test-unit.yml` each have `push: branches: [main]` and a `pull_request` block with
  `types: [opened, synchronize, reopened, labeled, unlabeled]` followed by `branches: [main]`. Five have a `concurrency`
  block keyed on `github.workflow` and `github.ref` with `cancel-in-progress` only for pull requests; `docker-build.yml`
  has none and must not gain one (no behaviour change beyond the trigger). `build-images.yml` is `workflow_dispatch`
  only and stays untouched.
- **No double runs.** Because `push` stays limited to `main`, a push to an intent branch fires only the `pull_request`
  event of its open stacked pull request, never a second `push` run. A push to `main` fires only `push` (no pull request
  has `main` as its head). So removing the `pull_request` base filter introduces no commit that is checked twice. The
  `concurrency` group keys on `github.ref` (`refs/pull/N/merge` for pull requests), so stacked pull requests never
  cancel each other or `main` runs.
- **Stage 4 resolution is base-agnostic.** `flow/review/loop.py` reads `target_branch` from `context.intent_data` (line
  215), and `flow/stages/decomposition.py` sets a child's `target_branch` to the parent intent branch (line 191). That
  value flows into `create_or_update_pr` (`gh pr create --base <target_branch>`), `check_pr_mergeability`
  (`gh pr view <head>`, whose result already reflects the pull request's real base; the local fallback tries
  `origin/<target_branch>` then `<target_branch>`) and `classify_review_diff`. `flow/github/ci.py` probes
  `gh pr checks <head>` and never names a base. The `"main"` strings in `flow/github/pr.py` are only parameter defaults
  that the loop always overrides.
- **Out-of-scope `main` literals (recorded, not changed).** `entrypoint/review_loop.py:787` and
  `entrypoint/planner.py:284` pass `target_branch="main"` to `check_and_resolve_stage_conflicts`. They are container
  entrypoints outside the intent's `scope` and outside the Stage 4 host check resolution, so changing them would be a
  behaviour change the intent forbids. They are noted in the PR body as a follow-up candidate.
- **Parsing.** No YAML parser is a project dependency (`uv.lock` has no `pyyaml`), so the guard test uses a small
  standard-library, indentation-aware reader of the `on:` block.
- **Budgets.** `flow/review/loop.py` is at 960 of a 962-line budget, `flow/github/pr.py` at 515 of 515 and
  `flow/github/ci.py` at 228 of 228. The plan therefore does not grow these modules; any fix the executor is forced to
  make must be budget-neutral or move code into a focused module.

### Strategy

Five sequential steps on a single execution branch: (1) remove the six `pull_request` base filters; (2) add a
standard-library guard test that pins "no base filter on `pull_request`" and "push stays on `main`"; (3) audit-confirm
and pin Stage 4 behaviour for a stacked pull request with hermetic tests in a new focused test module; (4) update the CI
docs; (5) run the affected tests, the full suite once, and both lint tasks. Total entropy is far below the 15.0 budget
and the steps share one small touch set, so no sub-intent list is used.

### Touch set (module granularity)

- `.github/workflows/docker-build.yml`, `make.yml`, `test-hygiene.yml`, `test-integration.yml`, `test-stress.yml`,
  `test-unit.yml` (edit: trigger only)
- `.github/workflows/README.md` (edit)
- `docs/git_flow.md` (edit: Stage 4 CI note)
- `apps/sandbox-executor/tests/test_workflow_triggers.py` (new)
- `apps/sandbox-executor/tests/test_review_stacked_pr.py` (new)
- `apps/sandbox-executor/src/sandbox_executor/flow/github/` and `flow/review/` (no change expected; contingent only, see
  Step 3)

---

## Step 1: Remove the base-branch filter from the pull_request trigger of the six CI workflows

- **Sub‑intent recommendation:** NO
- **Reasoning:** Six identical two-line deletions with a deterministic outcome; splitting would add pipeline cost with
  no risk reduction.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the six CI workflows run for pull requests into any base branch while leaving every other
  trigger property untouched.
- **Git branch:**
  `I-1794220515-run-ci-on-pull-requests-into-any-branch/P-1794220535-37b66e75-claude-agent-claude-opus-5-5/E-{timestamp}-execute/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In each of `docker-build.yml`, `make.yml`, `test-hygiene.yml`, `test-integration.yml`, `test-stress.yml` and
   `test-unit.yml`, delete only the `branches:` key and its `- main` item that sit under `pull_request:`.
2. Leave `push: branches: [main]`, the `types:` list, `workflow_dispatch:`, `permissions:` and every `concurrency:`
   block byte-for-byte unchanged. Do not add `concurrency` to `docker-build.yml`.
3. Do not touch `build-images.yml`.
4. Review with `git diff --stat .github/workflows` that exactly six files changed, each with exactly two deleted lines
   and no added lines.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (it is the change that actually gives stacked pull requests checks; p_success is high)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` Human review is the final gate; Invariant 4 (Human review);
  `holon-config/world/constraints.md` section 1 (only humans merge to `main`).
- **Potential failure modes for this step:** deleting the `push` filter instead of the `pull_request` one (would run CI
  twice per stacked commit); YAML indentation damage; accidentally touching `build-images.yml`.
- **Guardrails and early‑abort checks:** the diff-shape check above; Step 2's guard test also asserts that `push`
  remains restricted to `main`, so a wrong deletion fails a test.

### Success & Discard Criteria

- **Success:** six files changed, `-2/+0` lines each; no `pull_request` block carries `branches` or `branches-ignore`;
  each `push` block still lists only `main`.
- **Discard:** any other line in the workflows differs, or more than six files change; revert and redo.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 1.0   |
| impact_pred         | 85.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 81.15 |

### Step Metrics Rationale

`EV = 0.97 * 85.0 + 0.5 * 2.0 - 0.3 * 1.0 - 2.0 = 82.45 + 1.00 - 0.30 - 2.00 = 81.15`. Mechanical edit, highest impact
because it is the direct unblocker; the only real uncertainty is live-GitHub behaviour, which cannot be observed
in-sandbox and is reasoned about in the Plan Description.

---

## Step 2: Add a standard-library guard test for workflow triggers

- **Sub‑intent recommendation:** NO
- **Reasoning:** One new, self-contained test module with no production dependency; low risk, cheap, and its value is
  preventing regression of Step 1.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make it impossible for a base-branch filter to return to any `pull_request` trigger without a test
  failing, and pin the push-to-`main`-only property.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `apps/sandbox-executor/tests/test_workflow_triggers.py` with a module docstring that explains why (stacked
   sub-intent pull requests target their parent intent branch and must still get checks).
2. Locate the repository root from the test file path (walk up until a directory containing `.github/workflows` exists),
   keeping the path target-native via `pathlib`; collect every `*.yml` and `*.yaml` there, sorted, and assert the list
   is non-empty so a moved directory cannot make the test vacuous.
3. Implement a small private reader using only the standard library: find the top-level `on:` key (also accept the
   quoted form `"on":` and the YAML 1.1 alias `true:` that some parsers produce), then collect each event key at the
   first child indent and the set of keys directly under it, ignoring comments and blank lines. Also handle the flow
   forms `on: pull_request` and `on: [push, pull_request]` (event present, no filters).
4. Parametrise one test over every workflow file: if `pull_request` or `pull_request_target` is an event, assert that
   neither `branches` nor `branches-ignore` is among its keys, with a failure message naming the file and the reason.
5. A second parametrised test: when `push` is an event and has a `branches` key, its listed values (block list or flow
   list) must be exactly `main`, so goal 1's "push stays limited to `main`" is pinned too.
6. A third test feeds the reader small in-memory YAML snippets (filter present, filter absent, flow-list form,
   `branches-ignore`, comment lines) so the reader itself is verified and the guard cannot pass because of a parser bug.
7. Use only `pathlib`, `re` and `pytest`; no network, no subprocess, no environment reads, so the test is hermetic and
   runs in the default unit selection (no marker).
8. Keep helpers within ruff complexity limits (`C901 <= 10`, `PLR0912 <= 12`) without `noqa`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO (acceptance criterion 3 depends on it, but a failure is fixable within the step)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` section 3 (test changes declared in the plan; declared here);
  intent constraint "no new dependencies".
- **Potential failure modes for this step:** a reader that misses an indentation style and passes vacuously; picking up
  `pull_request` keys inside job steps rather than under `on:`.
- **Guardrails and early‑abort checks:** before Step 1 is applied (or by temporarily stashing it during development),
  the test must fail on the original workflows naming all six files; the snippet tests in item 6 guard the reader.

### Success & Discard Criteria

- **Success:** the test fails against the pre-change workflows for exactly the six files and passes after Step 1;
  `uv run pytest apps/sandbox-executor/tests/test_workflow_triggers.py` is green.
- **Discard:** if a robust stdlib reader exceeds about 80 lines or the complexity limits, simplify to a line-based scan
  of the `on:` block only and document its limits in the docstring.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.2   |
| impact_pred         | 60.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 53.34 |

### Step Metrics Rationale

`EV = 0.92 * 60.0 + 0.5 * 3.0 - 0.3 * 1.2 - 3.0 = 55.20 + 1.50 - 0.36 - 3.00 = 53.34`. Slightly lower p_success than
Step 1 because hand-rolled YAML reading is the classic source of vacuous tests; the fail-first check mitigates that.

---

## Step 3: Confirm and pin Stage 4 check and mergeability resolution for a stacked pull request

- **Sub‑intent recommendation:** NO
- **Reasoning:** The planning audit found no `main` assumption in the Stage 4 host path, so this is expected to be
  test-only work in one new module; if the executor finds otherwise, the fix is small and stays in the same branch.
- **Step Type:** TEST
- **Exploration level:** BALANCED
- **Hypothesis being tested:** For a child intent whose `target_branch` is its parent intent branch, the review loop
  creates the pull request against that branch, evaluates mergeability against it (both via `gh` and via the local git
  fallback), waits for CI checks to register and settle, reads them, and records them in the ledger and the result,
  without ever referencing `main`.
- **Learning target:** empirical confirmation (or refutation) that nothing in `flow/github/` or `flow/review/` falls
  back to `main`; a refutation becomes a precise, budget-neutral fix.
- **Maximum acceptable cost for this learning:** about 6 cost units; if a required source fix would push
  `flow/review/loop.py` or `flow/github/pr.py` past its line budget, stop and record it as a follow-up instead.

### Intent & Git Integration

- **Step Intent:** Cover the Stage 4 check and mergeability resolution with hermetic tests for a pull request whose base
  is an intent branch, and fix any `main` assumption found.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Re-read `flow/github/ci.py`, `flow/github/pr.py`, `flow/review/loop.py`, `flow/review/ensemble.py` and
   `flow/stages/review.py` and grep them for `main`; confirm the planning audit (only parameter defaults, always
   overridden by `context.intent_data["target_branch"]`). Record the conclusion in the execution report.
2. Create `apps/sandbox-executor/tests/test_review_stacked_pr.py` (new focused module; `test_review_loop.py` is already
   2,880 lines). Reuse the existing hermetic fixtures (`tests/review_fakes.py` `FakeEnsemble`, the loop context helper
   pattern from `test_review_loop.py`, and `tests/gh_write_guard.py` conventions) rather than duplicating them; if the
   private helpers in `test_review_loop.py` are not importable cleanly, build a minimal context in the new module.
3. Use a parent intent branch name with the real shape, for example `I-1700000000-parent/_`, and a child execution
   branch below the child intent branch.
4. Test A (gh path, end to end through the loop): set the context's `intent_data["target_branch"]` to the parent intent
   branch with no Stage 3 test result. Replace `subprocess.run` inside `flow.github.ci` and `flow.github.pr` with a fake
   `gh` that: answers `gh pr view` first with "no PR" (so `gh pr create` runs), then with `MERGEABLE`/`CLEAN`; answers
   `gh pr checks` first with a failure exit (not yet registered), then with one pending check, then with all checks
   passing. Patch `shutil.which` to report `gh` present, inject a fake clock and sleep so the registration and pending
   waits are exercised without real time. Assert: `gh pr create` was called with `--base` equal to the parent intent
   branch; no recorded `gh` argument equals `main`; the registration wait polled more than once; the loop's `ci_status`
   has `source` `github_cli` and `all_passed` true; the result is approved with the fake ensemble and is not
   `test results not measured`; and the Stage 4 ledger rows written in the temporary `holon-knowledge/ledger` carry the
   CI results.
5. Test B (local git fallback, real git in `tmp_path`): build a throwaway repository whose only branches are the parent
   intent branch and a child branch stacked on it (no `main` exists at all), with `gh` reported absent. Call
   `check_pr_mergeability(repo, child, target_branch=parent)` and assert `MERGEABLE`/`CLEAN`, not `MISSING_REF`; add a
   conflicting commit on the parent and assert `CONFLICTING`/`DIRTY`. This is the empirical proof that the fallback
   never needs `main`.
6. Test C (narrow unit): `probe_ci_checks` with a fake `gh pr checks` output for a stacked head returns the same summary
   shape and counts as for a pull request into `main` (parametrise the base name only in the fake PR metadata to show
   the result does not depend on it).
7. Contingency: only if items 1 to 6 expose a real `main` assumption in `flow/github/` or `flow/review/`, fix it in the
   owning module so the base comes from the intent's `target_branch`, keep the change budget-neutral, lower the module's
   budget in `apps/sandbox-executor/flow_module_budgets.json` if it shrank, and add no `noqa`. No change to
   `entrypoint/` or `stage_conflicts.py` (outside scope).
8. Run
   `uv run pytest apps/sandbox-executor/tests/test_review_stacked_pr.py apps/sandbox-executor/tests/test_review_loop.py`.

### Dependencies & Criticality

- **Depends on:** NONE (independent of Steps 1 and 2; ordered after them for a tidy history)
- **Is Bottleneck:** NO (a failing test is diagnostic, not plan-breaking; p_success remains above 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** hermetic testing rules in `apps/sandbox-executor/docs/hermetic_testing.md` and the gh write guard
  (no real `gh` or network); `holon-config/world/constraints.md` section 3 (ledger writes only to a temporary directory,
  append-only); intent constraint "no behaviour change outside CI triggers and check resolution".
- **Potential failure modes for this step:** a real `gh` being invoked if a patch target is wrong (the gh write guard
  should catch it); brittle coupling to private helpers of `test_review_loop.py`; fake clock not threaded into a wait
  and the test taking 180 s.
- **Guardrails and early‑abort checks:** set `HOLON_CI_REGISTRATION_TIMEOUT` explicitly via the loop's
  `ci_registration_timeout` argument and a fake clock; assert the test runs well under a second; abort the contingency
  fix if it would exceed a line budget.

### Success & Discard Criteria

- **Success:** three tests (A, B, C) pass; the loop approves a stacked pull request with green CI; no `gh` argument is
  `main`; the local fallback works in a repository without `main`.
- **Discard:** if the end-to-end Test A cannot be made hermetic within about 1.5 times its cost estimate, keep Tests B
  and C plus a loop-level test that stubs `probe_ci_checks` and `check_pr_mergeability` and asserts they receive the
  parent branch, and note the gap.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 2.2   |
| impact_pred         | 70.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 54.74 |

### Step Metrics Rationale

`EV = 0.82 * 70.0 + 0.5 * 6.0 - 0.3 * 2.2 - 5.0 = 57.40 + 3.00 - 0.66 - 5.00 = 54.74`. Highest entropy and learning
value: fake `gh` sequencing across two modules plus the review loop's many collaborators is where execution effort
concentrates. The real-git repository without `main` is the exploratory element that can surface a hidden assumption.

---

## Step 4: Document that pull requests into any branch run the CI checks

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small prose edits in two files; only risk is Prettier formatting, which Step 5 checks.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the CI docs state that pull requests into any base branch run the checks, and why.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `.github/workflows/README.md`, change each "Runs on pushes to `main` and on pull requests" to "on pull requests
   into any base branch" for the six workflows, and give `docker-build.yml` the same trigger statement.
2. Add a short "Pull requests into any branch" subsection: decomposed plans run child intents whose pull requests are
   stacked on the parent intent branch; with a `main`-only filter they received no checks and Stage 4 halted with
   `test results not measured`; `push` stays on `main`, so no commit is checked twice; the timing gate on a stacked pull
   request still compares against `main` baselines; `tests/test_workflow_triggers.py` guards the rule.
3. In `docs/git_flow.md`, next to the existing Stage 4 CI registration paragraph (around lines 320 to 330), add one or
   two sentences that stacked sub-intent pull requests receive the same checks because every CI workflow runs for pull
   requests into any branch.
4. Grep `docs/` for other statements that CI runs only on pull requests into `main` and correct any found.
5. Australian English throughout (for example "behaviour", "artefact", "normalise"); run `npx prettier --write` only on
   the edited Markdown files if `lint-docs` reports them.

### Dependencies & Criticality

- **Depends on:** Steps 1 and 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent constraint "Australian English"; `uv run task lint-docs`.
- **Potential failure modes for this step:** Prettier reflow failures; inaccurate claims about double runs.
- **Guardrails and early‑abort checks:** describe only what Step 1 changed; run `lint-docs` in Step 5.

### Success & Discard Criteria

- **Success:** both docs state the any-branch rule and the reason; `uv run task lint-docs` passes.
- **Discard:** none expected; on Prettier failure reformat only the touched files.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 0.6   |
| impact_pred         | 35.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 30.87 |

### Step Metrics Rationale

`EV = 0.93 * 35.0 + 0.5 * 1.0 - 0.3 * 0.6 - 2.0 = 32.55 + 0.50 - 0.18 - 2.00 = 30.87`. Low entropy prose change; impact
is moderate because the docs guide maintainers configuring branch protection and reading CI behaviour.

---

## Step 5: Verify with affected tests, the full suite once and both lint tasks

- **Sub‑intent recommendation:** NO
- **Reasoning:** Verification only; must run on the same branch as the changes.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove acceptance criterion 5 and that nothing else regressed.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Affected tests first:
   `uv run pytest apps/sandbox-executor/tests/test_workflow_triggers.py apps/sandbox-executor/tests/test_review_stacked_pr.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_ci_timing_gate.py`.
2. Full suite once: `uv run task test` (which is `pytest -n auto --dist worksteal` on the unit selection). No repeated
   runs: the intent states no timing or performance target.
3. `uv run task lint` (lockfile unchanged, ruff check and format, flow module budgets) and `uv run task lint-docs`.
4. Confirm `git diff --stat` matches the touch set; `uv.lock` and `pyproject.toml` unchanged; no ledger lines edited
   (only appends by the flow itself).
5. Report pass/fail counts and pytest-reported times in the execution report.

### Dependencies & Criticality

- **Depends on:** Steps 1 to 4
- **Is Bottleneck:** YES (acceptance requires green runs; p_success is above 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** intent constraint "tests strictly via `uv run pytest`"; `holon-config/world/constraints.md`
  section 3 (exactly three append-only ledgers, unchanged).
- **Potential failure modes for this step:** unrelated pre-existing flaky test; budget check failing if a module was
  touched.
- **Guardrails and early‑abort checks:** if an unrelated test fails, re-run only that test once to classify it and
  record the evidence rather than editing unrelated code.

### Success & Discard Criteria

- **Success:** all selected tests pass, both lint tasks pass, touch set as declared.
- **Discard:** a failure that would require changing modules outside the intent scope; stop and report.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 0.8   |
| impact_pred         | 50.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 41.76 |

### Step Metrics Rationale

`EV = 0.88 * 50.0 + 0.5 * 2.0 - 0.3 * 0.8 - 3.0 = 44.00 + 1.00 - 0.24 - 3.00 = 41.76`. The full suite is long and
occasionally noisy, so p_success is below the edit steps.

---

### Strategy Rationale

The intent is accepted as is: the change is the minimum that unblocks every decomposed flow, it fits easily within the
15.0 entropy budget, and nothing in the world ruleset argues against it. Splitting into sub-intents would be
counter-productive here, since the sub-intents' own pull requests would target this intent branch and, until Step 1
lands, would themselves hit the very failure this intent fixes.

Aggregation:

- **Entropy** is the sum of step entropies: `1.0 + 1.2 + 2.2 + 0.6 + 0.8 = 5.8`.
- **Cost** is the sum of step costs: `2.0 + 3.0 + 5.0 + 2.0 + 3.0 = 15.0`.
- **Impact** is the impact of the delivered outcome (decomposed flows can complete), taken from the bottleneck Step 1:
  `85.0`.
- **Learning value** is qualitative: mostly from Step 3's empirical audit, `5.0`.
- **p_success** is a qualitative judgement rather than a raw product. The raw product
  `0.97 * 0.92 * 0.82 * 0.93 * 0.88 = 0.60` treats every step failure as fatal, but Steps 2 to 4 are recoverable within
  the same branch; the realistic failure modes are Step 3's fake-`gh` sequencing and full-suite noise in Step 5, so
  `0.80` is used.
- **EV**: `EV = 0.80 * 85.0 + 0.5 * 5.0 - 0.3 * 5.8 - 15.0 = 68.00 + 2.50 - 1.74 - 15.00 = 53.76`.

Bottleneck: Step 1 for impact (without it nothing changes on GitHub), Step 3 for execution risk. Strong assumption: the
planning audit that Stage 4 resolution is base-agnostic is correct; Step 3 tests it, and the contingency path keeps a
refutation inside scope and budgets. Live GitHub behaviour (that stacked pull requests actually receive checks) cannot
be observed in the sandbox; it follows from GitHub's documented trigger semantics and will be observed on the first
decomposed flow after merge.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` section 1: only humans merge to `main`; commits stay on the execution branch.
  - `holon-config/world/constraints.md` section 3: the three ledgers stay append-only; tests write ledgers only under
    `tmp_path`.
  - `holon-config/world/ruleset.md` section 3: test additions are declared in Steps 2 and 3.
  - `docs/safety.md` Human review is the final gate and Invariant 4: CI evidence for human merge decisions is
    strengthened, never weakened.
  - Intent constraints: no new dependencies, Australian English, flow module line budgets, target-native paths.
- **Potential violations or edge cases:**
  - Removing the wrong filter (the `push` one) would cause double runs on intent branches.
  - A vacuous guard test giving false confidence.
  - A hermetic test accidentally invoking the real `gh` CLI.
  - Any source fix pushing `loop.py` (960/962), `pr.py` (515/515) or `ci.py` (228/228) over budget.
  - Running CI on every stacked pull request increases runner minutes; this is the intended trade-off.
- **Mitigations built into the plan:**
  - Diff-shape check in Step 1 and the push-on-`main` assertion in Step 2.
  - Fail-first check and reader self-tests in Step 2.
  - Patched `subprocess.run`, `shutil.which`, clock and sleep in Step 3, plus the existing gh write guard.
  - Source changes in Step 3 only on evidence, budget-neutral, with budget lowered if a module shrinks.
  - Out-of-scope `main` literals in `entrypoint/` are recorded as a follow-up, not changed.
- **Residual risk accepted (and why):** Real GitHub trigger behaviour is verified only after merge, by the next
  decomposed flow. This is accepted because the change is a two-line deletion per file with well-documented semantics,
  and the human maintainer reviews the pull request before merge.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 5.8
- **Budget Compliance:** The strategy fits within budget (5.8 of 15.0), so it runs as one plan with no sub-intent list.
