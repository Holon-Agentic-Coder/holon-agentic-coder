# Plan for I-1792762866-mergeability-missing-ref-not-conflict

- **Plan ID:** P-1792765112-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-23T14:18:33.143Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A. Two clarifications are made within scope, not as a reframe. First, a
  `MISSING_REF` result blocks consensus approval and ends the review loop early, because a ref that does not resolve
  will not resolve on the next iteration. Second, the legacy `merge-base` plus 3-way `merge-tree` heuristic, which runs
  on any other non-zero exit, is removed. Goal 2 requires every non-zero exit other than 1 to be `UNKNOWN`.
- **Exploration stance:** balanced. The defect is clear and was reproduced during planning, so most steps are exploit
  steps. One small exploratory step probes git edge cases: unrelated histories, annotated tags and refs that are trees
  rather than commits. These will show whether the new exit-code contract holds beyond the three cases the intent names.
- **Safety priority level:** elevated
- **Priority Justification:** The change alters the signal that `run_review_stage` uses to fail a PR as
  `conflicted_dirty`. That signal is part of the Bean 0034 human-review gate (`safety.md` "Human review is the final
  gate"). An error that turns a real `DIRTY` into `UNKNOWN` would quietly weaken a merge guardrail. The new tests also
  run real `git` subprocesses. `git` is on the process-sandbox whitelist (`safety.md` "Process sandbox"), but any `gh`
  call would reach the network and must be blocked in tests (`holon-config/world/constraints.md` section 2, Escape
  Actions).

## Exploration

- **Proportion of steps that are exploratory:** 0.2
- **Justification:** One of five steps (Step 4) is EXPLORATORY. Planning already reproduced the root cause on git
  2.47.3: `git merge-tree --write-tree nosuch main` exits **1**, the same code as a real conflict. It also showed that
  unrelated histories exit **128**. Little uncertainty remains about the main fix. The exploratory budget therefore goes
  to edge cases that could make the "exit 1 means conflict" rule wrong after both refs resolve.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 5.8   |
| impact_pred         | 85.0  |
| cost_pred           | 16.0  |
| learning_value_pred | 4.0   |
| ev_pred             | 52.26 |

### Strategy Rationale

**Root cause (confirmed during planning, no repository changes made):**

- In the local fallback, `check_pr_mergeability()` (`apps/sandbox-executor/src/sandbox_executor/flow.py` around lines
  900–951) checks the branch ref only to decide whether to substitute `origin/<branch>`. When neither the local nor the
  remote name resolves, it still passes the unresolvable name to `git merge-tree --write-tree`.
- On git 2.47.3 (the sandbox image version), a ref that cannot be resolved makes `merge-tree` print
  `merge-tree: <ref> - not something we can merge` and exit **1**. The code maps exit 1 to `CONFLICTING`/`DIRTY`. The
  same happens for a missing target ref: `origin/main` is absent and the bare `main` fallback is passed through without
  being checked.
- `run_review_loop()` then emits `FIND-DIRTY-NNN` with severity `critical`. `run_review_stage()` fails with
  `[CRITICAL: CI SIGNAL SUPPRESSED] ... has merge conflicts`. That is a false diagnosis: it sends resolvers after a
  conflict that does not exist instead of a missing branch or remote.

**Strategy:** test-first, in one execution branch.

1. Write real-temporary-repository tests that fail against the current code.
2. Fix ref resolution and the exit-code mapping.
3. Teach the review loop and review stage to report `MISSING_REF` distinctly.
4. Probe the edge cases with extra tests.
5. Run the full quality gate.

The whole change touches two files (`flow.py` and `tests/test_flow.py`), so a single execution branch is the
lowest-entropy route.

**Aggregation:**

- **p_success:** For the four blocking steps (1, 2, 3, 5), the product under an independence assumption is
  `0.92 * 0.88 * 0.85 * 0.90 = 0.619`. This is treated as a lower bound. The failures are strongly correlated (one root
  cause, one file), and a failed test run can be fixed and rerun inside the same branch. Step 4 does not block. Judged
  overall: `0.80`. Step 3 is the bottleneck (`p = 0.85`), because it changes the review-stage narrative and the
  consensus semantics, which other tests already assert on.
- **Entropy:** sum of steps, `1.2 + 1.8 + 1.6 + 0.8 + 0.4 = 5.8`.
- **Impact:** the blocking step impacts overlap (`20 + 45 + 30 + 10 = 105` raw). A qualitative cap gives `85`. The fix
  removes a false-critical signal from the review gate, which every intent passes through.
- **Cost:** sum of steps, `3 + 5 + 4 + 2 + 2 = 16`.
- **Learning value:** weighted towards the exploratory step's `5.0`, giving `4.0`.
- **EV:**
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost = 0.80 * 85.0 + 0.5 * 4.0 - 0.3 * 5.8 - 16.0 = 68.00 + 2.00 - 1.74 - 16.0 = 52.26`.

**Strong assumptions:**

- The executor's git version is 2.38 or later, so `--write-tree` is supported. 2.47.3 was observed in the sandbox.
- No production caller depends on the legacy 3-way heuristic.
- `gh` is unavailable in the tests or patched out of them.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1: work only on the `I-1792762866-mergeability-missing-ref-not-conflict/...`
    branch hierarchy. Never commit to `main`. Rebase from the parent before execution and before merge.
  - `holon-config/world/constraints.md` §2 and `docs/safety.md` "Process sandbox": predicted entropy 5.8 is below 10, so
    the process sandbox applies. Only whitelisted subprocesses (`git`, `pytest`, via `uv`) may run. No network.
  - `holon-config/world/constraints.md` §3 and `docs/safety.md` Invariant 6: the ledger is append-only. Only
    `intents.jsonl`, `plans.jsonl` and `executions.jsonl` may be used, and no auxiliary ledgers.
  - `holon-config/world/ruleset.md` §2: typed public signatures, plus docstrings that cover inputs, outputs, exceptions
    and side effects.
  - `holon-config/world/ruleset.md` §3: tests use pytest and live in `tests/test_flow.py`, which matches `flow.py`. Test
    changes must be declared in the plan. This plan declares new tests (Steps 1 and 4) and one updated mocked test (Step
    2).
  - Intent constraints:
    - Australian English (e.g. "behaviour", "normalise", "recognised").
    - No new dependencies.
    - Run tests only with `uv run pytest` from `apps/sandbox-executor`. No `unittest` runner and no `PYTHONPATH`.
    - `uv run ruff check` and `uv run ruff format --check` must pass.
    - Format markdown with the repository's `.prettierrc.json` (`printWidth` 120, `proseWrap` always).
    - Keep paths target-native.
  - `docs/safety.md` "Human review is the final gate" (Bean 0034): a genuine `DIRTY` must still fail the review stage.
- **Potential violations or edge cases:**
  - The real-repo tests could invoke `gh pr view` if `gh` is installed in the image. It was recently added to the agent
    images. That would be a network call from inside the sandbox.
  - The tests' git commits could inherit a global `commit.gpgsign` setting or lack a user identity. The commits would
    then fail or hang.
  - The existing mocked test `test_check_pr_mergeability_local_git_fallback_with_remote_tracking_ref` returns exit 1 for
    any ref it does not recognise. Once the code verifies `<ref>^{commit}`, it would wrongly produce `MISSING_REF`.
  - A misconfigured or unpushed target branch (no `origin/main` and no local `main`) will now return `UNKNOWN` instead
    of a false `DIRTY`. The review stage must still fail closed rather than approve.
  - `check_and_resolve_stage_conflicts()` only reacts to `is_dirty` or `CONFLICTING`, so `MISSING_REF` simply skips it.
    This is acceptable and must not raise.
- **Mitigations built into the plan:**
  - Every real-repo test patches `shutil.which` to return `None` for `gh`. Repositories are created under pytest
    `tmp_path` with `git init -b main`.
  - Commits use `-c user.name=... -c user.email=... -c commit.gpgsign=false`.
  - Step 2 explicitly declares the update to the existing mocked test so that `^{commit}` lookups are recognised.
  - `MISSING_REF` blocks consensus (fail closed) and produces its own review-stage error. It never produces approval.
  - A dedicated regression test keeps genuine conflicts mapped to `CONFLICTING`/`DIRTY`, with the existing
    `CI SIGNAL SUPPRESSED` assertion kept unchanged.
- **Residual risk accepted (and why):** Removing the legacy pre-2.38 `merge-tree` heuristic means git versions older
  than 2.38 will report `UNKNOWN` rather than a guess. The images ship 2.47.3, and the intent explicitly asks for
  `UNKNOWN` on other non-zero exits. An honest `UNKNOWN` is safer than a heuristic that cannot tell a missing ref from a
  conflict.
- **Allocated Entropy Budget:** 8.0
- **Predicted Plan Entropy:** 5.8
- **Budget Compliance:** The strategy fits within budget (5.8 of 8.0, 72.5%). That is below the 80% warning threshold in
  `safety.md`.

## Plan Description & Strategy

This is a test-first fix inside one execution branch:
`I-1792762866-mergeability-missing-ref-not-conflict/P-1792765112-claude-agent-claude-opus-5-5/E-{timestamp}-implement/_`,
created off the plan branch, which is created off the intent branch. It is rebased from its parent before execution and
before merge.

**Target contract for the `git_local` path of `check_pr_mergeability()`:**

1. **Ref substitution.** Keep the existing substitution rules unchanged.
   - Branch: an empty branch becomes `HEAD`. If the local name fails `rev-parse --verify`, try `origin/<branch>`.
   - Target: try `origin/<target>`, then the bare `<target>`.
2. **Resolution gate.** For each chosen ref, run `git rev-parse --verify <ref>^{commit}`, checking the branch ref first
   and then the target ref. On the first failure, return immediately. Do not call `merge-tree`. Return:
   - `mergeable` `UNKNOWN`
   - `merge_state_status` `MISSING_REF`
   - `is_dirty` `False`
   - `source` `git_local`
   - `diagnostics` set to a dict with:
     - `missing_ref`: the final candidate ref
     - `ref_role`: `branch` or `target`
     - `candidates_tried`: the list of names tried
     - `stderr`: the stripped `rev-parse` stderr
     - `message`: `cannot evaluate mergeability for ref <X>`
3. **Merge simulation.** Run `git merge-tree --write-tree <branch_sha> <target_sha>` using the resolved SHAs, so no
   ambiguity can come back. Map the exit codes:
   - `0`: `MERGEABLE`/`CLEAN`
   - `1`: `CONFLICTING`/`DIRTY`, with `is_dirty True`
   - anything else: `UNKNOWN`/`UNKNOWN`, with `diagnostics` holding `merge_tree_exit_code` and the stripped `stderr`
4. **Exceptions.** Keep the existing `UNKNOWN`/`UNKNOWN` behaviour. Add `diagnostics.error` with the exception text.
5. **Return shape.** Every return includes a `diagnostics` key. It is an empty dict when there is nothing to report,
   including the `gh` path. Update the docstring's key list to include `MISSING_REF` and `diagnostics`.

The review loop treats `MISSING_REF` as "cannot evaluate", which is neither clean nor dirty:

- It logs `cannot evaluate mergeability for ref <X>`.
- It records the finding `FIND-REF-NNN` (severity `important`), never `FIND-DIRTY`.
- It withholds consensus and stops iterating.

The review stage reports that message instead of the `CI SIGNAL SUPPRESSED` narrative.

---

## Step 1: Add failing real-repository regression tests for mergeability ref resolution

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is small and low-risk: test additions only, in a single file. A sub-intent would add branch and
  ledger overhead with no isolation benefit, since the tests must land together with the fix to be meaningful.
- **Step Type:** TEST
- **Exploration level:** BALANCED

- **Hypothesis being tested:** A real temporary repository containing `main`, with no `origin` remote, gives
  `CONFLICTING`/`DIRTY` for a non-existent branch name under the current code. A real-git fixture can show this in under
  1 second per test without network access.
- **Learning target:** We learn whether real-repo fixtures in `tests/test_flow.py` are stable across hosts (identity,
  gpgsign, default branch). We also get a reusable pattern for future git-semantics tests, beyond the `run_git` mocks
  used so far.
- **Maximum acceptable cost for this learning:** 3 cost units. If the fixture is flaky after two adjustment attempts,
  stop and fall back to a minimal fixture (a single commit per branch).

### Intent & Git Integration

- **Step Intent:** Turn goal 4 into executable red tests before any production change.
- **Git branch:**
  `I-1792762866-mergeability-missing-ref-not-conflict/P-1792765112-claude-agent-claude-opus-5-5/E-{timestamp}-implement/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Rebase the execution branch from the plan branch.
2. In `apps/sandbox-executor/tests/test_flow.py`, add a new pytest class `TestMergeabilityRefResolution`. Do not use
   `unittest.TestCase`.
3. Add a small module-level helper that builds a repository under `tmp_path`:
   - Run `git init -b main` and commit `shared.txt` on `main`.
   - Create `clean-feature`, which adds `feature.txt` (no overlap with `main`).
   - Create `conflict-feature`, which edits `shared.txt` one way.
   - Then edit the same line of `shared.txt` differently on `main` and commit.
   - Pass `-c user.name=Holon Test -c user.email=test@holon.invalid -c commit.gpgsign=false` on each commit. Use
     `subprocess.run` with `check=True` and `capture_output=True`.
4. In each real-repo test, use `monkeypatch` or `patch("shutil.which", return_value=None)` so the `gh` branch can never
   run.
5. Add these tests:
   - **(a) Missing branch:** `check_pr_mergeability(repo, "does-not-exist", "main")` returns `merge_state_status`
     `MISSING_REF`, `mergeable` `UNKNOWN`, `is_dirty False`, and `diagnostics["missing_ref"]` contains `does-not-exist`.
   - **(b) Missing target:** target `no-such-target` returns `MISSING_REF` with `ref_role` `target`.
   - **(c) Genuine conflict:** `conflict-feature` returns `CONFLICTING`/`DIRTY` with `is_dirty True`.
   - **(d) Clean branch:** `clean-feature` returns `MERGEABLE`/`CLEAN`.
   - **(e) Review loop:** `run_review_loop` with `dry_run=True`, `target="does-not-exist"`, `max_iterations=3`, a real
     repository and `probe_ci_checks` patched to an all-passed result. It asserts:
     - no finding id starts with `FIND-DIRTY`
     - the consensus status is not `conflicted_dirty`
     - consensus is not approved
     - some `context.log` message contains `cannot evaluate mergeability for ref does-not-exist`
     - (capture the logs with a stub or by wrapping `context.log`)
   - **(f) Review stage:** `run_review_stage` with `check_pr_mergeability` patched to return a `MISSING_REF` dict.
     `result.error` must not contain `CI SIGNAL SUPPRESSED` and must contain `cannot evaluate mergeability for ref`.
6. Run `uv run pytest tests/test_flow.py -k MergeabilityRefResolution` from `apps/sandbox-executor`. Expected: (a), (b),
   (e) and (f) fail on current code. (c) and (d) pass, which confirms the fixture is sound.
7. Write test docstrings and comments in Australian English.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (without trustworthy red tests the fix cannot be validated; p_success 0.92 is above 0.6, so no
  sub-intent is required)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (test location; test changes declared in plan);
  `docs/safety.md` "Process sandbox" (only `git` and `pytest` subprocesses); intent goal 5 (`uv run pytest` only, no
  `PYTHONPATH`).
- **Potential failure modes for this step:**
  - The global git config forces signing or a different default branch.
  - `gh` is present and reaches the network.
  - The log capture does not see `context.log` output, because it may also write to a file.
- **Guardrails and early‑abort checks:**
  - Assert `shutil.which` is patched before calling the function under test.
  - Pass `-b main` explicitly.
  - If (c) or (d) fail on current code, the fixture is wrong. Fix the fixture before touching production code.

### Success & Discard Criteria

- **Success:** Six new tests exist. Exactly (a), (b), (e) and (f) fail on unmodified `flow.py`, and (c) and (d) pass.
  The rest of the suite is unaffected.
- **Discard:** The fixture is still non-deterministic after two attempts, or cost exceeds `1.5 * 3 = 4.5` units.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.2   |
| impact_pred         | 20.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.5   |
| ev_pred             | 16.79 |

### Step Metrics Rationale

The failure has already been reproduced by hand: `git merge-tree --write-tree nosuch main` exits 1 on 2.47.3. That makes
the red-test outcome highly predictable (`p = 0.92`). Entropy is low: a single test file is touched, nothing is
irreversible, and there is no production change (SSA ≈ 2, NOV ≈ 3). Learning value is moderate (3.5), because real-repo
fixtures are a new pattern for `test_flow.py`.
`EV = 0.92 * 20.0 + 0.5 * 3.5 - 0.3 * 1.2 - 3.0 = 18.40 + 1.75 - 0.36 - 3.0 = 16.79`.

---

## Step 2: Resolve both refs before merge simulation and tighten the merge-tree exit-code mapping

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is a contained change to one function (about 60 lines). Step 1's tests cover it directly, and it
  shares a branch and review cycle with Step 3. Splitting it out would only add merge overhead.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Deliver goals 1 and 2. `check_pr_mergeability()` returns `MISSING_REF` when a ref cannot be resolved
  and `CONFLICTING`/`DIRTY` only when `merge-tree` exits 1 after both refs have resolved.
- **Git branch:**
  `I-1792762866-mergeability-missing-ref-not-conflict/P-1792765112-claude-agent-claude-opus-5-5/E-{timestamp}-implement/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `flow.py` `check_pr_mergeability()`, keep the `gh` path as it is, apart from adding `diagnostics: {}` to its
   return dict.
2. In the local fallback, keep the existing substitution logic and record every candidate name in `candidates_tried`:
   - target: `origin/<target>`, falling back to `<target>`
   - branch: `HEAD`, or `<branch>`, falling back to `origin/<branch>`
3. Add a small private helper in `flow.py`: `_resolve_commit_ref(repo_dir: str, ref: str) -> tuple[str | None, str]`.
   - It runs `run_git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=repo_dir, check=False)`.
   - It returns the SHA (or `None`) together with the stripped stderr.
   - Give it a full docstring.
   - Keep `--quiet` optional: if the executor sees empty stderr, keep the plain `--verify` form so that stderr can be
     captured for diagnostics.
4. Resolve the branch ref first, then the target ref. On the first failure, return the `MISSING_REF` dict described in
   "Plan Description & Strategy" and log at `logger.info`:
   `Cannot evaluate mergeability for ref %s (%s): ref could not be resolved to a commit.`
5. Run `merge-tree --write-tree` on the two resolved SHAs:
   - exit 0 maps to `MERGEABLE`/`CLEAN`
   - exit 1 maps to `CONFLICTING`/`DIRTY`
   - any other exit maps to `UNKNOWN`/`UNKNOWN`, with `diagnostics` set to `merge_tree_exit_code` plus the stripped
     stderr
6. Delete the legacy `merge-base` plus 3-way `merge-tree` fallback, as goal 2 requires. Do not touch the separate legacy
   `merge-tree HEAD <target>` call in `check_and_resolve_stage_conflicts()`. It only runs after a genuine `DIRTY`.
7. In the `except` branch, set `diagnostics = {"error": str(exc)}`.
8. Update the docstring:
   - `merge_state_status` gains `MISSING_REF`.
   - Document the `diagnostics` key and its sub-keys.
   - Describe the resolution order and exit-code contract, in Australian English.
9. **Declared test change:** update
   `TestReviewMergeabilityGuards.test_check_pr_mergeability_local_git_fallback_with_remote_tracking_ref`.
   - Make its `rev-parse` side-effect recognise `^{commit}` (and `--quiet`) lookups for `origin/main` and
     `origin/feature-branch`, returning fake SHAs.
   - Update its `merge-tree` assertion to expect those SHAs.
   - Its intent stays unchanged: remote-tracking substitution still produces `MERGEABLE`.
10. Run `uv run pytest tests/test_flow.py` from `apps/sandbox-executor`. Step 1 tests (a)–(d) must now pass, and every
    `TestReviewMergeabilityGuards` test must pass.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (it is the core fix; p_success 0.88 is above 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` "Human review is the final gate" (genuine `DIRTY` must remain detected);
  `holon-config/world/ruleset.md` §2 (typing and docstrings); `holon-config/world/ruleset.md` §3 (declared test edit);
  intent goal 5 (no new dependencies, Australian English).
- **Potential failure modes for this step:**
  - The `^{commit}` brace escaping in an f-string is written incorrectly. `{{commit}}` is required.
  - Passing SHAs instead of names changes the `merge-tree` output text that downstream code parses. Mitigation: only the
    exit code is consumed here.
  - Other tests that mock `run_git` with a blanket `returncode=0` start returning a SHA such as `""`. An empty stdout
    with exit 0 must still count as resolved: fall back to the ref name when stdout is empty.
- **Guardrails and early‑abort checks:**
  - Run the full `tests/test_flow.py` after the change, not just the new class.
  - If more than two unrelated pre-existing tests break, stop and re-examine the helper's contract with mocks rather
    than editing those tests.

### Success & Discard Criteria

- **Success:**
  - Step 1 tests (a), (b), (c) and (d) pass.
  - All pre-existing `tests/test_flow.py` tests pass, with only the one declared mocked test edited.
  - `uv run ruff check` is clean for `flow.py`.
- **Discard:** Pre-existing tests need edits beyond the one declared test, or cost exceeds `1.5 * 5 = 7.5` units.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.8   |
| impact_pred         | 45.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 2.5   |
| ev_pred             | 35.31 |

### Step Metrics Rationale

The logic is simple and the git semantics were verified during planning. Missing refs exit 128 from
`rev-parse --verify <ref>^{commit}`. Trees fail the `^{commit}` peel. Unrelated histories exit 128 from `merge-tree`.
The main risk is mock-compatibility fallout in the existing `run_git`-mocked tests, hence `p = 0.88`. Entropy is 1.8:
SSA is low, the code is a hot review-path function (CL ≈ 3), and IRR is low. Impact is the highest of all steps, because
this removes the false-`DIRTY` root cause. `EV = 0.88 * 45.0 + 0.5 * 2.5 - 0.3 * 1.8 - 5.0 = 39.60 + 1.25 - 0.54 - 5.0`
`= 35.31`.

---

## Step 3: Report MISSING_REF distinctly in the review loop and review stage

- **Sub‑intent recommendation:** NO
- **Reasoning:** Two localised edits in the same file and branch as Step 2. The behaviour belongs with the new return
  contract, and Step 1's tests (e) and (f) cover it.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Deliver goal 3. When the result is `MISSING_REF`, there is no `FIND-DIRTY` and no
  `CI SIGNAL SUPPRESSED` narrative. The loop reports `cannot evaluate mergeability for ref <X>` and fails closed.
- **Git branch:**
  `I-1792762866-mergeability-missing-ref-not-conflict/P-1792765112-claude-agent-claude-opus-5-5/E-{timestamp}-implement/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `run_review_loop()`, after `check_pr_mergeability` returns:
   - Compute `missing_ref = last_mergeability.get("merge_state_status") == "MISSING_REF"`.
   - Compute `is_dirty` as before. It is already `False` for `MISSING_REF`.
2. If `missing_ref`:
   - Read the ref from `last_mergeability.get("diagnostics", {}).get("missing_ref")`, falling back to `review_target`.
   - Log
     `[Review Loop] cannot evaluate mergeability for ref <X> against <target_branch>: ref could not be resolved to a commit.`
   - Append the finding `FIND-REF-{iteration:03d}` and set severity to `important` (unless already `critical`). Never
     add `FIND-DIRTY`.
3. Keep the ledger action and finding records as they are (append-only `executions.jsonl`). The `error_context` string
   already includes the finding ids, and the message above is added to it.
4. After the record writes, if `missing_ref`, break out of the loop. Retrying cannot make a missing ref appear, so this
   avoids burning up to 25 iterations.
5. Leave `consensus_status` as it is: `missing_ref` cannot produce `approved`, and it is not dirty, so it remains
   `rejected`. Add a `mergeability_note` field to `consensus_data` that carries the message.
6. In `run_review_stage()`, after reading `mergeability`, add a branch between the `is_dirty` branch and the
   `if not consensus_reached` branch for `merge_status == "MISSING_REF"`. Its error message reads
   `Review halted: cannot evaluate mergeability for ref <X> against <target_branch> (mergeable=UNKNOWN, mergeStateStatus=MISSING_REF). Verify that the branch exists locally or on origin.`
   Return `StageStatus.FAILED` with the same payload shape as the existing branches.
7. Do not change the `is_dirty` branch. The existing test `test_review_stage_flags_dirty_merge_state` must still assert
   `CI SIGNAL SUPPRESSED` and `DIRTY`.
8. Run `uv run pytest tests/test_flow.py`. Step 1 tests (e) and (f) now pass, and so do
   `test_review_loop_emits_discrete_action_records` and the other review-loop tests.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES (lowest blocking p_success at 0.85, but still above 0.6, so no sub-intent is needed)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` Invariant 4 and Bean 0034 (`MISSING_REF` must never yield approval);
  `holon-config/world/constraints.md` §3 (ledger append-only; no new ledger files); intent goal 5 (Australian English in
  log strings).
- **Potential failure modes for this step:**
  - Breaking out of the loop early changes the iteration counts that existing tests assert on. Only a `MISSING_REF`
    triggers the break, and no existing test uses one.
  - A new finding-id prefix could confuse downstream trailer parsing.
  - The new `consensus_data` key could break a strict schema consumer.
- **Guardrails and early‑abort checks:**
  - Check `format_review_git_trailers` and `parse_commit_trailers` with a `FIND-REF-001` id in a quick assertion inside
    test (e).
  - If a strict schema validator rejects the new key, drop `mergeability_note` and rely on the existing `mergeability`
    payload. That payload already carries `diagnostics`.

### Success & Discard Criteria

- **Success:**
  - Tests (e) and (f) pass, and the existing dirty-path and review-loop tests pass unchanged.
  - No `FIND-DIRTY` is emitted for a missing ref.
  - The review stage error contains `cannot evaluate mergeability for ref`.
- **Discard:** The early break or the new finding id needs changes to more than one pre-existing assertion, or cost
  exceeds `1.5 * 4 = 6` units. If so, fall back to logging only (no `FIND-REF`, no break) and re-evaluate.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.6   |
| impact_pred         | 30.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 22.02 |

### Step Metrics Rationale

This step changes observable review semantics: a new finding id, an early loop exit and a new stage error. Several
existing tests and ledger consumers observe those semantics, so it is the bottleneck at `p = 0.85`. Entropy is 1.6:
behavioural surface is moderate and novelty is low. Impact is 30, because operators get the correct diagnosis instead of
a false `CRITICAL` conflict alarm. `EV = 0.85 * 30.0 + 0.5 * 2.0 - 0.3 * 1.6 - 4.0 = 25.50 + 1.00 - 0.48 - 4.0`
`= 22.02`.

---

## Step 4: Exploratory edge-case probes for the resolve-then-merge contract

- **Sub‑intent recommendation:** NO
- **Reasoning:** It is cheap, adds tests only, and does not block. A failure here produces knowledge, such as a
  candidate follow-up intent, rather than blocking the root intent.
- **Step Type:** EXPLORATION
- **Exploration level:** EXPLORATORY

- **Hypothesis being tested:** After both refs resolve to commits, `merge-tree --write-tree` exit 1 means only a real
  content conflict. Four inputs are tested:
  - unrelated histories (observed to exit 128) yield `UNKNOWN`
  - an annotated tag on a clean commit yields `MERGEABLE` (the `^{commit}` peel works)
  - a ref naming a tree object yields `MISSING_REF`
  - `branch=""` resolves to `HEAD` and behaves like the checked-out branch
- **Learning target:** Whether the exit-code contract holds beyond the three cases the intent names. Any surprise
  becomes a documented failure mode for the review-loop knowledge entries.
- **Maximum acceptable cost for this learning:** 2 cost units. Stop after the four probes, without iterating on
  surprises inside this branch.

### Intent & Git Integration

- **Step Intent:** Harden the new contract with real-git edge cases and record any deviations as follow-up intents
  rather than scope creep.
- **Git branch:**
  `I-1792762866-mergeability-missing-ref-not-conflict/P-1792765112-claude-agent-claude-opus-5-5/E-{timestamp}-implement/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Extend `TestMergeabilityRefResolution` with four tests, reusing the Step 1 fixture helper with `gh` patched out:
   - **(g) Unrelated histories:** create an orphan branch with its own commit and assert `UNKNOWN`/`UNKNOWN`,
     `is_dirty False` and a non-empty `diagnostics["stderr"]`.
   - **(h) Annotated tag:** create an annotated tag on `clean-feature` and assert `MERGEABLE`/`CLEAN`.
   - **(i) Tree ref:** pass the tree SHA of `main` as the branch and assert `MISSING_REF`.
   - **(j) Empty branch:** check out `clean-feature`, pass `branch=""` and assert `MERGEABLE`/`CLEAN`.
2. If a probe contradicts the hypothesis, do not widen production changes in this branch, except for a one-line fix in
   the Step 2 helper. Instead:
   - mark that test `pytest.mark.xfail(strict=True)` with an Australian-English reason
   - describe the deviation in the execution report
3. Run `uv run pytest tests/test_flow.py -k MergeabilityRefResolution`.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO (the root intent's acceptance criteria are met without it)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (declared test additions); `docs/safety.md` "Exploration
  guidelines" (failure is data); process-sandbox whitelist (`git` only).
- **Potential failure modes for this step:** On some git versions, orphan-branch creation or the tag commands need a
  user identity. Tree-SHA handling may differ.
- **Guardrails and early‑abort checks:** Use the same identity and `gpgsign` flags as Step 1. Abort the step once cost
  reaches 2 units.

### Success & Discard Criteria

- **Success:** All four probes pass, or each deviation is captured as a strict `xfail` with a recorded explanation.
- **Discard:** The probes need production changes beyond one helper line, or cost exceeds `1.5 * 2 = 3` units. If so,
  drop the remaining probes and record them as a follow-up suggestion.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.75  |
| entropy_pred        | 0.8   |
| impact_pred         | 8.0   |
| cost_pred           | 2.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 6.26  |

### Step Metrics Rationale

Planning already confirmed two of the four behaviours on git 2.47.3: unrelated histories exit 128, and a tree fails the
`^{commit}` peel. The annotated-tag and empty-branch cases are inferred, hence `p = 0.75`. Learning value is the highest
in the plan (5.0), because these probes turn implicit git behaviour into regression evidence and candidate KB failure
modes. Impact is small and entropy is minimal (tests only).
`EV = 0.75 * 8.0 + 0.5 * 5.0 - 0.3 * 0.8 - 2.0 = 6.00 + 2.50 - 0.24 - 2.0 = 6.26`.

---

## Step 5: Full quality gate, formatting, and ledger recording

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is a mandatory verification step: routine, low-risk and not reusable as a separate unit.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove that the whole change satisfies goal 5. Record the execution in the permitted ledger, then hand
  off for human review of the root intent.
- **Git branch:**
  `I-1792762866-mergeability-missing-ref-not-conflict/P-1792765112-claude-agent-claude-opus-5-5/E-{timestamp}-implement/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run these commands from `apps/sandbox-executor`:
   - `uv run pytest`, the full suite with no `PYTHONPATH` and no `unittest` invocations
   - `uv run ruff check`
   - `uv run ruff format --check`
2. If formatting fails, run `uv run ruff format` on the two touched files only, then re-check.
3. Grep the diff for American spellings in new comments, docstrings and log strings. Words to check: "behavior",
   "normalize", "recognize", "analyze", "initialize" and "color". Fix any hits in added lines only.
4. Format any markdown touched by the execution with the repository's Prettier configuration (`.prettierrc.json`).
   Format the execution report under `executions/` if one is written. Do not reformat unrelated files.
5. Append the execution outcome to `holon-knowledge/ledger/executions.jsonl` through the existing `atomic_ledger_append`
   tooling: predicted versus actual metrics, and the Step 4 probe findings. Append only. Create no other ledger files.
6. Commit with a message scoped to this intent, then rebase from the plan branch to check for divergence. Never push to
   or merge into `main`. The root intent merge requires human approval.

### Dependencies & Criticality

- **Depends on:** Steps 1, 2, 3 (and Step 4 if executed)
- **Is Bottleneck:** YES (acceptance criteria are defined by this gate; p_success 0.90)

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 5 (tooling, spelling, markdown, ledgers); `holon-config/world/constraints.md` §1
  (rebase discipline, no `main` commits) and §3 (append-only ledger); `docs/safety.md` Invariant 4 (human approval for
  the root merge).
- **Potential failure modes for this step:**
  - Unrelated pre-existing failures in the full suite, such as Docker-dependent integration tests in a process sandbox.
  - Ruff format drift in untouched code.
- **Guardrails and early‑abort checks:**
  - If failures occur only in tests that need Docker or the network, and they also fail on the parent branch, record
    them as pre-existing in the execution report instead of "fixing" them.
  - Never edit unrelated tests to make the gate green.

### Success & Discard Criteria

- **Success:**
  - The full `uv run pytest` passes, or the only failures are documented, pre-existing and environment-bound failures
    that are identical on the parent branch.
  - Ruff check and format check pass.
  - Exactly one appended execution record exists.
  - The branch is rebased cleanly.
- **Discard:** A new failure is caused by this change and cannot be fixed within `1.5 * 2 = 3` cost units. If so, mark
  the execution failed and leave the branch for review.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.4   |
| impact_pred         | 10.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 7.38  |

### Step Metrics Rationale

This is a routine gate with well-understood tooling. The residual risk is pre-existing environment-bound test failures
and formatting drift, hence `p = 0.90`. Entropy is minimal: no new behaviour, plus an append-only ledger write.
`EV = 0.90 * 10.0 + 0.5 * 1.0 - 0.3 * 0.4 - 2.0 = 9.00 + 0.50 - 0.12 - 2.0 = 7.38`.

---
