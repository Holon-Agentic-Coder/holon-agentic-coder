# Plan for I-1793246184-execute-failure-keeps-partial-code-and-resumes-from-it

- **Plan ID:** P-1793246201-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-29T03:56:42.434Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** Not reframed. One interpretation is made explicit (Step 3): when the agent has
  already committed its own work and the tree is clean, nothing new is salvaged, but `partial_head` records the agent's
  last commit (HEAD differs from the plan tip). `partial_head` is absent only when HEAD equals the plan tip and the tree
  is clean, i.e. there is truly nothing to salvage. Without this, step-by-step agent commits (goal 2) could never be
  resumed (goal 4).
- **Exploration stance:** balanced. The salvage and push paths are well understood exploit work; the resume path
  (fetching a sibling execution branch into a `--depth 1` clone and checking ancestry) has one real unknown that is
  verified early with a local bare repository test.
- **Safety priority level:** elevated
- **Priority Justification:** The change edits the executor's commit and push path, which is the git safety boundary
  (`docs/safety.md` "Git is the safety boundary", Invariant 1 and Invariant 6) and writes a new field into the
  append-only `executions.jsonl` (`holon-config/world/constraints.md` section 3). It also prints diffs to stderr, so
  credential redaction must hold. No governance, estimator or `main` change is involved, so not critical.

## Exploration

- **Proportion of steps that are exploratory:** 0.1
- **Justification:** Only the shallow-clone fetch and ancestry behaviour of the resume path (Step 5) is uncertain enough
  to need a hypothesis; everything else follows existing patterns in `executor.py` and `flow.py`.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.68  |
| entropy_pred        | 10.0  |
| impact_pred         | 90.0  |
| cost_pred           | 27.5  |
| learning_value_pred | 5.0   |
| ev_pred             | 33.2  |

## Plan Description & Strategy

The work makes one guarantee: an execution's code is never lost. It is delivered as a single plan (the intent's own Q&A
rules out a split because every part edits the executor commit path) in ten ordered steps, highest value first, so a
budget breach late in the run still leaves the most important behaviour landed:

1. Baseline and map the existing assertions the new contract changes.
2. Configure the repo-local executor identity straight after the execution branch is created.
3. Salvage the agent's working tree into its own `partial work (failed)` commit and record `partial_head`.
4. Catch a refused push and print a redacted, size-capped unpushed patch block on stderr.
5. Let the executor accept an optional fourth argument (a failed execution branch) and resume from its partial head.
6. Make `holon flow` pass that fourth argument only on a Stage 3 resume.
7. Executor tests. 8. Flow tests. 9. `docs/budgets.md`. 10. Full validation and touch-set check.

Design decisions fixed by this plan (the executor must not re-decide them):

- **Order inside the failure path of `main()`:** empty-diff guard, then the existing prettier block (moved up, it only
  touches Markdown and never the ledger), then the salvage commit, then the summary row append (so the row can carry
  `partial_head`), then the unchanged staging of ledger and notes and the unchanged failure commit. On success the order
  change is behaviour-neutral: prettier still runs before `git add -A`.
- **Salvage pathspec:** everything except `holon-knowledge/ledger/executions.jsonl` and the run's own notes file
  `executions/<exec id>.md`, using `:(exclude)` pathspecs, so the later failure commit stays the one that carries the
  notes and the ledger row. `git add -A` honours `.gitignore`, so ignored files are never committed.
- **`partial_head`:** the sha of HEAD after the salvage step when HEAD differs from the plan tip (the diff base used by
  the empty-diff guard: `base_tip` after a git recovery, else `plan_tip`); absent otherwise and always absent on
  success. `superseding_failure_row` already copies every field, so superseding sync or recovery rows keep it.
- **History is never rewritten:** no amend, squash, reset or rebase is added. The existing pre-push sync uses a merge,
  so `partial_head` stays reachable from the pushed branch.
- **Resume source of truth:** flow passes only the branch name; the executor itself reads `partial_head` from that
  branch's latest summary row (reusing `flow._read_ledger_rows` and `flow._latest_execution_summary`) and verifies
  ancestry, so a stale or forged argument can only cause a logged fall-back to the plan tip.
- **Resume detection in flow:** `run_execute_stage` gains a keyword `resume_partial: bool = False`; the engine passes
  `True` only when the run's first stage is `execute` because of `--from-stage execute` or a `--checkpoint` resume. No
  `FlowContext` field is added, so the checkpoint schema is untouched.

---

## Step 1: Baseline the suite and map the assertions the new contract changes

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only, minutes of work; it de-risks Steps 3 and 7 by listing the existing tests that encode "never
  code on failure" before any edit.
- **Step Type:** INFO_GATHERING
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Confirm a green baseline and identify every test that the salvage contract will legitimately change.
- **Git branch:** the single execution branch of this plan (no per-step branch)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. From the repository root run `uv run task test` (or
   `uv run pytest apps/sandbox-executor/tests/test_executor.py apps/sandbox-executor/tests/test_flow.py` for speed) and
   `uv run task lint`; record any pre-existing failure in the execution notes so it is not attributed to this change.
2. In `apps/sandbox-executor/tests/test_executor.py` list the assertions that forbid `["git", "add", "-A"]` on a failed
   run. At planning time they are in `test_main_empty_diff_records_failed_and_skips_code_commit`,
   `test_main_empty_diff_guard_fails_closed_without_plan_tip`, the stall-breach test around the comment "code changes
   are not staged on failure", `test_breach_then_notes_only_resume_is_not_success` and
   `test_generic_exit_one_with_usable_notes_is_failure`. Under the new contract a scoped salvage add (with `:(exclude)`
   pathspecs) is expected; these assertions are rewritten in Step 7 to assert instead that no unscoped `git add -A` runs
   and that the failure commit title is still `failed for plan`, never `completed`.
3. Note the two harnesses already present: the fully mocked `run_cmd` side effect (`_run_executor_main`, `run_executor`
   in `TestExecutorIncrementalNotes`) and the real-git local bare remote helper `_create_bare_remote_with_plan`.
   Salvage, push-refusal and resume tests use the real-git helper.
4. Commit nothing in this step except, optionally, a first draft of the execution notes.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` section 3 (test changes must be declared in the plan: they are,
  here and in Steps 7 and 8); intent constraint 7 (only `uv run task test` / `uv run pytest`, never PYTHONPATH).
- **Potential failure modes for this step:** a pre-existing red test masks later regressions.
- **Guardrails and early‑abort checks:** if the baseline has failures unrelated to the touch set, record them and carry
  on; never fix files outside the touch set.

### Success & Discard Criteria

- **Success:** baseline result recorded and the list of affected assertions written into the execution notes.
- **Discard:** none; this step cannot be discarded, only shortened.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.3   |
| impact_pred         | 10.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 9.61  |

### Step Metrics Rationale

`EV = 0.97 * 10.0 + 0.5 * 3.0 - 0.3 * 0.3 - 1.5 = 9.70 + 1.50 - 0.09 - 1.50 = 9.61`. Read-only work, near-certain; its
value is preventing wasted turns later.

---

## Step 2: Configure the executor git identity before the agent runs

- **Sub‑intent recommendation:** NO
- **Reasoning:** A three-line move inside `main()` with a direct test; low risk, prerequisite for step-by-step agent
  commits.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Set `user.email` and `user.name` repo-locally immediately after `git checkout -b <exec branch>` so
  any commit the agent makes lands on the execution branch with the executor identity (goal 2).
- **Git branch:** the single execution branch of this plan
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py` add a small helper
   `_configure_executor_identity(repo_dir)` with a docstring, running the two existing `git config --local` commands
   (`executor-agent@holon-agentic-coder.com`, `Holon Executor Agent`). Introduce module constants for the two values and
   reuse them in `sync_and_reconcile_pre_push` so the identity is defined once.
2. Call it straight after the execution branch checkout (and after the resume checkout added in Step 5, which uses the
   same call site). Keep the existing calls before the final commit as harmless re-assertions, or replace them with the
   helper; either way the final commit still has an identity after a git recovery re-init (the recovery path runs
   `git init`, which discards local config, so the helper must also be called after a successful recovery before any
   commit; calling it unconditionally just before the salvage step in Step 3 covers that).
3. Never amend, squash or reset agent commits; add no such command anywhere.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` Invariant 1 (git discipline); intent goal 2 (never rewrite agent commits).
- **Potential failure modes for this step:** identity lost after the recovery re-init; a test harness whose mocked
  `run_cmd` asserts an exact command order breaks.
- **Guardrails and early‑abort checks:** run `test_executor.py` immediately after the move; fix only order-sensitive
  assertions in that file.

### Success & Discard Criteria

- **Success:** a test shows both `git config --local` commands are issued after `checkout -b` and before
  `runner.build_cmd` is first called; existing tests stay green.
- **Discard:** none expected; if order-sensitive tests explode beyond 3 assertions, keep the old late calls as well and
  only add the early call.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.6   |
| impact_pred         | 20.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 1.5   |
| ev_pred             | 18.07 |

### Step Metrics Rationale

`EV = 0.95 * 20.0 + 0.5 * 1.5 - 0.3 * 0.6 - 1.5 = 19.00 + 0.75 - 0.18 - 1.50 = 18.07`. Small, mechanical, and enables
the agent-commit half of the guarantee.

---

## Step 3: Salvage the agent's working tree into a partial-work commit and record partial_head

- **Sub‑intent recommendation:** NO
- **Reasoning:** The core of the intent and the highest-impact step, but it lives in one function region and the
  intent's Q&A forbids splitting it from the push and resume work; a sub-intent would only add rebase churn.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** On every non-success status that reaches the commit step, commit all non-ignored working-tree changes
  in `execute: <exec id> partial work (failed) for plan <plan branch>` ahead of the failure commit, and add
  `partial_head` to the failure summary row (goal 1).
- **Git branch:** the single execution branch of this plan
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add a constant for the ledger path already repeated in `main()` only if it shortens the new code; do not refactor
   unrelated call sites.
2. Add `_salvage_partial_work(repo_dir, exec_id, plan_branch, notes_rel, diff_base) -> str | None` with a docstring:
   - build the pathspec `["--", ".", ":(exclude)holon-knowledge/ledger/executions.jsonl", f":(exclude){notes_rel}"]`;
   - run `git add -A` with that pathspec (`check=False`; tracked edits, deletions and non-ignored untracked files);
   - if `git diff --cached --quiet` reports staged changes, ensure the identity (Step 2 helper) and run
     `git commit -m "execute: <exec id> partial work (failed) for plan <plan branch>"` with `check=False`; on a non-zero
     commit exit print a redacted warning naming the files left uncommitted (never silent) and continue;
   - finally resolve `git rev-parse HEAD`; return it when it differs from `diff_base`, else return None (this also
     covers agent commits with a clean tree, see the Autonomy Summary interpretation).
   - Keep it under roughly 30 lines; one helper for the pathspec if needed to stay low in branching.
3. In `main()`, inside the `else` (agent ran) branch, move the existing prettier block so it runs right after the
   empty-diff guard and before the summary row is written. Then, when `exec_status != "success"`, call the salvage
   helper with `diff_base = base_tip if recovery_triggered else plan_tip` (skip when `diff_base` is None, which is the
   fail-closed recovery case that never pushes). Store the result.
4. When the result is not None, set `exec_entry["partial_head"] = <sha>` before the existing atomic ledger append. Do
   not touch the success row. Leave `status` exactly as classified (`failure`, or the empty-diff `failed`).
5. Leave the existing failure staging untouched: it stages `executions.jsonl` and the notes and commits
   `execute: <exec id> failed for plan <plan branch>`. Replace the comment "Salvage commits (ledger + partial notes,
   never code)" with an Australian English comment describing the two-commit salvage.
6. Confirm the pre-push recovery verification and `sync_and_reconcile_pre_push` run unchanged after both commits; the
   `HEAD^` parent invariant is already satisfied by two commits.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES (without it the intent's primary goal is unmet)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` section 3 (append-only ledger: only a new field on a newly
  appended row, no edits to old rows); `docs/safety.md` Invariant 1; intent goal 1 (successful path unchanged).
- **Potential failure modes for this step:** salvage accidentally includes `executions.jsonl` so the failure commit has
  nothing to stage and the "no staged changes" branch skips the push; a secret-bearing file the agent wrote is committed
  (the same exposure the success path already has; `.gitignore` and the pre-push checks are unchanged); prettier reorder
  changes success behaviour; `git add -A` with pathspec quoting wrong on `:(exclude)`.
- **Guardrails and early‑abort checks:** a real-git test asserts the final failure commit contains exactly the ledger
  and notes and the partial commit contains everything else; a success-path test asserts one `completed` commit and no
  `partial work` commit.

### Success & Discard Criteria

- **Success:** on a budget breach and on an agent exit other than 0, the execution branch has a `partial work (failed)`
  commit with the dirty tracked and untracked files, followed by the `failed for plan` commit; the summary row has
  `status: failure` and `partial_head` equal to the partial commit; gitignored files absent; clean tree gives no
  `partial_head`.
- **Discard:** if after two attempts the reorder of prettier breaks the success path, keep prettier where it is and run
  the salvage before prettier instead, accepting unformatted Markdown in the partial commit (record the trade-off in the
  notes).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 2.0   |
| impact_pred         | 90.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 70.2  |

### Step Metrics Rationale

`EV = 0.82 * 90.0 + 0.5 * 4.0 - 0.3 * 2.0 - 5.0 = 73.80 + 2.00 - 0.60 - 5.00 = 70.20`. Highest impact: it fixes the
2026-10-27 loss directly. Entropy reflects reordering inside a ~800-line `main()` and five existing assertions that
change meaning.

---

## Step 4: Preserve the work when the execution branch push is refused

- **Sub‑intent recommendation:** NO
- **Reasoning:** Self-contained helper plus one call-site change; the redaction requirement makes it security-relevant
  but the redaction functions already exist and are tested.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Replace the checked `git push` with a handled one that, on refusal, reports and prints a recoverable,
  redacted, size-capped patch block on stderr and exits non-zero (goal 3).
- **Git branch:** the single execution branch of this plan
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add module constants: `UNPUSHED_PATCH_BEGIN = "BEGIN UNPUSHED EXECUTION PATCH"`,
   `UNPUSHED_PATCH_END = "END UNPUSHED EXECUTION PATCH"` and `UNPUSHED_PATCH_MAX_BYTES = 256 * 1024` (named, documented
   with a one-line comment on why the host log can hold it).
2. Add `_report_unpushed_work(repo_dir, exec_branch, plan_tip, reason) -> None` with a docstring: resolve the local HEAD
   sha; print `Error: push of execution branch '<branch>' was refused; local head <sha>: <redacted reason>` to stderr;
   when `plan_tip` is known run `git format-patch --stdout <plan tip>..HEAD` (`check=False`), redact the output with
   `redact_agent_secrets` (the same function `run_cmd` uses for failures), measure the redacted size in UTF-8 bytes, and
   either print the begin marker, the patch and the end marker, or, above the cap, print
   `git diff --stat <plan tip>..HEAD` (redacted) followed by a line saying the patch was omitted because it exceeded
   `UNPUSHED_PATCH_MAX_BYTES`. When `plan_tip` is unknown print the diffstat-free notice only. Split into two helpers
   (`_unpushed_patch_text`, `_report_unpushed_work`) if branching grows.
3. At the push call site run `git push -u origin <exec branch>` with `check=False`. On a zero exit keep the existing
   success messages and set `branch_recorded = True` only then (move it after the push for the push case; keep it set
   for the `HOLON_SKIP_PUSH` local-only case as today). On a non-zero exit call the helper with `(stderr or stdout)`,
   print the stage summary line when the agent ran, and `sys.exit(1)`. The exit happens inside the `try`, so the
   `finally` still cleans ephemeral credentials; the patch is already on stderr before cleanup.
4. Add no token-scope check or pre-flight gate.

### Dependencies & Criticality

- **Depends on:** Step 3 (the patch must include the partial-work commit)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` Invariant 1 and sandbox containment (no new mount, stderr only); intent goal 3
  (redacted output, named cap); `run_cmd` docstring (callers must redact raw output).
- **Potential failure modes for this step:** unredacted token in the diff or in git's reason; the patch includes the
  pre-push sync merge's `main` commits (format-patch over `plan_tip..HEAD` lists them), inflating it past the cap so
  only a diffstat is shown; `branch_recorded` semantics for the over-budget refusal path drift.
- **Guardrails and early‑abort checks:** test with a fake token built by the existing `_fake_token` helper written into
  a committed file, asserting the raw value never appears in captured stderr; test that the over-budget path still
  returns `OVER_BUDGET_EXIT_CODE` when the push succeeds.

### Success & Discard Criteria

- **Success:** a refused push against a local bare remote (for example a `pre-receive` hook that exits 1) exits
  non-zero, prints both markers around a patch that `git am` could apply, never prints the fake token, and an oversized
  patch prints the diffstat and the omission line instead.
- **Discard:** if the merge-inflation case makes the patch unusable in tests, keep the behaviour (the goal names
  `<plan tip>..HEAD` explicitly) and document the limitation in `docs/budgets.md`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.3   |
| impact_pred         | 55.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 44.86 |

### Step Metrics Rationale

`EV = 0.85 * 55.0 + 0.5 * 3.0 - 0.3 * 1.3 - 3.0 = 46.75 + 1.50 - 0.39 - 3.00 = 44.86`. Covers the second way work dies
with the container; moderate entropy because it touches exit semantics.

---

## Step 5: Let the executor resume from a failed execution branch's partial head

- **Sub‑intent recommendation:** NO
- **Reasoning:** The riskiest step (shallow-clone fetch and ancestry), but it shares the branch-creation lines with Step
  2 and is guarded by a fall-back to today's behaviour, so failure degrades gracefully rather than breaking runs.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** In the `--depth 1 --single-branch` clone,
  `git fetch <repo url> refs/heads/<failed branch>:refs/remotes/origin/<failed branch>` brings the failed branch's
  commits down to the shallow boundary (the plan tip), so `git merge-base --is-ancestor <plan tip> <partial head>`
  succeeds without deepening.
- **Learning target:** whether shallow clones need `--deepen` or `--unshallow` for the ancestry check, and whether the
  existing `_read_ledger_rows` can read the failed branch's ledger from the fetched remote ref.
- **Maximum acceptable cost for this learning:** about 1.5 cost units (one real-git bare-remote test); beyond that, use
  `--deepen=50` as `sync_and_reconcile_pre_push` already does and move on.

### Intent & Git Integration

- **Step Intent:** Accept an optional fourth positional argument naming a failed execution branch; when its latest
  summary row has a `partial_head` that descends from the plan tip, cut the new execution branch from it and append a
  continuation note to the rendered prompt; otherwise start from the plan tip and log why (goal 4, executor half).
- **Git branch:** the single execution branch of this plan
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Update the usage string to `executor.py <plan_branch> [agent_name] [model_name] [partial_execution_branch]` and read
   `sys.argv[4]` when present.
2. Before `git checkout -b <exec branch>`, record `plan_tip` from `git rev-parse HEAD` on the plan branch (move the
   existing lookup up; it must still be the plan branch tip so the empty-diff guard keeps comparing against the plan).
3. Add `_resolve_partial_head(repo_dir, repo_url, partial_branch, plan_tip) -> tuple[str | None, str]` with a docstring,
   returning the sha or None plus a reason string. It fetches the branch into `refs/remotes/origin/<branch>`
   (`check=False`), reads the latest summary row for the branch's execution id with the flow helpers (lazy import, as
   `main()` already does for `check_and_resolve_stage_conflicts`), requires `status == "failure"` and a string
   `partial_head`, checks the sha exists (`git cat-file -e <sha>^{commit}`) and runs
   `git merge-base --is-ancestor <plan tip> <sha>`, deepening once with `--deepen=50` if the first check fails in a
   shallow repository. Each refusal returns a specific reason ("fetch failed", "no partial_head", "not a descendant of
   the plan tip").
4. Cut the branch with `git checkout -b <exec branch> <partial head>` when resolved, else exactly as today; print
   `Resuming partial work from <branch> at <sha>` or `Starting from the plan tip of <plan>: <reason>` (redacted).
5. Add `continuation_note(partial_branch, partial_head) -> str` returning two or three sentences in Australian English
   telling the agent that the working tree already holds partial work from the named failed execution, to review the
   existing commits and the plan's completed steps, and to continue rather than redo them. Append it to `base_prompt`
   before `full_prompt` is built so follow-up turns (which receive `context=base_prompt`) see it too. No template is
   read or edited for this.
6. Do not change the success-path diff base, the recovery path (it refetches the plan branch and so naturally resets to
   the plan tip, keeping the agent's tree), or the plain `holon execute` invocation (it passes three arguments).

### Dependencies & Criticality

- **Depends on:** Steps 2 and 3 (`partial_head` must exist to resume from)
- **Is Bottleneck:** NO (the guarantee "code is never lost" holds without resume; resume adds continuity)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` section 1 (execution runs are cut from the plan branch: a
  partial head that descends from the plan tip keeps the plan branch as the base, and anything else falls back);
  `docs/safety.md` Invariant 1; intent goal 4 (no prompt template edit, no `cli.py` change).
- **Potential failure modes for this step:** shallow fetch leaves the ancestry check unanswerable; a forged branch name
  injects odd refspec text (validate it starts with the plan prefix plus `/E-` and ends with `/_`, otherwise fall back);
  the prompt note leaks into a template test.
- **Guardrails and early‑abort checks:** every failure returns to today's behaviour with a logged reason; a real-git
  test covers descend, not-descend and missing-field cases.

### Success & Discard Criteria

- **Success:** with a fourth argument whose branch carries a descending `partial_head`, the new execution branch's first
  parent chain contains that sha, the prompt the runner receives ends with the continuation note, and the empty-diff
  guard still diffs against the plan tip; the three fall-back cases start from the plan tip and print the reason.
- **Discard:** if the ancestry check cannot be made reliable in a shallow clone within the learning budget, keep the
  fall-back path only (log "resume unavailable") and report the gap in the execution notes; Steps 3 and 4 still stand.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.72  |
| entropy_pred        | 2.0   |
| impact_pred         | 60.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 40.1  |

### Step Metrics Rationale

`EV = 0.72 * 60.0 + 0.5 * 5.0 - 0.3 * 2.0 - 5.0 = 43.20 + 2.50 - 0.60 - 5.00 = 40.10`. Lowest success probability in the
plan because of the shallow-clone unknown; the learning value is the highest for the same reason.

---

## Step 6: Pass the failed execution branch on a Stage 3 flow resume

- **Sub‑intent recommendation:** NO
- **Reasoning:** One helper and one argument change in `flow.py`, reusing existing branch snapshot and ledger readers.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** When `holon flow` resumes at Stage 3, dispatch the executor with
  `[plan_branch, agent, model, partial_branch]` if origin holds a failed execution branch under the plan whose latest
  summary row carries `partial_head`; otherwise keep three arguments (goal 4, flow half).
- **Git branch:** the single execution branch of this plan
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/flow.py` add
   `_resumable_partial_branch(repo_dir, branches) -> str | None` with a docstring: iterate the snapshot
   `existing_executions` newest first (sort on the integer after `E-`), read each branch's `executions.jsonl` at
   `refs/remotes/origin/<branch>` with `_read_ledger_rows`, take `_latest_execution_summary` for its execution id, and
   return the first branch whose row has `status == "failure"` and a non-empty string `partial_head`.
2. Change `run_execute_stage(context)` to `run_execute_stage(context, resume_partial: bool = False)`. After the snapshot
   succeeds, build the positional list `[context.plan_branch, context.agent, context.model]`; when `resume_partial` is
   true and the helper returns a branch, append it and log `Stage 3 resume: continuing partial work from <branch>`; when
   true and none is found, log that the resume starts from the plan tip.
3. In `PipelineEngine.execute_stage` (and the timed wrapper that calls it), pass `resume_partial=True` only when the
   stage is `EXECUTE`, it is the first stage of this `run()` and `self.from_stage == FlowStage.EXECUTE`. A
   `--checkpoint` resume already sets `from_stage` from the checkpoint in `cli.py`, so both resume forms are covered
   without touching `cli.py`. Store the first-stage flag on the engine (not on `FlowContext`) so nothing new is
   checkpointed.
4. Leave adoption of the new branch unchanged: `_discover_child_branch` still only adopts branches absent from the
   snapshot, so the failed branch is never mistaken for the new run.

### Dependencies & Criticality

- **Depends on:** Step 5 (the executor must understand the fourth argument before flow sends it)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent touch set (`cli.py` is off-limits; the positional list is already forwarded); checkpoint
  schema stability (`CURRENT_CONTEXT_SCHEMA_VERSION` unchanged).
- **Potential failure modes for this step:** the engine's call signature is used by many flow tests with mocks that do
  not accept the keyword; dry-run path accidentally reads ledgers.
- **Guardrails and early‑abort checks:** keep the keyword optional with a default; place the lookup after the dry-run
  and skip-push early returns; run the whole `test_flow.py` after the change.

### Success & Discard Criteria

- **Success:** a resumed Stage 3 with a matching branch dispatches four positional arguments; without one, or on a
  non-resumed run, three.
- **Discard:** if engine plumbing touches more than the two call sites, fall back to deriving the flag inside
  `run_execute_stage` from a non-checkpointed engine attribute and record why.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 1.3   |
| impact_pred         | 45.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 35.01 |

### Step Metrics Rationale

`EV = 0.82 * 45.0 + 0.5 * 3.0 - 0.3 * 1.3 - 3.0 = 36.90 + 1.50 - 0.39 - 3.00 = 35.01`. `flow.py` is 5,100 lines with
dense tests, so integration risk, not logic, drives the entropy.

---

## Step 7: Executor tests for salvage, identity, refused push and resume

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tests are part of the acceptance criteria and live in the same file as the assertions Step 3 changes;
  splitting would leave a red suite between branches.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Cover every executor behaviour named in goal 6 with pytest, using local bare repositories under
  `tmp_path` and mocked runners only.
- **Git branch:** the single execution branch of this plan
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Rewrite the five assertions found in Step 1 so they forbid an unscoped `git add -A` (no pathspec) on failure and
   still require the `failed for plan` title, rather than forbidding any `git add -A`.
2. Add a real-git helper (beside `_create_bare_remote_with_plan`) that runs `executor.main()` against a bare origin with
   a fake runner whose turn edits a tracked file, deletes another, creates an untracked file and an ignored file
   (`.gitignore` seeded on the plan branch), writes notes, and returns a chosen exit code or `BudgetBreach`. Push
   enabled to the bare remote; no network, Docker, gh or LLM.
3. Tests (names in the existing style):
   - budget breach with dirty tracked and untracked files: on the pushed branch, `HEAD~1` is titled
     `partial work (failed)` and contains the edits, deletion and untracked file; `HEAD` is the `failed for plan` commit
     with only the ledger and notes; the summary row has `status: failure` and `partial_head == HEAD~1` sha;
   - the same for an agent exit 1 without a breach;
   - the ignored file is absent from every commit;
   - nothing to salvage (agent writes only notes): no `partial work` commit and no `partial_head` key;
   - success path: exactly one `completed` commit on top of the plan tip, no `partial_head`;
   - identity before dispatch: in the mocked harness, both `git config --local user.*` commands appear after
     `checkout -b` and before the first `runner.build_cmd` call;
   - refused push: a `pre-receive` hook in the bare remote exits 1; `main()` raises `SystemExit` non-zero; captured
     stderr contains the branch name, the local head sha, both marker lines and a patch whose `Subject:` lines include
     the partial-work commit; a fake token from `_fake_token` placed in a committed file never appears unredacted;
   - oversized patch: patch `UNPUSHED_PATCH_MAX_BYTES` to a small value; stderr has the diffstat and the omission line
     and no begin marker;
   - resume: a fourth argument naming a failed branch with a descending `partial_head` cuts the new branch from it and
     the runner's prompt ends with the continuation note; a branch without `partial_head`, and one whose head does not
     descend from the plan tip, start from the plan tip and print the reason.
4. Use `capsys` or `capfd` for stderr; keep each test under the hermetic fixtures already in `conftest.py`.

### Dependencies & Criticality

- **Depends on:** Steps 2 to 5
- **Is Bottleneck:** YES (the intent is accepted only with these tests passing)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` section 3 (declared test changes); intent goal 6 (no real Docker,
  remotes, gh or runners); `apps/sandbox-executor/docs/hermetic_testing.md` guards.
- **Potential failure modes for this step:** hermetic guard rejects the bare-remote push; time spent debugging the large
  mocked harness; flaky timestamps in execution ids when two runs share a second.
- **Guardrails and early‑abort checks:** start from the existing bare-remote tests (they already push to `bare_dir`),
  patch `time.time` where two runs happen in one test.

### Success & Discard Criteria

- **Success:** all new and rewritten tests pass with `uv run pytest apps/sandbox-executor/tests/test_executor.py`.
- **Discard:** if a single scenario consumes more than twice its share, keep the mocked-harness variant of that test and
  note the reduced fidelity in the execution notes.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 1.2   |
| impact_pred         | 40.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 29.14 |

### Step Metrics Rationale

`EV = 0.80 * 40.0 + 0.5 * 3.0 - 0.3 * 1.2 - 4.0 = 32.00 + 1.50 - 0.36 - 4.00 = 29.14`. Nine scenarios against real git
are the single largest cost item.

---

## Step 8: Flow tests for the Stage 3 resume dispatch

- **Sub‑intent recommendation:** NO
- **Reasoning:** Two or three focused tests patching `_dispatch_role`, following the existing pattern in `test_flow.py`.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove that a Stage 3 resume dispatches four positional arguments when a failed branch carries
  `partial_head`, and three otherwise (goal 6, flow part).
- **Git branch:** the single execution branch of this plan
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Reuse the existing `_dispatch_role` patching style; patch `_snapshot_child_branches` to return one failed branch and
   `_read_ledger_rows` to return a failure summary row with `partial_head` (or without it).
2. Run the engine with `from_stage="execute"` and assert the captured positional list has four items, the fourth being
   the failed branch; repeat without `partial_head` and assert three.
3. Add a non-resumed case (engine started from Stage 1 path, or `run_execute_stage(context)` with the default) that
   still dispatches three even when a `partial_head` branch exists.
4. Let the dispatch mock return a non-zero code so the stage ends early without needing a newly pushed branch.

### Dependencies & Criticality

- **Depends on:** Step 6
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 6; `holon-config/world/ruleset.md` section 3.
- **Potential failure modes for this step:** pre-dispatch checks (veto, budget precheck, conflict resolution) need extra
  patches before dispatch is reached.
- **Guardrails and early‑abort checks:** copy the patch set from the nearest existing Stage 3 dispatch test.

### Success & Discard Criteria

- **Success:** the three flow tests pass and the full `test_flow.py` stays green.
- **Discard:** none expected.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 0.6   |
| impact_pred         | 20.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 16.42 |

### Step Metrics Rationale

`EV = 0.88 * 20.0 + 0.5 * 2.0 - 0.3 * 0.6 - 2.0 = 17.60 + 1.00 - 0.18 - 2.00 = 16.42`.

---

## Step 9: Document salvage, partial_head, the unpushed patch and resume in docs/budgets.md

- **Sub‑intent recommendation:** NO
- **Reasoning:** One paragraph replaced and one short subsection added in a single permitted doc.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Replace "stages the partial notes, but not the code changes" with the new behaviour (goal 5).
- **Git branch:** the single execution branch of this plan
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Rewrite the paragraph at about `docs/budgets.md:167-170`: the executor commits the agent's working-tree changes in
   `execute: E-... partial work (failed) for plan ...`, then the notes and summary row in
   `execute: E-... failed for plan ...`; gitignored files are never committed; nothing is squashed or amended.
2. Add a short subsection (for example `### Partial work on failure`) covering: the optional `partial_head` field on the
   failure summary row (sha of the last salvaged or agent commit, absent when nothing differs from the plan tip and on
   success); the `BEGIN UNPUSHED EXECUTION PATCH` / `END UNPUSHED EXECUTION PATCH` block, its size cap and diffstat
   fall-back, and how to recover it (copy the lines between the markers from the captured log into a file and run
   `git am` on a checkout of the plan branch); the resume semantics (`holon flow --checkpoint` or `--from-stage execute`
   continues from the newest failed branch with a `partial_head` that descends from the plan tip; plain `holon execute`
   and non-resumed flows start from the plan tip).
3. Australian English; do not edit `docs/ledger_schema.md`, `docs/git_flow.md` or `README.md`. Run
   `uv run task lint-docs` and let prettier settle the table and wrapping.

### Dependencies & Criticality

- **Depends on:** Steps 3 to 6 (document what was actually built, including any discarded fall-back)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 5 and touch set; intent constraint 7 (no harness paths or ids in target files).
- **Potential failure modes for this step:** markdown lint failure; documenting behaviour that a discard criterion
  removed.
- **Guardrails and early‑abort checks:** write the doc last and re-read the final code paths before writing.

### Success & Discard Criteria

- **Success:** the old sentence is gone, the new subsection exists and `uv run task lint-docs` passes.
- **Discard:** none.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 0.4   |
| impact_pred         | 15.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 13.33 |

### Step Metrics Rationale

`EV = 0.93 * 15.0 + 0.5 * 1.0 - 0.3 * 0.4 - 1.0 = 13.95 + 0.50 - 0.12 - 1.00 = 13.33`.

---

## Step 10: Full validation, touch-set audit and execution notes

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard closing gate; cheap and decisive.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the whole change passes `uv run task test`, `uv run task lint` and `uv run task lint-docs`, and
  that only the five permitted files (plus the flow's own records) changed.
- **Git branch:** the single execution branch of this plan
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run `uv run task test`, `uv run task lint` and `uv run task lint-docs` from the repository root; fix only within the
   touch set.
2. Run `git diff --name-only <plan tip>` and confirm the list is limited to `executor.py`, `flow.py`,
   `test_executor.py`, `test_flow.py`, `docs/budgets.md`, the run's `executions/` notes and appended ledger rows.
3. Grep the diff for harness paths or ids (`.beans/`, `todo/`, `.agents/`) and for American spellings in new strings
   (for example "behavior", "initialize", "sanitize" in new prose; existing identifiers stay as they are).
4. Complete the execution notes: steps done, any discard criterion taken, and any step that needed a file outside the
   touch set (dropped and reported, never edited).

### Dependencies & Criticality

- **Depends on:** Steps 1 to 9
- **Is Bottleneck:** YES (a red suite or touch-set breach fails the intent)

### Safety & Constraint Considerations

- **Relevant rules:** intent constraint 7 and touch set; `holon-config/world/constraints.md` section 3 (ledgers only
  appended).
- **Potential failure modes for this step:** lint-only failures (line length, complexity rule on `main()`); unrelated
  flaky tests.
- **Guardrails and early‑abort checks:** if a complexity lint flags `main()`, extract the new code into the helpers
  already planned rather than suppressing the rule.

### Success & Discard Criteria

- **Success:** all three commands pass and the touch-set audit is clean.
- **Discard:** none; failures are fixed or reported.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.3   |
| impact_pred         | 30.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 26.41 |

### Step Metrics Rationale

`EV = 0.90 * 30.0 + 0.5 * 2.0 - 0.3 * 0.3 - 1.5 = 27.00 + 1.00 - 0.09 - 1.50 = 26.41`.

---

### Strategy Rationale

The intent is accepted as is. Its Q&A already settles the split question (one guarantee, one commit path), the touch set
is narrow and explicit, and the predicted entropy (10.0) fits the 12.0 budget, so there is no sub-intent list. The only
autonomy exercised is to pin down design choices the intent leaves open (salvage pathspec, where `partial_head` comes
from when the agent already committed, how flow detects a Stage 3 resume without touching the checkpoint schema or
`cli.py`), so the executor agent does not spend its stage budget re-deciding them.

Steps are ordered by value so that a stage-budget breach part-way through (the very failure this intent fixes, and a
real risk for a change of this size) still leaves the salvage and push guarantees landed first. Until this change ships
the executor will itself lose uncommitted work on a breach, so the executor agent should commit after each completed
step with an explicit identity (for example `git -c user.name=... -c user.email=... commit`); the current entrypoint
pushes the agent's own commits under the failure commit, so they survive.

Aggregation:

- `entropy_pred` is the sum of step entropies: `0.3 + 0.6 + 2.0 + 1.3 + 2.0 + 1.3 + 1.2 + 0.6 + 0.4 + 0.3 = 10.0`.
- `cost_pred` is the sum of step costs: `1.5 + 1.5 + 5.0 + 3.0 + 5.0 + 3.0 + 4.0 + 2.0 + 1.0 + 1.5 = 27.5`.
- `p_success_pred` is a qualitative judgement, 0.68. The naive product of step probabilities (about 0.22) is too
  pessimistic because failures inside one execution are repairable and strongly correlated (the same agent, the same
  file); the bottleneck is Step 5 (0.72), discounted for the large `main()` and the dense test files.
- `impact_pred` is 90.0, set by Step 3: never losing an execution's code is the core of the intent.
- `learning_value_pred` is 5.0, the maximum step value (Step 5's shallow-clone ancestry question).
- `ev_pred`: `EV = 0.68 * 90.0 + 0.5 * 5.0 - 0.3 * 10.0 - 27.5 = 61.20 + 2.50 - 3.00 - 27.50 = 33.20`.

Strong assumptions: the clone stays `--depth 1 --single-branch` with origin set to the repository URL; the pre-push sync
keeps using merge (not rebase), so recorded `partial_head` shas stay reachable; `superseding_failure_row` keeps copying
every field of the row it supersedes.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` section 1 (execution runs are cut from the plan branch; scoped commits): the
    resume base must descend from the plan tip or the run falls back to the plan tip.
  - `holon-config/world/constraints.md` section 3 and `docs/safety.md` Invariant 6 (append-only ledgers): `partial_head`
    is a new optional field on newly appended rows only.
  - `docs/safety.md` "Git is the safety boundary" and Invariant 1: no history rewriting, unchanged pre-push safety and
    sync checks, the no-parent push invariant kept.
  - `docs/safety.md` sandboxing: no new container mount; the unpushed patch travels on stderr only.
  - `holon-config/world/ruleset.md` sections 2 and 3: typed, docstringed helpers; test changes declared in Steps 1, 7
    and 8; pytest via `uv run` only.
  - Intent constraint 7: touch set of five files, Australian English, no new dependencies, no harness paths.
- **Potential violations or edge cases:**
  - Credentials appearing in the printed patch or in git's push refusal message.
  - The salvage commit picking up a secret file the agent created that `.gitignore` does not exclude (same exposure as
    today's success path).
  - A forged or stale fourth argument steering the execution branch base.
  - The pre-push sync merge inflating `<plan tip>..HEAD` so only a diffstat is printed.
  - Prettier reorder or identity move subtly changing the success path.
  - A needed change in `cli.py`, a prompt template or `docs/ledger_schema.md` (off-limits).
- **Mitigations built into the plan:**
  - All printed git output goes through `redact_agent_secrets`, proven by a fake-token test (Step 7).
  - Salvage reuses `.gitignore` and leaves the existing pre-push checks untouched; no new push path is added.
  - The executor re-reads `partial_head` from the branch ledger, validates the branch name shape and checks ancestry,
    falling back to the plan tip with a logged reason.
  - The cap and diffstat fall-back are named constants and documented, including the merge-inflation limitation.
  - A success-path test asserts exactly one `completed` commit; existing success tests stay unchanged.
  - Any step that would need an off-limits file is dropped and reported in the execution notes.
- **Residual risk accepted (and why):** salvaged partial work may include broken or unformatted code on a failed
  execution branch; that branch never merges on its own, and keeping the work is the explicit purpose of the intent. The
  resume path may fall back to the plan tip more often than ideal on shallow clones; that is today's behaviour, so it
  cannot regress anything.
- **Allocated Entropy Budget:** 12.0
- **Predicted Plan Entropy:** 10.0
- **Budget Compliance:** The strategy fits within budget (10.0 of 12.0, leaving 2.0 headroom for the Step 5 learning
  overrun), so it runs as one plan with no sub-intent list.

## Sub-intent list

- None
