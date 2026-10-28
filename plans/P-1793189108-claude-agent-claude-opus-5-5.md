# Plan for I-1793188814-native-review-loop-real-ensemble-no-fabricated-approval--1-harden-the-native-stage-4-review-loop-to-fail-cl

- **Plan ID:** P-1793189108-claude-agent-claude-opus-5-5
- **Parent Intent ID:** I-1793188814-native-review-loop-real-ensemble-no-fabricated-approval/\_
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-28T12:05:09.606Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A. Scope is kept exactly as written, but the plan pins down three ambiguities
  the intent leaves open (see Step 3 and Step 4): (a) where the single failure action record goes when the clone never
  produced a ledger, (b) the exact meaning of "a failed consensus post fails the stage", and (c) which waits count as
  "on the review path".
- **Exploration stance:** conservative. This is fail-closed security hardening of an approval gate. Every change removes
  a path where the stage could report success without evidence. Exploring new behaviour has little value here;
  predictable, fully faked tests matter most.
- **Safety priority level:** elevated
- **Priority Justification:** The intent carries the `security` and `provenance` labels and touches the Stage 4 gate
  that feeds human merge approval (`docs/safety.md` "Human review is the final gate" and Invariant 4). A bug here could
  turn a review of the wrong commit into a fabricated approval. `holon-config/world/constraints.md` §4 requires a deep
  model for security-sensitive plans, and §3 (Ledger Immutability) limits ledger writes to appends through the existing
  locked appender. The level is not `critical` because nothing here merges, pushes to `main` or changes estimators, and
  every test uses fakes (no network, which §2 forbids anyway).

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** Code inspection already identified every defect the intent names, so no step needs to probe unknown
  behaviour:
  - `_find_git_root` never advances `cur` to `parent`, so it spins forever in any directory without `.git`.
  - The entrypoint ignores clone and checkout failures (`check=False`).
  - `run_review_loop` runs the empty-diff guard only under `auto_pr and shutil.which("gh")`.
  - A PR-number or PR-URL target reaches `_execution_effective_diff` as `"123"`, gets `"not an execution branch"` and
    passes the guard unchecked.
  - A failed post only writes an `ACT-POST` failure row and leaves `approved` true.
  - The pending-CI wait is a bare `time.sleep(15)` per iteration with no deadline.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.68  |
| entropy_pred        | 14.0  |
| impact_pred         | 85.0  |
| cost_pred           | 16.0  |
| learning_value_pred | 6.0   |
| ev_pred             | 40.6  |

### Strategy Rationale

Run it as one plan with no sub-intent list. The predicted entropy is 14.0, the sum of the step entropies, which is under
the 16.0 budget. The steps are tightly coupled: the entrypoint (Step 3) uses the resolver (Step 2), and the flow guard
(Step 4) uses the resolved head branch. Splitting them into child intents would mean three PRs that each leave the gate
half-hardened, and an intermediate state can be less safe than either end state. For example, if the resolved head
branch is cloned but the guard still skips PR targets, the gate looks verified when it isn't.

How the overall metrics were derived:

- **Entropy:** sum of the step entropies, `0.8 + 2.5 + 3.5 + 3.5 + 1.5 + 1.7 + 0.5 = 14.0`.
- **Cost:** sum of the step costs, `1.0 + 2.5 + 3.5 + 3.5 + 1.5 + 3.0 + 1.0 = 16.0`.
- **Impact:** a qualitative 85 rather than the sum (98). Step 6 (tests) and Step 7 (validation) mostly protect the value
  Steps 2 to 4 deliver, so adding their impact would double-count it.
- **p_success:** Steps 3 and 4 are the bottleneck (0.78 and 0.75). Their failures are correlated: both depend on how
  well the executor reads the 5,000-line `flow.py` and on existing `test_review_loop.py` fakes that hard-code current
  behaviour. A plain product of the step probabilities would be about 0.34, but that undercounts the executor's ability
  to repair a failing test within the same run. The qualitative estimate is 0.68.
- **Learning value:** 6.0, mostly from finding out how many existing tests assumed fail-open behaviour.
- **EV:** `EV = 0.68 * 85.0 + 0.5 * 6.0 - 0.3 * 14.0 - 16.0 = 57.8 + 3.0 - 4.2 - 16.0 = 40.6`, using `mu = 0.5` and
  `lambda = 0.3` from `holon-config/metrics/ev_config.json`.

Strong assumptions:

- `gh` and `git` are only ever reached through injectable runners or `subprocess.run`, which tests can monkeypatch. The
  existing tests already do this.
- `try_append_action_record` already takes `LedgerLock` internally (`ledger.py:411`), so "under LedgerLock" means using
  that function rather than adding a second lock. Taking `LedgerLock` again around the call would self-deadlock if the
  lock is not re-entrant, so the executor must not wrap it.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1: commits stay on this plan's execution branch, with no `main` checkout.
  - `holon-config/world/constraints.md` §2: no network in the sandbox, so every `gh` and `git` remote interaction is
    faked.
  - `holon-config/world/constraints.md` §3: ledger is append-only, and only through `try_append_action_record`.
  - `holon-config/world/constraints.md` §4: deep model routing for security-sensitive work.
  - `holon-config/world/ruleset.md` §2: typed public signatures and docstrings for the new module.
  - `holon-config/world/ruleset.md` §3: `pytest`, and tests in `tests/` mirroring the source. Test-file changes are
    declared here: a new `tests/test_review_target.py`, edits to `tests/test_review_loop.py`, and a new test in
    `tests/test_cli.py`.
  - `docs/safety.md` Bean 0034 / Invariant 4: the loop must never merge; it halts for a human on approval.
  - `docs/safety.md` "Failure containment": an execution failure fails the stage instead of silently succeeding.
- **Potential violations or edge cases:**
  - A clone failure leaves no repository, so no in-repo ledger exists for the failure record.
  - A PR head branch name is attacker-controlled data from GitHub (for example one starting with `-`, or containing
    `..`), which creates an option- or ref-injection risk when passed to `git clone --branch`.
  - Existing tests that assert fail-open behaviour (guard skipped under `--no-auto-pr`, exit 0 after a failed post) will
    break. Updating them changes test assertions, which ruleset §3 allows only when declared, and it is declared here.
  - Forked-PR heads (`headRepositoryOwner` differs from the base repository) cannot be cloned from `origin` by branch
    name.
- **Mitigations built into the plan:**
  - Validate head branch names with `git check-ref-format --branch` semantics plus an explicit leading-`-` refusal, and
    validate the head SHA as 40-hex.
  - Pass `--` before positional refs wherever git accepts it.
  - Verify `HEAD == headRefOid` after checkout. A force-push race or a forked head shows up as a SHA mismatch, which is
    treated as a hard failure.
  - Write the failure record through `try_append_action_record`, which reports instead of raising, so a missing ledger
    never masks the non-zero exit.
  - Every timeout and sleep is injectable, so tests never sleep for real.
- **Residual risk accepted (and why):** Fork PRs fail closed (SHA mismatch or clone failure) instead of being supported.
  That is the correct direction for a security gate, and fork support can be its own intent later. A clone failure in an
  empty workspace may lose its failure record if the ledger directory cannot be written. The non-zero exit and the
  stderr diagnostic still carry the failure, and no alternative ledger file is ever created (the `ledger.py` contract).
- **Allocated Entropy Budget:** 16.0
- **Predicted Plan Entropy:** 14.0
- **Budget Compliance:** The strategy fits within budget, with 2.0 of headroom kept for test-repair churn in Step 6.

## Plan Description & Strategy

The plan has seven ordered steps in `apps/sandbox-executor`. It starts with the isolated, low-risk fix (Step 1), adds a
pure resolver module with no I/O apart from an injectable runner (Step 2), then wires it into the containerised
entrypoint (Step 3) and the flow-level review loop (Step 4). It then bounds the remaining unbounded waits (Step 5),
consolidates fake-only tests (Step 6) and runs the full validation gate (Step 7).

Each implementation step adds its own focused tests as it goes. Step 6 covers the cross-cutting scenarios and repairs
the existing tests that encoded fail-open behaviour.

The invariant the whole plan enforces: **Stage 4 reports approval only for the exact commit it cloned and verified. Any
resolution, clone, checkout, verification, diff-guard or posting failure produces `approved = False`, a non-zero exit,
and at most one failure action row from the entrypoint.**

---

## Step 1: Fix the `_find_git_root()` busy loop in `cli.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** A three-line defect with an obvious fix and a deterministic test. A separate branch and pipeline would
  cost more than the change.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make `_find_git_root` terminate for every input by advancing up the directory tree.
- **Git branch:** this plan's execution branch (`.../P-1793189108-claude-agent-claude-opus-5-5/E-{ts}-execute/_`)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/cli.py` (around line 816), set `cur = parent` at the end of the inner
   loop body. The loop then walks up until it reaches the filesystem root (`parent == cur`) and breaks.
2. Bound the walk defensively with a maximum depth (for example 256 levels) so a pathological symlink layout can never
   spin, and keep the existing fallback return value.
3. Keep the public signature and docstring. Extend the docstring to state that the function terminates and has no side
   effects.
4. Add a test in `tests/test_cli.py`:
   - Call `_find_git_root` on a `tmp_path` subdirectory with no `.git`, with `os.getcwd` and the module-file base
     monkeypatched to point at that tree, and run it in a daemon thread joined with a 5-second timeout. Assert the
     thread finished and returned the fallback.
   - Add a positive case with a `.git` directory two levels up.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (declared test addition); `docs/safety.md` Failure containment.
- **Potential failure modes for this step:** The test thread hangs if the fix is wrong. Because it is a daemon thread
  with a join timeout, the test fails instead of the suite hanging.
- **Guardrails and early‑abort checks:** Run only this test first. If it does not finish within 5 seconds, abort the
  step and re-read the loop.

### Success & Discard Criteria

- **Success:** The new tests pass in under 1 second, and existing callers (`test_token_reduction.py`,
  `test_local_llm.py`) stay green.
- **Discard:** Never discard: this is a required correctness fix. Escalate if more than 2 attempts fail.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.8   |
| impact_pred         | 12.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 10.66 |

### Step Metrics Rationale

The defect and fix are fully understood, which gives the high p_success. Impact is moderate: today any token-reduce
review run started outside a git tree hangs forever.
`EV = 0.95 * 12.0 + 0.5 * 1.0 - 0.3 * 0.8 - 1.0 = 11.4 + 0.5 - 0.24 - 1.0 = 10.66`.

---

## Step 2: Add the `review_target` resolver module

- **Sub‑intent recommendation:** NO
- **Reasoning:** A new, self-contained, pure module with an injectable command runner. It is reusable, but small enough
  to land with its consumers, and splitting it off would leave it with no caller.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Turn a raw review target (branch name, PR number, or PR URL) into a verified, typed `ReviewTarget`,
  or raise a single typed error.
- **Git branch:** this plan's execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `apps/sandbox-executor/src/sandbox_executor/review_target.py`, with a module docstring describing the
   fail-closed contract.
2. Define the types:
   - A frozen dataclass `ReviewTarget` with fields:
     - `raw`
     - `kind`: a literal, `"branch"` or `"pr"`
     - `pr_number` (int or None)
     - `head_branch`
     - `head_sha` (str or None for branch targets until verified)
     - `title`
     - `body`
   - A `ReviewTargetError(RuntimeError)` carrying a short `reason` code: `parse`, `gh_missing`, `gh_failed`, `timeout`,
     `bad_json`, `missing_field`, `invalid_branch` or `invalid_sha`.
3. Add `parse_review_target(raw)`, which classifies the input:
   - Pure digits (no sign, at least 1) are a PR number.
   - `https://github.com/<owner>/<repo>/pull/<n>` (optionally with a trailing `/files` or similar, query or fragment) is
     a PR URL. Accept only the `https` scheme and only `github.com`, or the host from an optional `GH_HOST` env value.
   - Anything else is a branch name.
   - Reject empty input, whitespace and a leading `-`.
4. Add `resolve_review_target(raw, *, cwd, env=None, runner=subprocess.run, timeout=60.0)`:
   - For a PR, call the runner with
     `["gh", "pr", "view", <number-or-url>, "--json", "number,headRefName,headRefOid,title,body"]`, with
     `capture_output`, `text`, `check=False` and `timeout`.
   - Map `FileNotFoundError` to `gh_missing` and `subprocess.TimeoutExpired` to `timeout`, a non-zero return code to
     `gh_failed` (with redacted, bounded stderr), JSON errors to `bad_json`, and an absent or empty field to
     `missing_field`.
   - Check that the returned `number` equals the parsed number, so a URL from another repository resolved by `gh` in
     this repository is caught.
   - For a branch target, return a `ReviewTarget` with `head_sha=None` (verified later at checkout).
5. Add `validate_branch_name(name)`:
   - Refuse a leading `-`, `..`, `@{`, control characters, spaces, `~^:?*[\`, a trailing `/` or `.lock`, and empty path
     components.
   - Return the name unchanged on success.
   - Apply it to the resolved `headRefName` and to raw branch targets.
6. Add `validate_sha(sha)`: lowercase 40-hex only (allow 64-hex for SHA-256 repositories).
7. Route all diagnostic strings through `redact_agent_secrets` before they enter the exception message.
8. Add `tests/test_review_target.py` with fake runners (callables recording argv and returning a `CompletedProcess`)
   for:
   - every parse form
   - success
   - each error reason
   - number mismatch
   - malicious branch names (`-uexploit`, `a..b`, `x y`)
   - a non-hex SHA
   - checking that the exact argv contains the required `--json` field list

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO (Steps 3 and 4 depend on it, but it is low risk)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (typing, docstrings); `holon-config/world/constraints.md` §2
  (no real `gh` in tests); intent label `security`.
- **Potential failure modes for this step:**
  - The URL regex is too permissive, for example accepting `http://` or other hosts.
  - The branch validation is too strict and rejects legitimate Holon branch names containing `/` and `_`.
- **Guardrails and early‑abort checks:** Include real Holon branch names from `holon-knowledge/ledger/intents.jsonl`
  (for example the current branch with `/P-.../_`) as positive test fixtures.

### Success & Discard Criteria

- **Success:**
  - The module imports with no side effects.
  - `tests/test_review_target.py` covers 100% of its branches and passes with no subprocess spawned (assert the fake
    runner is the only callee).
  - Ruff is clean.
- **Discard:** If cost reaches more than 1.5 times `cost_pred`, ship the minimal resolver (number and URL only) and
  record the validation hardening as a follow-up.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 2.5   |
| impact_pred         | 18.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 13.59 |

### Step Metrics Rationale

This is a new file with a clear contract and no coupling, so it carries low entropy. The impact is high because it is
the provenance anchor: the review then binds to `headRefOid`.
`EV = 0.88 * 18.0 + 0.5 * 2.0 - 0.3 * 2.5 - 2.5 = 15.84 + 1.0 - 0.75 - 2.5 = 13.59`.

---

## Step 3: Clone and verify only the resolved head in `entrypoint/review_loop.py`, failing hard

- **Sub‑intent recommendation:** NO
- **Reasoning:** This step is the core of the intent and tightly coupled to Steps 2 and 4. It is the riskiest step
  (bottleneck), but a sub-intent would not lower that risk; it would only add merge churn.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:**
  - The container entrypoint reviews exactly the resolved head branch at exactly the resolved head SHA.
  - Any resolution, clone, fetch, checkout or SHA-verification failure ends the stage immediately with no consensus, a
    non-zero exit, and exactly one failure action record.
- **Git branch:** this plan's execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Factor the workspace preparation out of `main()` into a typed helper,
   `prepare_review_workspace(target: ReviewTarget, repo_dir, repo_url, run) -> str`, which returns the verified HEAD
   SHA. `run` is injectable and defaults to a non-exiting variant of `run_cmd`.
2. Resolve before cloning:
   - Call `resolve_review_target(args.target, cwd=repo_dir if it is a git dir else a temp/cwd, env=...)`.
   - When there is no repository yet, `gh pr view` needs a repository context. Pass `--repo <owner/repo>`, derived from
     `get_repo_url()` with credentials stripped, through an optional `repo` argument on the resolver. Note this argument
     in Step 2's signature.
3. On a fresh workspace, clone with `git clone --single-branch --branch <head_branch> -- <repo_url> .`, using
   `check=False` and an inspected return code.
4. On an existing workspace:
   - Run `git fetch --no-tags origin +refs/heads/<head_branch>:refs/remotes/origin/<head_branch>`.
   - Run `git checkout -B <head_branch> refs/remotes/origin/<head_branch>`.
   - Inspect the return code of each.
5. Verify the checkout:
   - Run `git rev-parse HEAD`.
   - For PR targets, require exact equality with `head_sha`.
   - For branch targets, require equality with `git rev-parse refs/remotes/origin/<head_branch>` and store that as
     `head_sha`.
   - A mismatch is a hard failure with reason `head_sha_mismatch`.
6. Hard-failure path: implement a single function,
   `_fail_stage(reason, detail, target, repo_dir, append=try_append_action_record)`, that:
   - builds one action record with:
     - `action_id = generate_action_id("ACT-REVIEW-TARGET")`
     - `entry_type = "action"`
     - `action_origin = "pr_review"`
     - `outcome = "failure"`
     - `category = "code"`
     - `target` = the raw target
     - `error_context` = the redacted, bounded detail
     - `created_at` (UTC)
   - calls the append callable once with the ledger path `<repo_dir>/holon-knowledge/ledger/executions.jsonl`. The
     callable itself takes `LedgerLock`; do not wrap it in a second lock. If the ledger directory does not exist (clone
     failed), still make exactly one call: it reports the failure and returns None, and no alternative ledger file is
     created.
   - prints a redacted failure banner (and a JSON object with `consensus.approved=false` and an `error` field when
     `--json` is set).
   - calls `sys.exit(2)`, distinct from exit 1 for "review ran, rejected", so operators can tell infrastructure failures
     apart.
7. Never call `run_review_loop` after a failure. Set `FlowContext.execution_branch = target.head_branch` and pass
   `target=` as the PR number for PR targets (so `gh pr checks` and posting address the PR), or the branch for branch
   targets.
8. Pass the resolved `ReviewTarget` to `run_review_loop` through a new optional keyword `resolved_target` (Step 4), so
   the flow uses `head_branch` for the diff guard and `head_sha` as `target_commit` instead of re-resolving.
9. Match intent and plan ledger metadata against `head_branch` instead of the raw target, so PR-number targets find
   their intent row.
10. Keep the git identity configuration and conflict check unchanged, but run them only after verification succeeds.
11. Write the tests in `tests/test_review_loop.py`, faking `resolve_review_target`, `run_cmd`/`run`, `get_repo_url`,
    `run_review_loop` and the appender:
    - resolution error leads to exit 2 and one append call, with `run_review_loop` not called
    - clone failure behaves the same way
    - checkout failure on an existing workspace behaves the same way
    - SHA mismatch behaves the same way
    - a successful PR-number target makes `run_review_loop` receive the PR number, the head branch and the head SHA
    - a successful PR-URL target behaves the same way
    - the clone argv contains `--single-branch --branch <head_branch>` and never the raw PR number
    - the existing token-leak tests stay green

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES (p 0.78, above 0.6, so a sub-intent is not mandated)

### Safety & Constraint Considerations

- **Relevant rules:**
  - `holon-config/world/constraints.md` §3 (append-only via `try_append_action_record`)
  - `docs/safety.md` Invariant 4 (no approval without review)
  - the existing redaction contract on `run_cmd` (token in clone URL)
- **Potential failure modes for this step:**
  - `gh pr view` without a repository context fails in a fresh container. Mitigated by `--repo`.
  - Changing the exit code breaks host-side callers that treat non-zero as one outcome. Check `cli.py` dispatch: any
    non-zero is already a failure.
  - Clone URL tokens leak through new error paths. Mitigated by routing everything through the existing redactors and
    keeping the leak tests.
- **Guardrails and early‑abort checks:**
  - Before editing, grep `cli.py` and `flow.py` for consumers of the entrypoint exit code.
  - If any caller distinguishes 1 from other values, use exit 1 instead of 2 and record that decision in the execution
    notes.

### Success & Discard Criteria

- **Success:** All new entrypoint tests pass with zero real subprocesses. There is exactly one append call per failure
  path (asserted with a call counter), and no failure path reaches `run_review_loop`.
- **Discard:** If verifying the SHA for branch targets breaks more than 5 existing tests in ways that reveal a design
  conflict (not just stale fakes), keep strict verification for PR targets only, document branch-target verification as
  a follow-up sub-intent, and continue.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.78  |
| entropy_pred        | 3.5   |
| impact_pred         | 22.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 2.5   |
| ev_pred             | 13.86 |

### Step Metrics Rationale

This step changes the control flow of a security entrypoint and its exit semantics. The repository-context subtlety for
`gh` in a fresh clone is the main uncertainty. Impact is the highest of any step because it closes the "review the wrong
commit" hole. `EV = 0.78 * 22.0 + 0.5 * 2.5 - 0.3 * 3.5 - 3.5 = 17.16 + 1.25 - 1.05 - 3.5 = 13.86`.

---

## Step 4: Make the flow guard unconditional and a failed consensus post fail the stage

- **Sub‑intent recommendation:** NO
- **Reasoning:** Edits inside `run_review_loop` in `flow.py` that are coupled to Step 3's resolved target. The risk
  comes from existing tests that assume fail-open behaviour, which is a test-repair cost, not a design risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:**
  - The empty-diff and diff-unverified guard runs on every target (branch, PR number or PR URL, with or without
    `--auto-pr` or `gh` on PATH).
  - A posting failure turns the stage outcome into failure.
- **Git branch:** this plan's execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add `resolved_target: ReviewTarget | None = None` to `run_review_loop`. Compute `guard_branch` as
   `resolved_target.head_branch` when given. Otherwise, for digit or URL targets, call `resolve_review_target`; a
   resolution failure becomes a hard refusal with `status="failed"` and `error`. Otherwise use `review_target` as the
   guard branch.
2. Move the empty-diff guard out of the `if auto_pr and not effective_dry_run and shutil.which("gh")` condition:
   - Run `_execution_effective_diff(repo_dir, guard_branch)` unconditionally, except in dry runs, where it still runs
     but only logs.
   - `has_diff is False` keeps the existing early return, now reached regardless of `auto_pr`.
   - `has_diff is None` sets `pr_refusal_error` and `pr_allowed=False` regardless of `auto_pr`, so the loop halts
     without consensus.
   - Rename internal wording where needed ("refused the review" rather than "refused the PR"), keeping
     `EMPTY_DIFF_FAILURE_REASON` and the `diff_unverified` reason strings stable for ledger consumers.
3. Use `resolved_target.head_sha` (when present) as the `target_commit` passed into `consensus_data`. Keep
   `_resolve_review_commit` as the fallback only when no resolved target exists.
4. Posting failure fails the stage:
   - After the existing `ACT-POST` row is recorded, if `not posted` and `consensus_reached`:
     - set `consensus_reached = False`
     - set `consensus_status = "post_failed"`
     - append `"consensus post failed"` to `halt_reasons`
     - log a fail-closed message
   - Recompute the dependent fields (`halted_for_human`, `bean_0034_enforced`, the summary row `status`, the outcome and
     verdict trailers, and `consensus.approved`/`score` in the return value) after this point. Move the posting block so
     it runs before those fields are derived, or recompute them; prefer moving the derivation below the posting.
   - A rejected-consensus post that fails also marks `posting.failed=True` and leaves the outcome as failure.
5. The dry run keeps its current behaviour: no posting, and a not-measured, unapproved consensus.
6. Confirm the entrypoint's `sys.exit(0 if approved else 1)` now yields 1 for `post_failed`. Add a test.
7. Write the tests in `tests/test_review_loop.py` (existing `TestReviewLoopEmptyDiffGuard*` classes extended):
   - The guard runs with `auto_pr=False`.
   - The guard runs when `shutil.which("gh")` returns None.
   - A PR-number target with a fake resolver that returns an execution head branch with an empty diff is refused with no
     consensus.
   - A PR-URL target behaves the same way.
   - An unverifiable diff for a PR target halts with no consensus.
   - A fake `post_pr_review_consensus` that returns `{"error": ...}` or raises gives `approved` False, status
     `post_failed`, a summary row status of `failed`, and exactly one `ACT-POST` failure row.

### Dependencies & Criticality

- **Depends on:** Step 2 (and Step 3 for the end-to-end wiring)
- **Is Bottleneck:** YES (p 0.75, above 0.6)

### Safety & Constraint Considerations

- **Relevant rules:**
  - `docs/safety.md` Bean 0034 / Invariant 4
  - `holon-config/world/ruleset.md` §3: modified assertions are declared. These are existing tests asserting guard skip
    under `--no-auto-pr` or approval after a failed post.
  - ledger schema stability (`docs/ledger_schema.md`)
- **Potential failure modes for this step:**
  - Reordering the posting and derivation code changes a ledger row shape. Mitigated by `preserve_shape=True` and a
    schema test.
  - The guard now fetches in runs that previously skipped it. It is faked in tests; in production it is the intended
    behaviour.
  - A new status string, `post_failed`, is unknown to downstream readers.
- **Guardrails and early‑abort checks:**
  - Grep for consumers of `consensus.status` values (`flow.py` stage runner, `cli.py` summaries).
  - If any consumer whitelists statuses, add `post_failed` there or fall back to `rejected` with a `posting_failed`
    flag.

### Success & Discard Criteria

- **Success:**
  - The new guard and posting tests pass.
  - The full `test_review_loop.py` and `test_flow.py` suites are green.
  - No code path returns `approved=True` when `posted` is false (asserted).
- **Discard:** If moving the derivation below the posting causes more than 1.5 times `cost_pred` in test churn, use the
  minimal variant: override `approved`, `status` and the summary status after posting, without restructuring.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.75  |
| entropy_pred        | 3.5   |
| impact_pred         | 22.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 2.5   |
| ev_pred             | 13.20 |

### Step Metrics Rationale

`run_review_loop` is about 500 lines with order-dependent derived fields, and existing tests encode the current order.
That is the main source of risk. The impact is high because this step closes both the PR-number guard bypass and the
"approved but never posted" fabrication path.
`EV = 0.75 * 22.0 + 0.5 * 2.5 - 0.3 * 3.5 - 3.5 = 16.5 + 1.25 - 1.05 - 3.5 = 13.20`.

---

## Step 5: Bound every wait on the review path with a deadline and an injectable sleep

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mechanical. The existing `_wait_for_ci_registration` already shows the pattern to copy.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** No wait reachable from Stage 4 can block without a monotonic deadline, and every sleep is injectable
  for tests.
- **Git branch:** this plan's execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Inventory the waits:
   - Grep `flow.py`, `entrypoint/review_loop.py` and `review_target.py` for `while True`, `while `, `time.sleep` and
     `subprocess.run` calls without `timeout`, limited to functions reachable from `run_review_loop` and the entrypoint.
   - Known items:
     - the pending-CI `time.sleep(15)` in the iteration loop
     - the mergeability retry `time.sleep(2)`, which is bounded by attempts but has no deadline
     - `subprocess.run` calls for `gh pr checks`, `gh pr view`, `gh pr comment` and `gh pr review` that lack `timeout=`
     - `_wait_for_ci_registration`, which already has a deadline
     - `cli._wait_for_proxy`, which already has a deadline
2. Add a review-loop deadline: `review_deadline_seconds`, resolved from
   `load_budgets(os.environ).stage_seconds("review")` when available, else from a `HOLON_REVIEW_DEADLINE_SECONDS`
   default (for example 3600). Add a `sleep` parameter (default `time.sleep`) next to the existing `clock` parameter of
   `run_review_loop`.
3. Replace the pending-CI `time.sleep(15)` with a sleep of `min(15, remaining)` using the injected sleep and clock. When
   the deadline has passed, stop iterating, log "review deadline exceeded" and fail closed (no consensus, `halt_reasons`
   includes the deadline).
4. Give every review-path `gh` and `git` subprocess call an explicit `timeout=` (60 seconds for `gh`, 300 seconds for
   clone/fetch). Treat `TimeoutExpired` as that call's existing failure path, which is now fail-closed after Step 4.
5. Thread the injectable sleep into the mergeability retry helper (default `time.sleep`).
6. Write tests with a fake clock and a recording fake sleep:
   - Pending CI that never resolves stops at the deadline without consensus, the total fake sleep never exceeds the
     deadline, and no real sleep happens (assert that `time.sleep` is not called, by patching it to raise).
   - `TimeoutExpired` from a fake `gh` produces no approval.

### Dependencies & Criticality

- **Depends on:** Step 4 (same function; avoids merge conflicts within the step sequence)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` "no stage before the merge waits for a human"; `docs/budgets.md` stage budgets.
- **Potential failure modes for this step:**
  - A deadline that is too short in production cuts real CI waits.
  - A missing `stage_seconds("review")` key raises an error.
- **Guardrails and early‑abort checks:**
  - Read `budgets.py` before choosing the key. If there is no review stage budget, use the env default and do not add a
    new budget key (that would be a config change outside scope).

### Success & Discard Criteria

- **Success:**
  - The grep inventory shows no unbounded `while` loop and no `subprocess.run` without `timeout` on the review path.
  - The deadline tests pass in under 1 second of wall time.
- **Discard:** If a call site is shared with non-review stages and adding a timeout breaks their tests, limit the change
  to review-only wrappers and list the shared site as residual.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.5   |
| impact_pred         | 8.0   |
| cost_pred           | 1.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 5.59  |

### Step Metrics Rationale

The pattern is already proven in the same file, which keeps the step low risk. Impact is moderate: it is liveness
hardening, not provenance. `EV = 0.88 * 8.0 + 0.5 * 1.0 - 0.3 * 1.5 - 1.5 = 7.04 + 0.5 - 0.45 - 1.5 = 5.59`.

---

## Step 6: Consolidate fake-only pytest coverage and repair fail-open assertions

- **Sub‑intent recommendation:** NO
- **Reasoning:** The tests belong with the code they protect, and splitting them off would leave the hardening
  unverified on the branch.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The suite proves the fail-closed invariant end to end, using fakes only, and contains no assertion
  that encodes the old fail-open behaviour.
- **Git branch:** this plan's execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run the whole `apps/sandbox-executor/tests` suite and list every failure caused by Steps 1 to 5.
2. Classify each failure as one of:
   - (a) a stale fake (update the fake)
   - (b) an assertion of fail-open behaviour (rewrite it to assert fail-closed, and list each one in the execution notes
     because ruleset §3 requires declared assertion changes)
   - (c) a real regression (fix the code)
3. Add one parametrised end-to-end test that drives the entrypoint `main()` with `sys.argv` for a branch, a PR number
   and a PR URL. The resolver, git runner, `run_review_loop` internals (`_execution_effective_diff`, `probe_ci_checks`,
   `post_pr_review_consensus`) and the appender are all faked. Across the success and failure matrix, assert that the
   exit code is 0 only when the SHA was verified, the diff guard passed and the posting succeeded.
4. Use the existing `hermetic_fixtures.py` and `gh_write_guard.py` helpers so any accidental real `gh` write or network
   call fails the test.
5. Make sure no test uses a real `time.sleep` longer than 0.1 seconds, or real `git clone` against a remote. Local
   `git init` in `tmp_path` is acceptable where existing tests already do it.

### Dependencies & Criticality

- **Depends on:** Steps 1 to 5
- **Is Bottleneck:** NO (its cost is absorbed into Steps 3 and 4 if they slip)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (declared test changes);
  `apps/sandbox-executor/docs/hermetic_testing.md`; `holon-config/world/constraints.md` §2 (no network).
- **Potential failure modes for this step:**
  - Weakening an assertion just to get green.
  - Fakes that bypass the code under test, giving false confidence.
- **Guardrails and early‑abort checks:**
  - Every rewritten assertion must assert a stricter outcome (fail-closed), never a looser one.
  - Reviewers can check this through the list in the execution notes.

### Success & Discard Criteria

- **Success:**
  - The full suite is green.
  - The new and changed tests are listed.
  - `gh_write_guard` reports zero real `gh` invocations.
  - The suite's wall time grows by less than 10 seconds.
- **Discard:** If more than 15 pre-existing tests need assertion changes, stop. The intent's fail-closed semantics then
  conflict with documented behaviour, so escalate to the parent intent for a decision instead of rewriting en masse.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 1.7   |
| impact_pred         | 12.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 7.08  |

### Step Metrics Rationale

The cost is relatively high because of test-repair churn in a 496-line test file and `test_flow.py`. The learning value
comes from quantifying how much fail-open behaviour was encoded in tests.
`EV = 0.82 * 12.0 + 0.5 * 1.5 - 0.3 * 1.7 - 3.0 = 9.84 + 0.75 - 0.51 - 3.0 = 7.08`.

---

## Step 7: Run the validation gate

- **Sub‑intent recommendation:** NO
- **Reasoning:** A routine gate with no design content.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Confirm that formatting, lint, typing and tests pass, and that the pre-commit check accepts the
  branch.
- **Git branch:** this plan's execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run `uv run pytest apps/sandbox-executor/tests -q`, using the Makefile target if one exists.
2. Run ruff format and check (the repository config) on the changed files.
3. Run `python scripts/pre_commit_check.py` (or the Makefile equivalent).
4. Commit on the execution branch with a message summarising the fail-closed invariant, and append the execution record
   through the normal Stage 3 flow (append-only).

### Dependencies & Criticality

- **Depends on:** Step 6
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §1 (branch discipline) and §3 (ledger append-only).
- **Potential failure modes for this step:** Lint failures in long `flow.py` lines; a pre-commit check rejecting the
  formatting of the new module.
- **Guardrails and early‑abort checks:** Do not push to or check out `main`, and do not edit existing ledger lines.

### Success & Discard Criteria

- **Success:** Every check exits 0, and the commit is on the execution branch.
- **Discard:** N/A. Fix and re-run, at most 3 iterations.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.5   |
| impact_pred         | 4.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 2.70  |

### Step Metrics Rationale

This is a routine gate. `EV = 0.90 * 4.0 + 0.5 * 0.5 - 0.3 * 0.5 - 1.0 = 3.6 + 0.25 - 0.15 - 1.0 = 2.70`.

---

## Sub-intent list

- None. The plan fits the 16.0 entropy budget (predicted 14.0) and runs as a single plan.
