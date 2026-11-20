# Plan for I-1793339935-flow-tests-never-write-the-checkout-knowledge-index

- **Plan ID:** P-1793339950-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-30T05:59:10.780Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS (with one evidence-driven sharpening of the FIX step, see below)
- **Reframed intent (if applicable):** Not reframed. The goal, touch set and constraints stand. The planner's static
  analysis shows that, on the current `main` snapshot, both named tests already pass `repo_dir=str(tmp_path)`, and
  `run_intent_stage` builds its index path from `context.repo_dir`
  (`apps/sandbox-executor/src/sandbox_executor/flow.py:2221`). The leak that is still left is cwd-relative: the
  `ConsultEngine` it constructs auto-syncs `DEFAULT_LEDGERS`
  (`apps/sandbox-executor/src/sandbox_executor/knowledge/consult.py:17`), which are relative paths to the checkout's
  `holon-knowledge/ledger/*.jsonl`. Every other default that falls back to `DEFAULT_PROJECT_DB_PATH`
  (`knowledge/index.py:16`, `records.py:166`, `wisdom.py`, `promotion.py`, `stats.py`) is also relative to the current
  working directory, which is the repository root under `uv run pytest`. So the FIX step pins **both** `repo_dir` and
  the working directory to `tmp_path`, and the executor first proves empirically which tests really write the checkout
  (Step 1) instead of trusting the intent text or this static reading.
- **Exploration stance:** balanced. Mostly exploit (a well-understood fixture-and-snapshot pattern already used for
  ledgers), plus one cheap exploratory measurement: let the new guard enumerate offenders across the whole suite rather
  than relying on grep alone.
- **Safety priority level:** elevated
- **Priority Justification:** `holon-config/world/constraints.md` §3 (ledger immutability: exactly the three append-only
  ledgers, never rewritten) and §1 (commit scope limited to the active step) apply directly. The suite reads the real
  ledgers, and a careless fix or scratch test could write into the checkout's `holon-knowledge/`. The scratch proof in
  Step 5 deliberately writes into the checkout's `.index/`, so cleaning up after it is a hard requirement.

## Exploration

- **Proportion of steps that are exploratory:** 0.33 (Step 1 reproduction and Step 3 guard-driven sweep are BALANCED;
  the rest are EXPLOIT)
- **Justification:** The intent's premise does not match the current code exactly (the tests already use `tmp_path` for
  `repo_dir`), so a short empirical measurement is worth far more than its cost. It stops the executor fixing the wrong
  thing and gives the sweep a ground-truth list instead of a grep guess.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.78  |
| entropy_pred        | 3.0   |
| impact_pred         | 70.0  |
| cost_pred           | 12.5  |
| learning_value_pred | 5.0   |
| ev_pred             | 43.7  |

## Plan Description & Strategy

The work runs as one plan on one execution branch. It stays in `apps/sandbox-executor/tests/`, plus `plans/`,
`executions/` and the three ledgers. The order is **measure → guard → sweep → fix → prove → verify**. The guard goes in
before the fixes so that it finds the offenders itself (a ground-truth sweep), and so that every fix is checked by the
very mechanism that will protect the suite from now on.

Guard design (the core of the plan):

- A small pure helper, `knowledge_index_snapshot(root)`, returns a mapping from relative path to `(size, mtime_ns)` for
  every file under `<root>/holon-knowledge/.index/`. An absent directory gives an empty mapping. A companion,
  `describe_index_changes(before, after)`, returns the sorted list of created, modified and deleted paths. It lives in
  `apps/sandbox-executor/tests/hermetic_fixtures.py`, the shared module that already owns `REPO_ROOT` and that the
  hermetic-guard file imports.
- A per-test autouse fixture in `apps/sandbox-executor/tests/conftest.py` takes the snapshot of
  `REPO_ROOT/holon-knowledge/.index/` before and after each test. On any difference it calls `pytest.fail` with
  `pytrace=False`, using a message such as "Test wrote the checkout's holon-knowledge/.index/: <relative paths>; route
  the index to tmp_path (repo_dir and cwd)". It **fails closed**: if the snapshot itself raises (for example a
  permission error or an unreadable directory), the test fails with that error and is never skipped. This follows the
  existing `gh_write_guard` fixture pattern in the same file. Because it compares before with after, a developer's
  existing index from real `holon` use is not counted as a failure; only changes made during a test are.
- `apps/sandbox-executor/tests/test_sandbox_hermetic_guard.py` (the existing hermetic-guard test file) is extended in
  two ways. (a) The stress canary takes a snapshot of the checkout's `holon-knowledge/.index/` before and after its
  whole-suite child run and fails, naming the paths, on any change. (b) New fast unit-job tests (not marked `stress`)
  check the helpers against a throwaway root under `tmp_path`: a created file is reported by its relative path, so are a
  modified and a deleted file, an absent directory is empty, and the fixture's failure message names the path. These
  negative controls never touch the real checkout.

Touch-set note: `conftest.py` and `hermetic_fixtures.py` are inside `apps/sandbox-executor/tests/`, the touch set's
outer boundary. A per-test guard cannot live only in a test module, because module-level autouse fixtures apply to that
module alone. The intent's explicit list ("test_flow.py and the hermetic-guard test file") is read as the expected
centre of the change, not as an exclusion of the shared test helpers. If the reviewer reads it strictly, the fallback is
Step 2's alternative: put the helpers in the guard file and drop the conftest fixture, keeping only the canary
assertion. That gives weaker, stress-job-only coverage, and the execution notes must record that trade-off.

---

## Step 1: Reproduce the leak and record a baseline

- **Sub‑intent recommendation:** NO
- **Reasoning:** A few minutes of read-only measurement with no code change. Splitting it out would cost more than the
  step itself.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** On the current snapshot, the two named tests no longer create
  `holon-knowledge/.index/action_index.db` in the checkout (because `repo_dir=tmp_path`), but they do read the
  checkout's real ledgers through the cwd-relative `DEFAULT_LEDGERS`. Other tests that use `repo_dir="."` (for example
  `test_flow.py` `TestReviewMergeabilityGuards::test_review_stage_flags_dirty_merge_state` near line 1226, the review
  stage test near line 3630, and the `_dispatch_role` test near line 3075) are the likely real writers, through the
  review-stage consult at `flow.py:4201`.
- **Learning target:** The exact list of tests that create or modify files under the checkout's
  `holon-knowledge/.index/` today, and whether the two named tests are writers or only readers.
- **Maximum acceptable cost for this learning:** Two targeted test invocations and one full-suite run (about 1.5 cost
  units).

### Intent & Git Integration

- **Step Intent:** Establish ground truth before changing anything.
- **Git branch:**
  `I-1793339935-flow-tests-never-write-the-checkout-knowledge-index/P-1793339950-claude-agent-claude-opus-5-5/E-{timestamp}-hermetic-knowledge-index/_`
  (single execution branch for all steps)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Rebase the execution branch on the plan branch (constraints §1).
2. Record whether `holon-knowledge/.index/` exists in the checkout and list its contents with sizes and mtimes. If it
   exists before any test runs, record it in the execution notes as pre-existing; do not delete a developer's index
   without saying so. In a fresh sandbox it is expected to be absent, as the planner observed.
3. From the repository root, run `uv run pytest` with the two named node ids from `test_flow.py`, then list the
   checkout's `holon-knowledge/.index/` again. Record created or modified files, and remove any file the run created.
4. Run `uv run pytest apps/sandbox-executor/tests/test_flow.py` once and repeat the listing, to see whether the
   `repo_dir="."` tests are the writers. Record the result and clean up.
5. Write the findings (hit list, writer or reader, mechanism) into the execution notes draft.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO (if reproduction is inconclusive, Step 3's guard run still enumerates the offenders)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §1 and §3; `docs/safety.md` "Git is the safety boundary".
- **Potential failure modes for this step:** Deleting a pre-existing developer index by mistake; running tests through a
  forbidden runner or `PYTHONPATH`.
- **Guardrails and early‑abort checks:** Delete only files whose creation this step observed. Run tests only through
  `uv run pytest` from the repository root. Check `git status --short` after each run; it must show no tracked or
  untracked changes beyond the plan or execution artefacts.

### Success & Discard Criteria

- **Success:** A recorded, reproducible list of offending tests, with each one classified as a writer or a reader.
- **Discard:** If `uv` cannot run the suite at all in the sandbox, stop and record an environment failure instead of
  guessing.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.2   |
| impact_pred         | 15.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 5.0   |
| ev_pred             | 15.19 |

### Step Metrics Rationale

`EV = 0.95 * 15.0 + 0.5 * 5.0 - 0.3 * 0.2 - 1.5 = 14.25 + 2.50 - 0.06 - 1.50 = 15.19`. The learning value is high
because the intent's premise and the code disagree, and the result decides what Step 4 changes.

---

## Step 2: Add the fail-closed checkout-index guard

- **Sub‑intent recommendation:** NO
- **Reasoning:** About 60 to 100 lines of test-only code that follow an existing pattern (`gh_write_guard`, the ledger
  fingerprint tests). Its risk is contained and it can be reverted in one commit.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make any test that creates, modifies or deletes a file under the checkout's `holon-knowledge/.index/`
  fail, with a message naming the path.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/hermetic_fixtures.py`, add a module constant for the checkout index directory,
   derived from the existing `REPO_ROOT` (target-native and relative to the repository, never hard-coded). Add
   `knowledge_index_snapshot(root)`, which walks `<root>/holon-knowledge/.index/` and returns a mapping from POSIX
   relative path to `(size, mtime_ns)`. An absent directory gives an empty mapping, and any other `OSError` is
   propagated, never swallowed. Also add `describe_index_changes(before, after)`, which returns sorted strings such as
   "created holon-knowledge/.index/action_index.db". Include SQLite side files (`-wal`, `-shm`), because WAL mode
   creates them.
2. In `apps/sandbox-executor/tests/conftest.py`, add an autouse fixture, `checkout_knowledge_index_guard`, next to
   `gh_write_guard`. It takes the snapshot before `yield` and again after, and calls `pytest.fail(..., pytrace=False)`
   with the offending paths and a remediation hint ("pin repo_dir and the working directory to tmp_path"). The message
   says the change may come from a concurrent xdist worker, so a misattribution can be diagnosed. Its docstring states
   the fail-closed rule. Use Australian English in docstrings and messages.
3. In `apps/sandbox-executor/tests/test_sandbox_hermetic_guard.py`: (a) in
   `test_canary_workspace_survives_entrypoint_tests`, take a snapshot of the checkout index before the child run and
   assert after it that `describe_index_changes` is empty, with a message listing the paths; (b) add fast unit tests
   that build a fake root under `tmp_path` and check that created, modified, deleted and absent cases are reported
   correctly, and that the reported strings contain the relative path `holon-knowledge/.index/<name>`. Update the module
   docstring briefly so it says the guard now covers the knowledge index.
4. Do not touch `src/`, `cli.py` or any documentation, `apps/sandbox-executor/docs/hermetic_testing.md` included.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (goal 3, and Step 5 relies on it)

### Safety & Constraint Considerations

- **Relevant rules:** intent touch set; `docs/safety.md` "Failure containment"; constraints §3.
- **Potential failure modes for this step:** False positives from mtime churn when a test only opens the database
  read-only (SQLite WAL can touch `-shm`); guard overhead; a guard that silently passes when the snapshot errors.
- **Guardrails and early‑abort checks:** Snapshot errors propagate, so the guard fails closed. Keep the walk limited to
  `.index/` (normally absent, so the cost is about one `stat` per test). If false positives come from read-only opens,
  report the changed paths anyway: any open of the checkout index is a hermeticity defect and is in scope.

### Success & Discard Criteria

- **Success:** The new unit tests pass. The fixture reports paths relative to the repository. `uv run task lint` passes
  on the changed files.
- **Discard:** If the per-test fixture adds more than 5% to the wall time of `uv run pytest apps/sandbox-executor/tests`
  compared with the Step 1 run (measured once), switch it to a session-scoped before/after check. That still fails
  closed and names paths, but it no longer names the test.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.0   |
| impact_pred         | 35.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 28.45 |

### Step Metrics Rationale

`EV = 0.85 * 35.0 + 0.5 * 4.0 - 0.3 * 1.0 - 3.0 = 29.75 + 2.00 - 0.30 - 3.00 = 28.45`. This step carries the highest
impact because it prevents regressions permanently. The entropy is the largest of the steps because it adds suite-wide
behaviour through an autouse fixture.

---

## Step 3: Sweep the suite for checkout `holon-knowledge/` writers

- **Sub‑intent recommendation:** NO
- **Reasoning:** It is a measurement plus a grep, and its output feeds Step 4 directly.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The guard-driven run finds every `.index/` writer. A grep finds the remaining risky
  patterns, which the guard cannot see because they write other `holon-knowledge/` paths or only read.
- **Learning target:** The complete hit list for the execution notes (goal 2).
- **Maximum acceptable cost for this learning:** One run of `uv run pytest apps/sandbox-executor/tests` plus grep (about
  2 cost units).

### Intent & Git Integration

- **Step Intent:** Produce the full list of offending tests.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run `uv run pytest apps/sandbox-executor/tests` from the repository root with the new guard in place. Every guard
   failure is a hit. Record the node id and path for each.
2. Grep `apps/sandbox-executor/tests/` for these patterns: `repo_dir="."`, `repo_dir=str(REPO_ROOT)`, `os.getcwd()`,
   `chdir(REPO_ROOT)`, `ConsultEngine(` without `db=` or a `tmp_path` path, `ActionIndexDatabase(` without a `db_path`,
   calls to `append_action_event`, `reconcile_records`, `promote_pending_candidates`, the stats helpers or the wisdom
   helpers without an explicit index or database path, and `parents[3] / "holon-knowledge"`. Classify each as a writer
   (must fix), a read-only fixture (for example the deliberate ledger byte-identity tests in
   `test_ledger_append_only.py` and `test_calibration.py`, which are already guarded and left alone), or a mocked call
   (no effect).
3. Confirmed candidates the planner already found: `test_flow.py` review-stage tests built with `repo_dir="."` (near
   lines 1226 and 3630), which reach the consult at `flow.py:4201`, and `_dispatch_role` with `repo_dir="."` (near line
   3075). The `repo_dir="."` uses in `test_calibration.py` near lines 966 and 980 are assertions on a mocked call and
   are expected to be harmless. `test_prompt_templates.py` near line 95 passes `repo_dir=str(REPO_ROOT)` to the prompt
   builder; check whether that path consults the index.
4. Record every hit, including the harmless ones, in the execution notes with a classification.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 2; touch set.
- **Potential failure modes for this step:** Guard failures under xdist being blamed on the wrong test; incomplete grep.
- **Guardrails and early‑abort checks:** Re-run any ambiguous hit alone (`uv run pytest <node id>`) to confirm which
  test it belongs to. Clean up any `.index/` files the run creates before Step 4.

### Success & Discard Criteria

- **Success:** Every guard failure is accounted for in a classified hit list.
- **Discard:** If more than 15 distinct writers turn up in more than three files, record it and still fix them in Step
  4, because the shared fixture approach scales. Re-plan only if a writer cannot be fixed without production changes.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.3   |
| impact_pred         | 15.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 13.41 |

### Step Metrics Rationale

`EV = 0.90 * 15.0 + 0.5 * 4.0 - 0.3 * 0.3 - 2.0 = 13.50 + 2.00 - 0.09 - 2.00 = 13.41`. The cost is low and the sweep is
required by the intent. The value is mostly in completeness.

---

## Step 4: Pin repo_dir and the working directory to tmp_path in every offender

- **Sub‑intent recommendation:** NO
- **Reasoning:** These are small, local test edits that follow one consistent pattern, and the guard from Step 2 checks
  each of them.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the two named tests and every sweep hit hermetic without changing production code (goals 1 and
  2).
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. For `TestFlowStages::test_stage1_intent_validation_failure` and
   `test_intent_stage_fails_fast_on_unresolved_open_questions`, add the `monkeypatch` fixture and call
   `monkeypatch.chdir(tmp_path)` before `run_intent_stage`, keeping `repo_dir=str(tmp_path)`. That pins the cwd-relative
   `DEFAULT_LEDGERS` and `DEFAULT_PROJECT_DB_PATH` under `tmp_path`. Add one positive assertion that the index the stage
   opened is `tmp_path / "holon-knowledge" / ".index" / "action_index.db"`, which proves where it was sent. Keep the
   existing assertions unchanged.
2. If more than two tests need the same pinning, add one small helper fixture in `test_flow.py` (for example
   `hermetic_repo_dir`) that does `chdir` and returns `str(tmp_path)`. Do not repeat the code in each test.
3. For each `repo_dir="."` writer found in Step 3, replace `"."` with `str(tmp_path)` and add the `chdir` where the code
   path resolves relative defaults. Where a test depends on `"."` meaning a real git repository (for example a test that
   expects a missing-ref message), initialise a minimal git repository in `tmp_path` or keep the expectation the same
   with an empty `tmp_path`. Assertions about behaviour must not change.
4. Fix sweep hits in other files under `apps/sandbox-executor/tests/` the same way, only where they are confirmed
   writers. Record each file touched beyond `test_flow.py` in the execution notes, with the reason (goal 2 requires it).
5. After each file, run `uv run pytest <that file>` and confirm the guard is silent and the checkout's `.index/` is
   absent or unchanged.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** YES (goals 1 and 2)

### Safety & Constraint Considerations

- **Relevant rules:** intent constraint "tests only"; touch set; constraints §3.
- **Potential failure modes for this step:** A `chdir` breaks a test that relies on cwd-relative fixtures; a change of
  `repo_dir` alters which code branch is exercised, so the test passes for the wrong reason.
- **Guardrails and early‑abort checks:** Compare each changed test's assertions before and after the edit; only the
  set-up may change. If a writer can only be fixed by changing `src/`, stop. Record it as a follow-up intent and do not
  change production code.

### Success & Discard Criteria

- **Success:** Every hit from Steps 1 and 3 passes with the guard active. The checkout's `.index/` stays absent or
  unchanged after `uv run pytest apps/sandbox-executor/tests/test_flow.py`.
- **Discard:** Any need to edit `src/` or `cli.py`. Also discard if the cost exceeds `1.5 * 2.5 = 3.75` units, in which
  case fix the named tests only and defer the rest as a recorded follow-up.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 0.8   |
| impact_pred         | 30.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 23.76 |

### Step Metrics Rationale

`EV = 0.85 * 30.0 + 0.5 * 2.0 - 0.3 * 0.8 - 2.5 = 25.50 + 1.00 - 0.24 - 2.50 = 23.76`. The probability is slightly
reduced because a test that relies on `repo_dir="."` may need a minimal git repository in `tmp_path`.

---

## Step 5: Prove the guard bites with a scratch test, then remove it

- **Sub‑intent recommendation:** NO
- **Reasoning:** A short throwaway check that is required by goal 4 and never committed.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Show that the guard fails closed on an original offending pattern, with a message naming the path.
- **Git branch:** same execution branch (the scratch file is never staged)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create an uncommitted scratch module, for example `apps/sandbox-executor/tests/test_zz_scratch_index_leak.py`. It
   rebuilds an original offending pattern: a `FlowContext` with `repo_dir="."` (the checkout) passed to
   `run_intent_stage` with `intent_data={}`, without `chdir`.
2. Run `uv run pytest` on that file from the repository root. It must fail, and the failure text must contain
   `holon-knowledge/.index/action_index.db`. Copy the relevant output lines into the execution notes.
3. Delete the scratch module, and delete the files the scratch run created under the checkout's
   `holon-knowledge/.index/` (only those, as compared with the Step 1 baseline). Remove the directory if it did not
   exist before.
4. Check `git status --short` and confirm that neither the scratch file nor anything else is left behind.

### Dependencies & Criticality

- **Depends on:** Step 2 (Step 4 does not need to be finished)
- **Is Bottleneck:** NO (it proves goal 4, but a failure here points to a fixable defect in Step 2)

### Safety & Constraint Considerations

- **Relevant rules:** constraints §1 (commit boundaries); intent goal 4.
- **Potential failure modes for this step:** The scratch file is committed by accident; leftovers stay in the checkout's
  `.index/`.
- **Guardrails and early‑abort checks:** Never `git add` the scratch file. Stage by explicit path only. Compare the
  checkout's `.index/` with the Step 1 baseline after cleanup.

### Success & Discard Criteria

- **Success:** The guard fails with the path named, and after cleanup the checkout matches the baseline.
- **Discard:** If the guard does not fail, return to Step 2 (at most one fix iteration) before going further.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.3   |
| impact_pred         | 10.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 9.41  |

### Step Metrics Rationale

`EV = 0.90 * 10.0 + 0.5 * 3.0 - 0.3 * 0.3 - 1.0 = 9.00 + 1.50 - 0.09 - 1.00 = 9.41`. It is cheap and it makes the guard
falsifiable rather than vacuous.

---

## Step 6: Full verification, lint, execution notes and ledger row

- **Sub‑intent recommendation:** NO
- **Reasoning:** The standard closing gate.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Show that the suite is green and hermetic, the linters pass, and the artefacts are complete.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Record that the checkout's `holon-knowledge/.index/` is absent (or matches the baseline).
2. Run the affected files first:
   `uv run pytest apps/sandbox-executor/tests/test_flow.py apps/sandbox-executor/tests/test_sandbox_hermetic_guard.py`,
   plus any other file changed in Step 4.
3. Run the full suite once with `uv run task test` (no repetition, because the intent sets no timing target). Record the
   pass and fail counts and the time pytest reports.
4. Confirm again that no file was created under the checkout's `holon-knowledge/.index/`.
5. Run `uv run task lint` and `uv run task lint-docs`. Both must pass.
6. Write `executions/E-{timestamp}-claude-agent-claude-opus-5-5.md`, containing: the hit list from Steps 1 and 3 with
   classifications, the files changed, the scratch-proof output, the test counts, and the touch-set note about
   `conftest.py` and `hermetic_fixtures.py`. Use Australian English, and do not refer to any coordinating harness, its
   directories or its tracker ids.
7. Append exactly one row to `holon-knowledge/ledger/executions.jsonl` and change no existing row. Confirm with a
   `git diff` of that file that it shows additions only. Leave the count of ledgers at three.
8. Commit by explicit paths with the required attribution trailer, then push the execution branch.

### Dependencies & Criticality

- **Depends on:** Steps 4 and 5
- **Is Bottleneck:** YES (merge gate)

### Safety & Constraint Considerations

- **Relevant rules:** constraints §3; the intent's test-runner rule; `docs/safety.md` "Human review is the final gate".
- **Potential failure modes for this step:** Unrelated flaky tests; lint-docs failing on the new execution notes; the
  guard firing on a concurrent writer that has nothing to do with this work.
- **Guardrails and early‑abort checks:** Re-run a failing unrelated test once on its own and report it honestly. Never
  hide failures. Run prettier and lint-docs on the notes before committing.

### Success & Discard Criteria

- **Success:** `uv run task test`, `uv run task lint` and `uv run task lint-docs` are all green. No new file exists
  under the checkout's `holon-knowledge/.index/`. Exactly one ledger line is appended.
- **Discard:** Persistent failures caused by this change that cannot be fixed within the touch set. In that case report
  the failure with its output and do not merge.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 0.4   |
| impact_pred         | 15.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 11.13 |

### Step Metrics Rationale

`EV = 0.85 * 15.0 + 0.5 * 2.0 - 0.3 * 0.4 - 2.5 = 12.75 + 1.00 - 0.12 - 2.50 = 11.13`. The cost is mostly the full-suite
run.

---

### Strategy Rationale

The intent is accepted as one plan, as its own Q&A requires ("One intent. Do not split"). The strategy puts the guard
before the fixes so that the sweep in goal 2 rests on observed writes rather than on grep alone. The planner's static
reading found a mismatch between the intent's premise and the code (both named tests already use `tmp_path` for
`repo_dir`; the remaining leak is cwd-relative). Step 1 settles that empirically for little cost, and the fix pins both
`repo_dir` and the working directory, which is right in either case.

Aggregation: entropy is the sum of the step values, `0.2 + 1.0 + 0.3 + 0.8 + 0.3 + 0.4 = 3.0`. Cost is the sum,
`1.5 + 3.0 + 2.0 + 2.5 + 1.0 + 2.5 = 12.5`. The overall probability of success is a qualitative judgement weighted on
the bottleneck steps, Step 2 (0.85), Step 4 (0.85) and Step 6 (0.85). Their correlated product, adjusted upwards because
a failure in Step 5 sends work back to Step 2 instead of ending the plan, gives 0.78. Impact (70) is above the sum of
the step values on their own, because the lasting value is the guard that stops future regressions. Learning value (5.0)
is the largest step value, from Step 1. Overall:
`EV = 0.78 * 70.0 + 0.5 * 5.0 - 0.3 * 3.0 - 12.5 = 54.60 + 2.50 - 0.90 - 12.50 = 43.70`.

Strong assumptions: `uv` and the repository's task definitions are available in the execution sandbox (they were not
available in the planning sandbox, so the planner could not run tests); and no developer process writes the checkout's
`.index/` during the test run.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:** `holon-config/world/constraints.md` §1 (branch prefix and
  commit scope, rebase before execution), §2 (process sandbox, since entropy is below 10), §3 (three append-only
  ledgers, no rewrites); `docs/safety.md` (git as the safety boundary, entropy budgets, human review as the final gate);
  and the intent's constraints (tests only, `uv run` runners only, Australian English, no new dependencies, no harness
  references, touch set).
- **Potential violations or edge cases:**
  - The scratch proof in Step 5 deliberately writes into the checkout's git-ignored `.index/`.
  - `conftest.py` and `hermetic_fixtures.py` are outside the intent's explicit file list but inside its directory
    boundary.
  - A developer's existing local index could be deleted by mistake during cleanup.
  - Under xdist, the guard could blame a test for a write made by another worker.
- **Mitigations built into the plan:**
  - Cleanup removes only the paths observed as created, compared with a recorded baseline, and the result is checked
    with `git status`.
  - The use of `conftest.py` is justified, with a stated fallback if the reviewer reads the touch set strictly.
  - The guard's message names the path and mentions concurrent workers.
  - The guard fails closed on snapshot errors.
  - No change is made under `src/`, to `cli.py` or to any documentation.
  - The ledger gets one appended row, confirmed by `git diff`.
- **Residual risk accepted (and why):** A real `holon` process writing the checkout's index during a developer's local
  test run would trip the guard as a false positive. This is accepted: the failure is loud and easy to explain, and
  failing closed is what the intent asks for.
- **Allocated Entropy Budget:** 5.0
- **Predicted Plan Entropy:** 3.0
- **Budget Compliance:** The strategy fits within the budget (`3.0 <= 5.0`), so there is no sub-intent list and it runs
  as one plan.
