# Plan for I-1793275605-ledger-writes-are-pure-appends-never-reorder-intents

- **Plan ID:** P-1793275622-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-29T12:07:02.845Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A. The intent is one well-scoped defect, and the operator answered that it must
  not be split.
- **Exploration stance:** balanced. Planning analysis has already found the rewriting code path, so most steps are
  exploit steps. One short reproduction step stays to confirm exactly which rows move, before the fix removes the
  mechanism.
- **Safety priority level:** elevated
- **Priority Justification:** `holon-config/world/constraints.md` §3 (Ledger Immutability, Zero Modification Policy) is
  broken by the defect itself, and the fix touches the code that writes the provenance ledgers that every later stage
  depends on. A regression would corrupt history or leave operator checkouts dirty.

## Exploration

- **Proportion of steps that are exploratory:** 0.14 (1 of 7 steps)
- **Justification:** The writer was found during planning, so little discovery is left. Step 1 is a cheap, bounded
  reproduction. It turns the analysis into a failing test, which lowers the risk of fixing the wrong path.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 6.1   |
| impact_pred         | 82.0  |
| cost_pred           | 14.0  |
| learning_value_pred | 5.0   |
| ev_pred             | 53.9  |

## Planning Findings (evidence gathered read-only)

The executor must re-verify these findings, but they set the direction of the plan.

1. **The rewriting path:** `apps/sandbox-executor/src/sandbox_executor/flow.py`, `check_and_resolve_stage_conflicts`
   (about lines 792-938). After the porcelain and merge-tree probes it reads every ledger in the working tree. If the
   raw content contains any of the substrings `<<<<<<<`, `|||||||`, `=======` or `>>>>>>>` anywhere, it marks the ledger
   as conflicting. It then calls `reconcile_jsonl_ledger_conflict`, which parses the rows, deduplicates them by primary
   key (`branch` or `slug`, `plan_id`, `action_id` or `execution_id`), keeps the higher `ledger_revision` or the later
   `created_at`, re-sorts the whole list by `created_at` and re-serialises every row with `json.dumps`. It writes the
   result over the file with `open(..., "w")` and then runs `git add` on the ledger.
2. **The trigger is present on main today:** row 38 of `holon-knowledge/ledger/intents.jsonl` (slug
   `review-loop-agent-conflict-guard-and-pre-commit`) has a description that mentions the diff3 marker `|||||||`. The
   substring scan therefore treats every checkout of `intents.jsonl` as "conflicting", on every run, with no real
   conflict present. Running the reconcile function on main's current file alone is idempotent: the output is the same
   209,499 bytes. So the reorder shows up once a run has added rows that dedupe or sort differently, for example
   duplicate `branch` keys or new rows with earlier `created_at` values. Step 1 must reproduce the exact movement.
3. **Callers that trigger it:** `flow.py` Stage 1 (`check_and_resolve_stage_conflicts(..., branch=None)`, about line
   2360, only when not a dry run), the Stage 2 and Stage 3 calls (about lines 2510 and 3010), and the
   `entrypoint/intent_creator.py:103`, `entrypoint/planner.py:300`, `entrypoint/executor.py:1600` and
   `entrypoint/review_loop.py:333` entrypoints. Because the function runs `git add` in the operator's checkout, a failed
   or dry run leaves `intents.jsonl` staged with no new row. That matches the Stage 4 checkout error in the intent.
4. **Other non-append writers:** `reconcile_ledger_file`, which uses a temp file plus `os.replace` and is reached
   through `reconcile_ledgers`, and its callers in `entrypoint/executor.py` `sync_and_reconcile_pre_push` (about lines
   1071-1117). That code reconciles all three ledgers after every merge, including a clean one, and stages them.
   `reconcile_ledger_rows` is the sort and dedupe core.
5. **Pure-append writers (already correct):** `ledger.LedgerLock`, `atomic_ledger_append`, `try_append_ledger_line`,
   `try_append_action_record`, the summary appender, `entrypoint/intent_creator.py:114`, `entrypoint/planner.py:543`,
   `knowledge/records.py:162`, `flow.py:2167` and `calibration.py` ACT-CAL. All of them open the file in `"a"` mode.

---

## Step 1: Reproduce the reorder and inventory every ledger writer

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is cheap, read-mostly confirmation work. It produces the failing regression test that the later
  steps must turn green.
- **Step Type:** EXPLORATION
- **Exploration level:** BALANCED
- **Hypothesis being tested:** `check_and_resolve_stage_conflicts` is the only per-run path that rewrites
  `intents.jsonl`. A false-positive marker match on row 38 (`|||||||` inside a JSON string) drives the dedupe, sort and
  re-serialise step and the following `git add`.
- **Learning target:** The exact row movement (dedupe of repeated `branch`/`slug` keys versus the `created_at` sort
  versus `json.dumps` byte drift), and whether any other writer such as `sync_and_reconcile_pre_push` also fires on a
  normal flow run.
- **Maximum acceptable cost for this learning:** about 2 cost units. Stop exploring once one test reproduces a
  non-prefix rewrite.

### Intent & Git Integration

- **Step Intent:** Turn the planning findings into an exact inventory and a failing pytest reproduction.
- **Git branch:**
  I-1793275605-ledger-writes-are-pure-appends-never-reorder-intents/P-1793275622-claude-agent-claude-opus-5-5/E-{ts}-execute/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Grep `apps/sandbox-executor/src/sandbox_executor` for every write to `holon-knowledge/ledger` paths: `open(` with
   `"w"`, `os.replace`, `write_text`, `reconcile_`, `git add` on ledger paths, `sort(`, `sorted(`. Record each writer as
   append-only or rewriting in the execution notes.
2. Add a pytest regression test in `apps/sandbox-executor/tests/test_flow.py`, or in a new
   `tests/test_ledger_append_only.py`, using `tmp_path`. Build a temporary `intents.jsonl` whose rows have out-of-order
   `created_at` values, a repeated `branch` key and one description containing `|||||||`. Call
   `check_and_resolve_stage_conflicts(repo_dir, branch=None)` with `run_git` monkeypatched to a fake that returns an
   empty porcelain status. Assert that the bytes are unchanged and that no `git add` call was recorded. This test must
   fail before the fix.
3. Confirm that `sync_and_reconcile_pre_push` reconciles and stages all three ledgers even after a clean merge. Note
   whether the flow's Stage 3 path reaches it.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO (the findings already locate the path, so a failed reproduction only lowers confidence)

### Safety & Constraint Considerations

- **Relevant rules:** constraints.md §3; ruleset.md §3 (pytest only); intent constraint "tests must not call real
  Docker, gh, git remotes or LLM runners".
- **Potential failure modes for this step:** the test touches the real repository ledger; the fake `run_git` misses a
  call and real git runs.
- **Guardrails and early‑abort checks:** use only `tmp_path` copies and never open the repository's
  `holon-knowledge/ledger` files for writing. Monkeypatch `run_git` and `check_pr_mergeability` at their `flow` module
  bindings.

### Success & Discard Criteria

- **Success:** A pytest reproduction fails on the unmodified code, showing a non-prefix rewrite or a `git add`, and the
  writer inventory is complete.
- **Discard:** If no reproduction appears within about 2 cost units, continue with the fix anyway: the substring-scan
  defect is visible in the code. Record the open question in the execution notes.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.6   |
| impact_pred         | 10.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 8.82  |

### Step Metrics Rationale

`EV = 0.90 * 10.0 + 0.5 * 4.0 - 0.3 * 0.6 - 2.0 = 9.0 + 2.0 - 0.18 - 2.0 = 8.82`. Planning analysis has already found
the path, so the probability of success is high. The learning value comes from separating the dedupe, sort and
serialisation effects.

---

## Step 2: Add the prefix guard helper in ledger.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** A small, reusable pure function with low risk. It is the foundation for Steps 3 and 4.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Give every ledger write a fail-closed check that the pre-write bytes are a prefix of the post-write
  bytes.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/ledger.py`, add an exception class `LedgerRewriteError` (a subclass of
   `RuntimeError`) with an Australian English docstring.
2. Add `assert_ledger_prefix(path: str, before: bytes) -> None`. It reads the current bytes and raises
   `LedgerRewriteError`, naming the path and the first divergent byte offset, when the current bytes do not start with
   `before`. The function stays short with one branch. A small `_first_divergence(a, b)` helper is optional.
3. Inside `atomic_ledger_append`, capture the file bytes under the lock before yielding, using a missing file as `b""`.
   After the caller's block, flush and then call `assert_ledger_prefix`. Keep `LedgerLock` itself unchanged so existing
   lock tests stay valid. If the flush-ordering logic gets awkward, put the check in a thin `_guarded_append` wrapper
   used by `atomic_ledger_append`.
4. Leave key order and serialisation of appended rows unchanged. No new dependencies; standard library only.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md §2 (typing, docstrings); intent constraint "keep functions short and low in branching";
  Australian English.
- **Potential failure modes for this step:** reading a large ledger twice per append adds latency (the ledgers are about
  200 KB, so this is acceptable); the check runs before the buffered write reaches disk and passes falsely.
- **Guardrails and early‑abort checks:** flush before the post-read, and add a unit test that rewrites the file inside
  the context and expects `LedgerRewriteError`.

### Success & Discard Criteria

- **Success:** `test_ledger_lock.py` and the new guard tests pass, and a deliberate in-context truncation raises
  `LedgerRewriteError`.
- **Discard:** If wrapping `atomic_ledger_append` breaks more than 3 existing tests in ways that cannot be fixed without
  changing their semantics, fall back to an explicit `assert_ledger_prefix` call at each writer.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.8   |
| impact_pred         | 15.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 11.81 |

### Step Metrics Rationale

`EV = 0.92 * 15.0 + 0.5 * 0.5 - 0.3 * 0.8 - 2.0 = 13.8 + 0.25 - 0.24 - 2.0 = 11.81`. This is a self-contained helper
with well-understood semantics.

---

## Step 3: Remove the rewriting paths and stop staging ledgers

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is the core fix and the bottleneck, but it stays within two modules and the operator ruled out
  splitting. Its tests come in Step 4 on the same branch.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make sure no function rewrites, sorts, deduplicates or re-serialises existing ledger rows, and that
  no stage pre-check runs `git add` on a ledger.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `flow.py` `check_and_resolve_stage_conflicts`, replace the substring marker scan with a line-anchored check. A
   ledger counts as conflicted only when the path is reported as unmerged by `git status --porcelain`, or when a line
   **starts with** a marker after `rstrip` (real conflict markers always start a line; JSON rows start with `{`). Put
   this in a small `_has_conflict_marker_lines(content)` helper.
2. Change the ledger-resolution branch so it never rewrites the file. For a genuinely unmerged append-only ledger, use a
   pure append union. Keep "ours" (the bytes of the `HEAD`/base side, read through `git show :2:<path>`, or the lines
   above the first marker as the fallback) unchanged as the prefix. Then append only the "theirs" lines whose exact text
   is not already present, in their original order. Write the result through one helper, for example
   `_append_only_union(ours_text, theirs_lines)`, and check it with `assert_ledger_prefix`. If the ours side cannot be
   isolated, raise `ConflictResolutionError` rather than rewriting.
3. Remove the `run_git(["add", ...])` call from the stage pre-check. Pre-checks only report; the commit step that owns
   the ledger append stages it. This prevents a failed or dry run from leaving `intents.jsonl` staged.
4. Retire the sort and dedupe behaviour: rework `reconcile_ledger_rows`, `reconcile_jsonl_ledger_conflict`,
   `reconcile_ledger_content` and `reconcile_ledger_file` so they keep the original line order and the original bytes of
   every line, and only append missing lines. Because `reconcile_jsonl_ledger_conflict` already uses the append-only
   union from item 2, `reconcile_ledger_file` becomes a no-op on files without marker lines. Keep the public names so
   that `entrypoint/executor.py` and existing tests still import them, and update their docstrings. Where a test relied
   on the old dedupe or sort semantics, update it in Step 4 and say why.
5. In `entrypoint/executor.py` `sync_and_reconcile_pre_push`, stop calling `reconcile_ledgers` after a clean merge,
   because git's merge result is already correct. On a ledger conflict, rely on the reworked append-only
   `reconcile_ledger_file`.
6. Keep every new or changed function under about 25 lines with at most two branches, and use Australian English in the
   log strings ("serialise", "behaviour").

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** constraints.md §3 (append-only); intent Q&A ("existing ledgers are never rewritten"; no repair of
  history); touch set limited to the ledger-writing modules.
- **Potential failure modes for this step:** a real append conflict between two branches now raises an error where it
  used to "resolve"; the union duplicates a row that differs only in whitespace; existing tests that assert dedupe
  behaviour fail.
- **Guardrails and early‑abort checks:** compare lines exactly as bytes and do not parse JSON for identity. Run
  `uv run pytest apps/sandbox-executor/tests/test_flow.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_planner.py apps/sandbox-executor/tests/test_calibration.py`
  straight after the change.

### Success & Discard Criteria

- **Success:** The Step 1 reproduction passes. No code path under `sandbox_executor` opens a
  `holon-knowledge/ledger/*.jsonl` file in `"w"` mode or calls `os.replace` on it, except the append-only union guarded
  by `assert_ledger_prefix`.
- **Discard:** If more than 10 existing tests encode the dedupe or sort contract and the rework would exceed 1.5 times
  the cost estimate, stop. Keep only items 1 and 3, the minimal fix (the line-anchored marker check and no staging), and
  record the remaining rework as a follow-up.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 2.5   |
| impact_pred         | 40.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 27.30 |

### Step Metrics Rationale

`EV = 0.82 * 40.0 + 0.5 * 0.5 - 0.3 * 2.5 - 5.0 = 32.8 + 0.25 - 0.75 - 5.0 = 27.30`. This step carries most of the
impact and most of the entropy because it changes conflict semantics that other tests encode.

---

## Step 4: Add the pytest suite for the pure-append guarantee

- **Sub‑intent recommendation:** NO
- **Reasoning:** The tests are tightly coupled to Steps 2 and 3. They give the acceptance evidence the intent asks for.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the guarantee for a successful intent stage, a failed Stage 1 and a dry run, across all three
  ledgers, with no real Docker, gh, git remotes or LLM runners.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `apps/sandbox-executor/tests/test_ledger_append_only.py`. Use a fixture that seeds a `tmp_path` repository (a
   local `git init` with no remote is fine, or fake `run_git`) with three ledgers. The `intents.jsonl` rows have
   out-of-order `created_at` values, a repeated `branch` key and a `|||||||` substring. Snapshot the bytes of each
   ledger.
2. Write a test for the successful intent stage. Drive the flow's Stage 1, or `entrypoint/intent_creator` row writing,
   using the existing hermetic fakes in `tests/hermetic_fixtures.py` and `conftest.py`. Assert that each snapshot is an
   exact prefix of the new bytes and that `intents.jsonl` grew by exactly one line.
3. Write a test for a failed Stage 1, where the fake agent runner exits non-zero. Assert that all three ledgers are
   byte-identical, or that only a failure record was appended, and that the staged set from
   `git diff --cached --name-only` on the local temporary repository contains no ledger. With a fake `run_git`, assert
   instead that no `add` call names a ledger path.
4. Write a dry-run test with `context.dry_run = True` that makes the same assertions.
5. Write a regression test for `reconcile_jsonl_ledger_conflict` and `reconcile_ledger_file`. Input without marker lines
   gives byte-identical output. Input with real line-anchored markers gives the ours bytes followed by the missing
   theirs lines in their original order, and out-of-order timestamps are never sorted.
6. Write guard tests: `assert_ledger_prefix` raises `LedgerRewriteError` for a truncated or reordered file and passes
   for a pure append. Writing through `atomic_ledger_append` after an in-context rewrite raises.
7. Update any existing assertions in `test_flow.py`, `test_review_loop.py` or `test_planner.py` that encoded the old
   dedupe or sort behaviour, with a comment explaining the new append-only contract (this is declared here, as
   ruleset.md §3 requires).
8. Use the local `tests/gh_write_guard.py` and hermetic guard so that any accidental gh, Docker or remote call fails the
   test.

### Dependencies & Criticality

- **Depends on:** Step 2, Step 3
- **Is Bottleneck:** YES (acceptance depends on it)

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md §3; intent item 4; `apps/sandbox-executor/docs/hermetic_testing.md`.
- **Potential failure modes for this step:** driving Stage 1 end to end needs a lot of mocking; a test leaks to a real
  `git fetch`.
- **Guardrails and early‑abort checks:** if driving Stage 1 needs more than about 60 lines of fakes, test at the
  `check_and_resolve_stage_conflicts` plus intent-row-writer seam instead, and say so in the test docstring.

### Success & Discard Criteria

- **Success:**
  `uv run pytest apps/sandbox-executor/tests/test_ledger_append_only.py apps/sandbox-executor/tests/test_ledger_lock.py apps/sandbox-executor/tests/test_flow.py`
  all pass, and the new tests fail when Step 3 is reverted locally.
- **Discard:** If hermetic Stage 1 driving cannot be achieved within 1.5 times the cost estimate, use the seam-level
  tests described above.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.2   |
| impact_pred         | 20.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 13.89 |

### Step Metrics Rationale

`EV = 0.85 * 20.0 + 0.5 * 0.5 - 0.3 * 1.2 - 3.0 = 17.0 + 0.25 - 0.36 - 3.0 = 13.89`. Hermetic fixtures already exist, so
the main risk is how much mocking a full Stage 1 run needs.

---

## Step 5: Document the pure-append guarantee in docs/ledger_schema.md

- **Sub‑intent recommendation:** NO
- **Reasoning:** A single sentence in one permitted file.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** State the guarantee where the ledgers are documented.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `docs/ledger_schema.md`, next to the "strictly three append-only ledgers" or `LedgerLock` paragraph (about lines
   63-96), add one or two sentences. They should say that every write is a pure append under `LedgerLock`, that the
   pre-write bytes are always a byte prefix of the post-write bytes (checked by `assert_ledger_prefix`, which fails
   closed with `LedgerRewriteError`), that conflict resolution only appends missing lines and never sorts, deduplicates
   or re-serialises rows, and that failed or dry runs never stage a ledger.
2. Do not touch `docs/git_flow.md`, `docs/examples.md` or `README.md`. Run `uv run task lint-docs`.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent touch set; Australian English.
- **Potential failure modes for this step:** prettier reflow changes unrelated lines; editing a file outside the
  permitted set.
- **Guardrails and early‑abort checks:** check that `git diff --stat` shows only the permitted files.

### Success & Discard Criteria

- **Success:** `uv run task lint-docs` passes and the diff to `docs/ledger_schema.md` is limited to the guarantee
  sentence(s).
- **Discard:** N/A (trivial).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.2   |
| impact_pred         | 5.0   |
| cost_pred           | 0.5   |
| learning_value_pred | 0.0   |
| ev_pred             | 4.19  |

### Step Metrics Rationale

`EV = 0.95 * 5.0 + 0.5 * 0.0 - 0.3 * 0.2 - 0.5 = 4.75 - 0.06 - 0.5 = 4.19`.

---

## Step 6: Run lint, docs lint and the full test suite once

- **Sub‑intent recommendation:** NO
- **Reasoning:** A standard verification gate. Repeated runs are not needed because the intent has no timing goal.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Confirm repository-wide quality gates pass.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. From the repository root, run the affected tests first:
   `uv run pytest apps/sandbox-executor/tests/test_ledger_append_only.py apps/sandbox-executor/tests/test_ledger_lock.py apps/sandbox-executor/tests/test_flow.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_planner.py apps/sandbox-executor/tests/test_intent_creator.py apps/sandbox-executor/tests/test_executor.py`.
2. Then run `uv run task lint`, `uv run task lint-docs` and the full suite once with `uv run task test`. Never set
   PYTHONPATH.
3. Report the pass and fail counts and the pytest-reported time.

### Dependencies & Criticality

- **Depends on:** Steps 2-5
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** intent item 6; Test-Run Discipline (one full run).
- **Potential failure modes for this step:** unrelated pre-existing failures; lint complexity rules flag the reworked
  functions.
- **Guardrails and early‑abort checks:** if a failure is pre-existing on main (check with `git stash` or a compare
  against the base), record it and do not fix it outside the touch set.

### Success & Discard Criteria

- **Success:** All three commands exit 0.
- **Discard:** If a failure needs files outside the touch set, stop and report it.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 0.3   |
| impact_pred         | 8.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 0.0   |
| ev_pred             | 5.95  |

### Step Metrics Rationale

`EV = 0.88 * 8.0 - 0.3 * 0.3 - 1.0 = 7.04 - 0.09 - 1.0 = 5.95`.

---

## Step 7: Verify byte-prefix preservation against main's real ledgers (read-only)

- **Sub‑intent recommendation:** NO
- **Reasoning:** A cheap end-to-end sanity check on real data that does not modify the repository's ledgers.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Show that the reworked functions are byte-identity on main's real ledgers. This covers the `|||||||`
  row and the divergence class seen at byte 101,845.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In a pytest test that copies `holon-knowledge/ledger/*.jsonl` into `tmp_path` (read-only use of the originals), run
   `check_and_resolve_stage_conflicts` with fake git, then `reconcile_ledger_file`. Assert byte identity, then append
   one row through `atomic_ledger_append` and assert the prefix property. Skip the test if the files are absent.
2. Confirm that `git status --porcelain holon-knowledge/ledger` is empty after the whole suite.

### Dependencies & Criticality

- **Depends on:** Step 3, Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** constraints.md §3; intent Q&A (no rewrite of historical rows).
- **Potential failure modes for this step:** the test accidentally writes to the real ledger path.
- **Guardrails and early‑abort checks:** copy into `tmp_path` and assert that the real file's hash is unchanged at the
  end of the test.

### Success & Discard Criteria

- **Success:** The test passes and the repository ledgers are untouched.
- **Discard:** If the real-data test is flaky because ledger content changes during a concurrent run, keep only the
  synthetic tests.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.5   |
| impact_pred         | 6.0   |
| cost_pred           | 0.5   |
| learning_value_pred | 0.5   |
| ev_pred             | 5.12  |

### Step Metrics Rationale

`EV = 0.92 * 6.0 + 0.5 * 0.5 - 0.3 * 0.5 - 0.5 = 5.52 + 0.25 - 0.15 - 0.5 = 5.12`.

---

### Strategy Rationale

The intent asks for find, fix, guard, test and document work on one defect, and the operator explicitly ruled out
splitting it. Planning analysis has already found the mechanism: a substring-based conflict-marker scan in
`check_and_resolve_stage_conflicts` gives a false positive on a JSON row that mentions `|||||||`. It then runs a dedupe,
`created_at` sort and re-serialise pass, overwrites the file and runs `git add` on it in the operator's checkout. A
related always-on `reconcile_ledgers` call after clean pre-push merges uses the same machinery. The strategy is
therefore mostly exploit. Step 1 confirms the mechanism with a failing test. Step 2 adds the guard. Step 3 removes the
rewriting semantics (line-anchored marker detection, an append-only union, no staging in pre-checks). Steps 4-7 add the
evidence.

Aggregation:

- **p_success:** this is a serial chain with partly correlated risks. Multiplying all steps naively gives about 0.48,
  which is too pessimistic because Steps 5-7 are near-certain and depend on the same root cause. The overall value 0.82
  is set by the bottleneck, Step 3 (0.82). It assumes that a failure in Step 4 or 6 is usually a fixable follow-on of
  Step 3 inside the same run.
- **entropy:** the sum of the step values: `0.6 + 0.8 + 2.5 + 1.2 + 0.2 + 0.3 + 0.5 = 6.1`, which is within the 9.0
  budget.
- **impact:** 82. This removes the manual repair commit that every flow PR has needed since 2026-10-28, and the Stage 4
  checkout blocker. The value is a qualitative judgement close to the sum of the step impacts (104), discounted for
  overlap.
- **cost:** the sum of the step costs: `2.0 + 2.0 + 5.0 + 3.0 + 0.5 + 1.0 + 0.5 = 14.0`.
- **learning value:** 5.0. This is mostly Step 1, plus the reusable prefix-guard pattern.
- **EV:** `EV = 0.82 * 82.0 + 0.5 * 5.0 - 0.3 * 6.1 - 14.0 = 67.24 + 2.50 - 1.83 - 14.0 = 53.91`.

Strong assumption: existing tests that encode the dedupe and sort contract are few. If there are many, the Step 3
discard path (the minimal fix) still meets goals 1, 2 (no staging, no rewrite in pre-checks), 3 and 4.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §3: append-only ledgers, Zero Modification Policy. This is the defect and the
    target guarantee.
  - `holon-config/world/constraints.md` §1: changes are scoped to the execution branch; no commits to main.
  - `holon-config/world/ruleset.md` §2: typing and docstrings on new public helpers.
  - `holon-config/world/ruleset.md` §3: pytest only; test changes are declared in Step 4.
  - The intent's touch set and constraints: Australian English, no new dependencies, `uv run` from the root without
    PYTHONPATH, no edits to `docs/git_flow.md`, `docs/examples.md` or `README.md`, and exactly three ledgers.
  - `docs/safety.md`: provenance integrity and fail-closed behaviour on safety-relevant checks.
- **Potential violations or edge cases:**
  - Tests or reproduction scripts writing to the repository's real ledgers.
  - A genuine two-branch append conflict now raising an error instead of being silently "resolved".
  - The guard adding I/O on every append.
  - Prettier reflowing unrelated lines in `docs/ledger_schema.md`.
- **Mitigations built into the plan:**
  - Only `tmp_path` copies are used, with a hash check of the real ledgers (Step 7).
  - The append-only union keeps the ours bytes as the prefix, and is checked by `assert_ledger_prefix`.
  - The prefix check fails closed with a clear `LedgerRewriteError`.
  - The diff is checked against the touch set.
  - Historical rows are never repaired, as the intent's Q&A requires.
- **Residual risk accepted (and why):** Real append conflicts may now surface as errors where they were previously
  rewritten silently. That is correct fail-closed behaviour for a provenance ledger. Main's existing out-of-order
  history stays as it is, as the operator ruled.
- **Allocated Entropy Budget:** 9.0
- **Predicted Plan Entropy:** 6.1
- **Budget Compliance:** The strategy fits within budget, so it runs as a single plan with no sub-intent list.

## Plan Description & Strategy

Stop every non-append write to `holon-knowledge/ledger/*.jsonl`:

1. Detect conflicts only from git's unmerged status or line-anchored markers, never from substrings inside JSON rows.
2. Replace the dedupe, sort and re-serialise reconciliation with an ours-prefix append-only union.
3. Never run `git add` on a ledger from a stage pre-check or a failed or dry run.
4. Skip reconciliation after clean merges.
5. Guard every write with a byte-prefix assertion inside `atomic_ledger_append`.

The fix is backed by hermetic pytest coverage for success, failed Stage 1 and dry-run paths across all three ledgers,
and by one guarantee sentence in `docs/ledger_schema.md`.

## Sub-intent list

None
