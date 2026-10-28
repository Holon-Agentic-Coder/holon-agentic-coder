# Plan for I-1793189433-review-loop-fail-closed-target-and-guards

- **Plan ID:** P-1793189449-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-28T12:10:49.622Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A. Two clarifications are carried into the steps, not a reframe: (a)
  `verify_review_diff` takes an extra keyword `base_branch` (default `main`, fed from the intent's `target_branch`)
  because the `gh pr view` field list fixed by the intent does not include `baseRefName`; (b) the container entrypoint
  resolves a PR reference before any clone, so its `run_gh` seam supplies the repository through the `GH_REPO`
  environment variable derived from `HOLON_REPO_URL` (credentials stripped), since an empty workspace gives `gh` no
  repository context.
- **Exploration stance:** balanced, leaning exploit. Code reading already shows that `_find_git_root()` in `cli.py`
  never assigns `cur = parent`, so any start directory without a `.git` directly inside it spins forever (99% CPU, no
  child process, exactly the observed symptom). One short confirm-first test is the only exploratory step; the rest is
  well-scoped hardening of existing code paths.
- **Safety priority level:** elevated
- **Priority Justification:** The defect lets Stage 4 post and report an approval for work it never reviewed, which
  undermines the human review gate that `docs/safety.md` ("Human review is the final gate", parent intent merge to
  `main`) relies on. The change touches `gh` write paths (review posting), so the plan must also prove that no
  `gh pr merge` or auto-merge is ever reached. The ledger is append-only (`holon-config/world/constraints.md` §3), and
  the new failure rows must only ever append.

## Exploration

- **Proportion of steps that are exploratory:** 0.125
- **Justification:** One of eight steps (Step 1) first confirms the hang hypothesis with a deadline-guarded pytest
  before fixing it. The suspect is already strongly supported by reading the code, so more exploration would add cost
  with little learning; the fallback (bound every loop defensively, record the root cause as unconfirmed) is cheap.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.78  |
| entropy_pred        | 9.0   |
| impact_pred         | 85.0  |
| cost_pred           | 20.5  |
| learning_value_pred | 5.0   |
| ev_pred             | 45.6  |

---

## Step 1: Confirm and fix the `_find_git_root()` infinite loop

- **Sub‑intent recommendation:** NO
- **Reasoning:** A one-line fix with a single targeted test; small, low risk and on the critical path for the observed
  21-minute hang, so it belongs in the single execution branch.
- **Step Type:** EXPLORATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The host-side 99% CPU hang with no child process is `_find_git_root()` in
  `apps/sandbox-executor/src/sandbox_executor/cli.py`: its inner `while True` computes `parent` but never assigns
  `cur = parent`, so any base directory that does not itself contain `.git` loops forever. It is reached through
  `setup_token_reduction_proxy()` (and therefore `holon review-loop --token-reduce` and the Stage 4 proxy setup).
- **Learning target:** Whether the observed hang is fully explained by this loop, or whether another unbounded loop must
  be hunted (the fallback in Step 5).
- **Maximum acceptable cost for this learning:** About 15 minutes of execution time; past that, apply the defensive
  bounds and record the root cause as unconfirmed in the execution notes.

### Intent & Git Integration

- **Step Intent:** Prove the hang with a deadline-guarded test, then fix the traversal so it always terminates.
- **Git branch:**
  `I-1793189433-review-loop-fail-closed-target-and-guards/P-1793189449-claude-agent-claude-opus-5-5/E-{timestamp}-fail-closed/_`
  (single execution branch shared by all steps)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_cli.py` add a test that builds a nested `tmp_path` directory with no `.git`
   anywhere beneath the temporary root, patches `os.getcwd` to that directory and patches the module-level
   `os.path.exists` lookup for `.git` (or monkeypatches the `__file__` fallback) so no real ancestor `.git` can satisfy
   the search; this keeps the test hermetic even when pytest runs inside the repository checkout.
2. Run `_find_git_root(start_dir=<nested dir>)` in a daemon `threading.Thread`, `join(timeout=5)`, and assert the thread
   is no longer alive (deadline guard; no new dependency, no `pytest-timeout`). Also assert the returned path is the
   absolute start directory (the documented fallback). Add a second case where `.git` sits two levels above the start
   directory and assert that ancestor is returned.
3. Run only this test first with `uv run pytest apps/sandbox-executor/tests/test_cli.py -k git_root` from the repository
   root and record that it fails (thread still alive) before the fix: this is the confirmation.
4. Fix: assign `cur = parent` after the `parent == cur` check. Keep the docstring accurate and in Australian English.
5. Re-run the test and record the result in the execution notes ("root cause confirmed" or "unconfirmed").

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO (if refuted, Step 5 still bounds every loop defensively)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (pytest only, test changes declared here); `docs/safety.md`
  (sandboxing: no stray processes).
- **Potential failure modes for this step:** the test passes accidentally because a real ancestor `.git` exists
  (repository checkout or `/`); a hung daemon thread lingers in the xdist worker.
- **Guardrails and early‑abort checks:** isolate `.git` lookups to `tmp_path`; use a daemon thread so a regression fails
  the test instead of hanging the suite; abort the confirmation after about 15 minutes.

### Success & Discard Criteria

- **Success:** the new test fails before the fix (thread alive after the deadline) and passes after it; the
  ancestor-found case returns the right directory.
- **Discard:** if the test cannot reproduce the hang within about 15 minutes, keep the fix (it is still a real bug),
  mark the root cause as unconfirmed and proceed to Step 5's defensive bounds.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.6   |
| impact_pred         | 20.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 19.22 |

### Step Metrics Rationale

The bug is visible by reading the code, so success is very likely; entropy is tiny (one line plus a test). Learning
value is the highest of all steps because it settles the root cause of the observed hang.
`EV = 0.92 * 20.0 + 0.5 * 6.0 - 0.3 * 0.6 - 2.0 = 18.40 + 3.00 - 0.18 - 2.0 = 19.22`

---

## Step 2: Add `review_target.py` with target resolution and the diff verdict

- **Sub‑intent recommendation:** NO
- **Reasoning:** A new, small, pure module with injectable seams; it is the foundation of Steps 3 and 4 and is cheaper
  to deliver in the same branch than to coordinate across branches.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Provide one fail-closed place that turns a raw review target (branch, PR number or PR URL) into a
  verified head branch and SHA, and classifies the reviewable diff as `verified`, `empty` or `unverified`.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `apps/sandbox-executor/src/sandbox_executor/review_target.py` with a module docstring (Australian English)
   stating it does target resolution only, never posts and never merges.
2. `ReviewTargetError(RuntimeError)`: raised for every resolution failure; its message is passed through
   `redact_agent_secrets` before it is raised so callers can log it as-is.
3. Frozen dataclass `ReviewTarget`: `raw`, `head_branch`, `head_sha`, `pr_number: int | None = None`,
   `title: str | None = None`, `body: str | None = None`, plus a read-only property `is_pr` (`pr_number is not None`).
4. `is_pr_reference(target)`: true for a bare positive integer (after `strip`) or a URL matching
   `https://github.com/<owner>/<repo>/pull/<n>` (optionally with a trailing path or slash); false otherwise. Keep it a
   single compiled regular expression plus `isdigit`.
5. `RunGh` type alias (callable taking an argv list and `cwd`, returning `subprocess.CompletedProcess[str]`) and a
   default `_default_run_gh` with the same shape `flow.py` uses:
   `subprocess.run([...], cwd=..., capture_output=True, text=True, check=False, timeout=60)`. When `shutil.which("gh")`
   is `None` the default raises `ReviewTargetError("gh is not available ...")`; injected fakes bypass that check.
6. `resolve_review_target(repo_dir, target, run_gh=None)`: strip the target; empty raises. For a PR reference call
   `gh pr view <target> --json number,headRefName,headRefOid,title,body`; map `FileNotFoundError`, `OSError` and
   `subprocess.TimeoutExpired` to `ReviewTargetError`; a non-zero exit raises with redacted stderr; unparseable JSON, an
   empty `headRefName` or an empty `headRefOid` raises. Return a `ReviewTarget` with the PR fields. Split into
   `_resolve_pr(...)` and `_resolve_branch(...)` to keep branching low.
7. Branch path reuses `flow._resolve_review_commit(repo_dir, branch, fetch=True)` through a function-level import (the
   module is imported by `flow.py`, so a top-level import would be circular). That helper returns the bare branch name
   when it cannot resolve, so: if the result equals the branch name, raise; otherwise expand the short SHA to the full
   SHA with `git rev-parse --verify <sha>^{commit}` via `sandbox_executor.git.run_git(check=False)` and raise when that
   fails. A target of `HEAD` is rejected for the container path only by the caller (Step 4), not here.
8. `github_repo_slug(repo_url)`: returns `owner/repo` from an https or ssh GitHub URL, dropping any userinfo and a
   trailing `.git`; returns `None` for anything else. Used only to give `gh` repository context before a clone.
9. `DiffVerdict` as a `StrEnum` (`verified`, `empty`, `unverified`) and
   `verify_review_diff(repo_dir, target, base_branch="main")` returning `(verdict, detail)`:
   - `target` may be a `ReviewTarget` or a raw string; a raw string is classified with `is_pr_reference` only (no gh
     call, so the guard never depends on `shutil.which("gh")`).
   - Execution branches (the existing `_execution_parent_plan_branch` recognises them) delegate to the existing
     `flow._execution_effective_diff` and map `True/False/None` to `verified/empty/unverified`.
   - Every other target (PR head SHA, plain branch) fetches `base_branch` from origin when an `origin` remote exists,
     computes the merge base of `origin/<base>` (or local `<base>`) and the head (resolved SHA for a PR, branch ref
     otherwise) and calls the existing `changed_paths` helper; no changes means `empty`, any git error or unresolved ref
     means `unverified`. A raw PR string without a resolved SHA is `unverified` (fail closed).
10. Add `__all__` and full type annotations and docstrings for every public name (`ruleset.md` §2).

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (Steps 3, 4 and 6 build on it; p_success is above 0.6 so no sub-intent is needed)

### Safety & Constraint Considerations

- **Relevant rules:** `ruleset.md` §2 (typing, docstrings, no wildcard imports); intent constraint 8 (no new
  dependencies, small functions, Australian English); `docs/safety.md` (credentials must not leak).
- **Potential failure modes for this step:** circular import between `flow.py` and `review_target.py`; leaking a
  tokenised URL in an error; misclassifying a branch named with digits only as a PR.
- **Guardrails and early‑abort checks:** function-level imports from `flow`; every error string goes through the
  existing redactor; document that an all-digit target is always treated as a PR number (matches current
  `_resolve_review_commit` behaviour).

### Success & Discard Criteria

- **Success:** the module imports cleanly from both `flow.py` and the entrypoint, `ruff check` and `ruff format --check`
  pass, and the Step 6 unit tests for it pass.
- **Discard:** if the circular import cannot be avoided with function-level imports in under 30 minutes, move only the
  pure helpers into `review_target.py` and keep the flow-dependent wrappers in a thin call site; re-plan if more than
  1.5x the cost is consumed.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 2.0   |
| impact_pred         | 30.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 22.90 |

### Step Metrics Rationale

A new file with clear seams is low risk, but it is the foundation of the change and carries the PR-diff logic, the
least-specified part of the intent, hence entropy 2.0 and p 0.85.
`EV = 0.85 * 30.0 + 0.5 * 4.0 - 0.3 * 2.0 - 4.0 = 25.50 + 2.00 - 0.60 - 4.0 = 22.90`

---

## Step 3: Wire `flow.py` call sites: resolution, unconditional diff guard, failed post, pending-CI poll

- **Sub‑intent recommendation:** NO
- **Reasoning:** The edits are local to `_resolve_review_commit` and `run_review_loop`; they must land together with
  Step 2 to keep the suite green.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the host-side loop (used by `holon review-loop --local`, the container entrypoint and Stage 4 of
  `holon flow`) fail closed on an empty or unverified diff for every target, and on a failed consensus post.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `_resolve_review_commit`: replace the inline `gh pr view ... headRefOid` block with a call to
   `review_target.resolve_review_target(repo_dir, clean)` inside `try/except ReviewTargetError`; on success use
   `head_sha`, on failure keep today's behaviour (log at debug and fall back to `HEAD`), because this function only
   labels the summary commit. Nothing else in the function changes.
2. `run_review_loop` empty-diff gate: replace `if auto_pr and not effective_dry_run and shutil.which("gh"):` with:
   - dry run: log that the diff was not measured and keep going (the dry-run path already ends in a simulated,
     unmeasured status; additionally make sure a dry run can never return `approved: True` by forcing the consensus
     status to `not_measured` when `effective_dry_run`, so dry runs are explicit).
   - otherwise call `verify_review_diff(repo_dir, review_target, base_branch=target_branch)` unconditionally. For a PR
     reference, first resolve it with `resolve_review_target` (a `ReviewTargetError` yields the `unverified` verdict
     with the redacted reason).
   - `empty` returns the existing rejected result (status `rejected`, `error` set, no PR, no post) whatever `auto_pr`
     is; `unverified` keeps the existing `pr_refusal_error` path (no PR, no iterations, no post, status `failed` instead
     of `rejected` so the two cases are distinguishable). The PR-creation block keeps its own `auto_pr` and `gh` gate.
   - Extract the rejected-result dictionary into a small helper `_refused_review_result(...)` to keep branching low.
3. Failed post: after `post_pr_review_consensus` (both the returned-failure and the raised branch), when `posted` is
   false set `post_error` to the bounded, redacted `_posting_failure_context(posting_res)`; still append the ACT-POST
   failure row (existing code); then force `consensus_reached = False`, `consensus_status = "post_failed"` and
   `approval_score = 0.0` before the summary row and the action event are written, so the ledger summary says `failed`
   and `halt_reasons` includes "consensus post failed". Set `loop_result["error"]` to the post error. `halted_for_human`
   and `bean_0034_enforced` follow the forced value.
4. Bounded pending-CI wait: replace the single `time.sleep(15)` re-probe with a helper `_wait_for_pending_ci(...)`
   placed next to `_wait_for_ci_registration`. It polls `probe_ci_checks` while `pending > 0`, sleeping
   `min(ci_poll_interval, remaining)` (default `CI_REGISTRATION_POLL_SECONDS`), with a deadline of the remaining
   `review_iteration` stage budget for this iteration (from `iteration_timer`, using the injected `clock`). On deadline
   it returns the last probe and logs that checks were still pending; still-pending checks are never `all_passed`, so
   the iteration records a CI finding.
5. Add an injectable `sleep: Callable[[float], None] = time.sleep` parameter to `run_review_loop`,
   `_wait_for_ci_registration` and `_wait_for_pending_ci`, and let `_wait_for_ci_registration` use the injected `clock`
   too, so tests never sleep. No `gh pr merge` or auto-merge call is added; grep the diff for `merge` verbs before
   committing.
6. `run_review_loop_cli` already returns 1 when not approved; add a line printing `error` (redacted) when present so the
   operator sees why.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES (the core fail-closed behaviour; p above 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** intent constraint 8 (flow.py limited to call sites; this step is the minimal set the goal itself
  requires inside `run_review_loop`); `constraints.md` §3 (append-only ledgers: only new rows, via the existing
  `record_review_action` and `try_append_ledger_line`); `docs/safety.md` human review gate.
- **Potential failure modes for this step:** existing tests in `test_flow.py` and `test_review_loop.py` that relied on
  the guard being skipped without `gh` or with `--no-auto-pr`, or on an approval after a failed post, now fail; hidden
  extra ledger rows.
- **Guardrails and early‑abort checks:** run `test_flow.py` and `test_review_loop.py` immediately after this step; any
  existing assertion that encoded the fail-open behaviour is updated (declared here as an intended test change) and
  listed in the execution notes; keep the ACT-POST row shape untouched except for the existing `error_context`.

### Success & Discard Criteria

- **Success:** with `auto_pr=False`, a PR-number target or no `gh` on `PATH`, an empty diff yields a non-approved
  result; a failed post yields `approved False` and status `post_failed` with exactly one ACT-POST failure row; dry runs
  never report approved.
- **Discard:** if more than five unrelated existing tests break in ways that need production redesign, stop and re-plan
  rather than widening the change.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 2.2   |
| impact_pred         | 30.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 20.84 |

### Step Metrics Rationale

`flow.py` is a 5,000-line hot file with many review-loop tests, so regressions in existing assertions are the main risk
(entropy 2.2, conflict likelihood moderate).
`EV = 0.80 * 30.0 + 0.5 * 3.0 - 0.3 * 2.2 - 4.0 = 24.00 + 1.50 - 0.66 - 4.0 = 20.84`

---

## Step 4: Make the container entrypoint resolve, clone, verify and fail closed

- **Sub‑intent recommendation:** NO
- **Reasoning:** Confined to one 249-line file with an existing `run_cmd` seam; this is where the PR #90/#91 failure
  happened and it must ship with the rest.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Never pass a raw PR number to `git clone --branch`, verify the checked-out commit is the resolved
  head, and on any preparation failure write one failure action row, print a FAILED summary and exit 1 without computing
  or posting a consensus.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add `ReviewPreparationError` (or reuse `ReviewTargetError`) and split `main()` preparation into small functions:
   `_resolve_target(repo_dir, raw, run_gh)`, `_clone_or_fetch(repo_dir, target)`, `_verify_head(repo_dir, target)` and
   `_fail_preparation(repo_dir, raw, stage, detail)`.
2. `_resolve_target`: for a PR reference build a `run_gh` that adds `GH_REPO=<github_repo_slug(HOLON_REPO_URL)>` to the
   environment when the workspace has no `.git` (a PR URL needs no repo context); call `resolve_review_target`. For a
   branch target with no clone yet, the head SHA is taken after the clone (`git ls-remote` is not needed): the resolved
   target carries the branch name and `_verify_head` fills the SHA by calling `resolve_review_target` again against the
   fresh clone.
3. `_clone_or_fetch`: fresh workspace → `git clone --branch <head_branch> <repo_url> .`; existing workspace →
   `git fetch origin +refs/heads/<head_branch>:refs/remotes/origin/<head_branch>` then
   `git checkout -B <head_branch> origin/<head_branch>`. Every `run_cmd` uses `check=False` and its return code is
   inspected; a non-zero code raises with redacted stderr.
4. `_verify_head`: `git rev-parse HEAD` must equal `head_sha`; when it differs (branch moved), run
   `git fetch origin <head_sha>` and `git checkout --detach <head_sha>`, then re-check; any mismatch or failed command
   raises.
5. `_fail_preparation`: when `<repo_dir>/holon-knowledge/ledger` exists, append exactly one record (`action_id` from
   `generate_action_id("ACT-REV")`, `entry_type` `action`, `category` `code`, `outcome` `failure`, `action_origin`
   `pr_review`, `target` the raw target, `labels` `["review", "review-loop", "preparation"]`, `error_context` from
   `flow._bounded_error_context(detail)`) through
   `try_append_action_record(path, record, label=..., preserve_shape=True)`, which already takes `LedgerLock`. Otherwise
   print the redacted detail to stderr and state that no ledger row was written. Then print a short FAILED summary
   (target, stage, reason) and `sys.exit(1)`. `--json` prints a JSON object with `consensus.approved` false and
   `consensus.status` `failed` instead.
6. Pass `review.head_branch` (not the raw target) as `execution_branch` and as the loop `target` for branch targets; for
   PR targets keep the PR reference as the loop target so mergeability, CI and posting still address the PR, and use the
   head branch for the ledger metadata lookups (`intents.jsonl`, `plans.jsonl`).
7. The final `sys.exit(0 if approved else 1)` stays; approval now already excludes failed posts (Step 3).
8. Update the module docstring to describe the fail-closed preparation.

### Dependencies & Criticality

- **Depends on:** Step 2, Step 3
- **Is Bottleneck:** YES (it fixes the observed PR-number failure; p above 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §3 (append-only, one row only); `docs/safety.md` (redaction, no credential
  leaks); the existing `tests/leak_assertions.py` contract on clone banners.
- **Potential failure modes for this step:** the clone banner or the failure row leaks the tokenised repo URL; a failure
  row is written twice (ledger in a half-cloned workspace); `GH_REPO` derived from a credentialed URL.
- **Guardrails and early‑abort checks:** reuse `redact_command` and `redact_agent_secrets`; derive the slug only via
  `github_repo_slug` (which drops userinfo); keep the existing leak tests green.

### Success & Discard Criteria

- **Success:** for target `90` the fake `run_cmd` sees `git clone --branch <headRefName>` and never `--branch 90`; a
  failing clone exits 1, calls neither `run_review_loop` nor any posting function, and appends exactly one ACT-REV
  failure row when a ledger directory exists.
- **Discard:** if the PR-head lookup cannot be made to work without a repository context in the container, fall back to
  requiring a PR URL or `GH_REPO` and fail closed with a clear message, documenting the limitation.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 1.6   |
| impact_pred         | 25.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 17.52 |

### Step Metrics Rationale

One file with good seams, but it touches clone credentials and ledger writes, and the before-clone `gh` context is a
real-world unknown that tests can only fake.
`EV = 0.80 * 25.0 + 0.5 * 3.0 - 0.3 * 1.6 - 3.5 = 20.00 + 1.50 - 0.48 - 3.5 = 17.52`

---

## Step 5: Audit and bound every wait reachable from Stage 4

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mostly an audit with at most a few small edits; reading showed most loops are already bounded.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Guarantee that every loop reachable from `holon review-loop` and Stage 4 of `holon flow` has a
  monotonic deadline and a sleep, and record the audit result.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Audit table, recorded in the execution notes: `_find_git_root` (fixed in Step 1; bounded by filesystem depth, no
   sleep needed because it does no waiting); `_wait_for_proxy` in `cli.py` (already has a monotonic deadline and
   `PROXY_POLL_INTERVAL_SECONDS` sleep: no change); `_wait_for_ci_registration` (already deadline plus sleep; Step 3
   adds the injected clock and sleep); the pending-CI re-probe (replaced by `_wait_for_pending_ci` in Step 3);
   `check_pr_mergeability` retries (`range(3)` with `time.sleep(2)` and per-call subprocess timeout: bounded; confirm
   every `subprocess.run` there has a `timeout`); `LedgerLock` acquisition in `ledger.py` (monotonic timeout already;
   confirm it sleeps between attempts and add the missing back-off sleep only if absent, as it is reached by every
   review row); `probe_ci_checks` and `post_pr_review_consensus` subprocess calls (confirm each has `timeout`); the
   review iteration loop itself (bounded by `max_iterations` and the per-iteration stage budget).
2. Any `subprocess.run` on these paths without a `timeout` gets `timeout=60` (gh) or the existing git default, with
   `subprocess.TimeoutExpired` mapped to the existing failure result.
3. If Step 1 refuted the suspect, add the same deadline treatment to every `while True` on the path and write "root
   cause unconfirmed" in the execution notes.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 5; constraint 8 (call-site sized edits only).
- **Potential failure modes for this step:** scope creep into unrelated modules; changing `LedgerLock` semantics used by
  every stage.
- **Guardrails and early‑abort checks:** edit `ledger.py` only if a sleep is genuinely missing, and only to add one;
  otherwise document.

### Success & Discard Criteria

- **Success:** every listed loop is documented as bounded, with code changes only where a bound was missing.
- **Discard:** if a bound would require redesigning a module outside Stage 4, document it as a follow-up instead.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 0.8   |
| impact_pred         | 10.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 8.26  |

### Step Metrics Rationale

Low risk because the audit mostly confirms existing bounds.
`EV = 0.85 * 10.0 + 0.5 * 3.0 - 0.3 * 0.8 - 1.5 = 8.50 + 1.50 - 0.24 - 1.5 = 8.26`

---

## Step 6: Add the pytest coverage at the injected seams

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tests are the acceptance evidence for this change and must ship with it; explicitly declared here as
  required by `ruleset.md` §3.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove each fail-closed path with hermetic pytest tests using fakes at the `run_gh`, `run_cmd` and
  dispatcher seams, never real `gh`, git remotes, Docker or model runners.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. New `apps/sandbox-executor/tests/test_review_target.py`:
   - `is_pr_reference` truth table (bare number, PR URL, branch names, empty string, a URL that is not a PR).
   - PR number and PR URL resolve to `headRefName` and `headRefOid` through a fake `run_gh` returning canned JSON; the
     fake records the argv and asserts the exact `--json number,headRefName,headRefOid,title,body` field list.
   - `ReviewTargetError` for non-zero exit, `TimeoutExpired`, `FileNotFoundError`, invalid JSON, empty branch and empty
     SHA; the default runner raises when `shutil.which("gh")` is patched to `None`.
   - Branch target: a local `tmp_path` git repository without a remote resolves to the full branch tip SHA; an unknown
     branch raises.
   - `verify_review_diff` returns `empty` for an execution branch identical to its plan branch, `verified` when it
     changes code, `unverified` for an unresolved PR head, and `empty` for a PR head equal to its base, all on local
     `tmp_path` repositories without `gh`.
   - `github_repo_slug` drops userinfo and `.git`.
2. `apps/sandbox-executor/tests/test_review_loop.py` additions:
   - Entrypoint with target `90`: fake resolver returns `headRefName` `feature/x`; fake `run_cmd` records calls; assert
     `git clone --branch feature/x` was used and no argv contains `--branch` followed by `90`.
   - Failing clone (fake `run_cmd` returns code 128): `SystemExit` code 1, `run_review_loop` and
     `post_pr_review_consensus` mocks never called, and the workspace's `executions.jsonl` (pre-created ledger
     directory) gains exactly one row with `outcome` `failure`, `action_origin` `pr_review` and an `ACT-REV` id; without
     a ledger directory, nothing is written and stderr carries the reason.
   - HEAD mismatch after clone triggers an explicit SHA fetch; a failing fetch exits 1.
   - Empty-diff guard runs with `auto_pr=False` and for a PR-number target (patch `verify_review_diff` to `empty` and
     assert it was called, `approved` is false and no post happened), and with `shutil.which` returning `None`.
   - Failed post (fake returns failure, and a second case raises): `approved` false, status `post_failed`, `error` set,
     exactly one ACT-POST failure row; `run_review_loop_cli` returns 1.
   - Using the suite-wide `gh_write_guard`, run a full approved loop with a fake `gh` and assert no recorded argv
     contains `pr merge` or `--auto`.
   - Dry run never reports `approved` and reports `not_measured`.
   - `_wait_for_pending_ci` honours its deadline with an injected fake clock and a recording sleep (no real sleeping),
     and returns early when checks stop pending.
3. `apps/sandbox-executor/tests/test_cli.py`: the Step 1 deadline-guarded `_find_git_root` tests, plus a dispatch test
   asserting `holon review-loop --local` exits 1 when the loop result is not approved.
4. Update any existing assertion in `test_flow.py` or `test_review_loop.py` that encoded the old fail-open behaviour,
   and list each one in the execution notes.

### Dependencies & Criticality

- **Depends on:** Steps 1 to 5
- **Is Bottleneck:** YES (acceptance evidence; p above 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** `ruleset.md` §3 (pytest only, test changes declared); intent goal 6 (no real gh, git remotes,
  Docker or LLM runners); `tests/gh_write_guard.py`.
- **Potential failure modes for this step:** tests that accidentally reach the host `gh`; flaky timing tests;
  xdist-unsafe shared state.
- **Guardrails and early‑abort checks:** inject clocks and sleeps instead of real time; use `tmp_path` only; rely on the
  write guard to fail loudly on any unmocked `gh` write.

### Success & Discard Criteria

- **Success:** all new tests pass on the first targeted run of the three modules and remain green under
  `uv run task test`.
- **Discard:** a test that can only pass with real network or Docker is dropped and its behaviour documented instead.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 1.0   |
| impact_pred         | 15.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 10.20 |

### Step Metrics Rationale

Many cases, but every seam already exists or is added in Steps 2 to 4; the main risk is fixture friction with the
entrypoint's module-level imports. `EV = 0.80 * 15.0 + 0.5 * 3.0 - 0.3 * 1.0 - 3.0 = 12.00 + 1.50 - 0.30 - 3.0 = 10.20`

---

## Step 7: Update the Stage 4 docs and CLI help

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small documentation edits tied to the behaviour change.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Describe the fail-closed resolution, clone, empty-diff and posting behaviour and the bounded waits
  where operators look for them.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Update the review-loop sections of `apps/sandbox-executor/README.md`, `docs/git_flow.md` and `docs/budgets.md` (Stage
   4 budget and the pending-CI poll bounded by `review_iteration` and `CI_REGISTRATION_POLL_SECONDS`), and
   `docs/ledger_schema.md` for the preparation-failure ACT-REV row and the `post_failed` status.
2. Document the limitation: when the workspace has no `holon-knowledge/ledger/` directory, a preparation failure is
   reported only on stderr and through the exit code.
3. Update the `review-loop` subparser help in `cli.py` (help strings only) and the entrypoint `--auto-pr` help to say
   the empty-diff guard runs whatever `--auto-pr` is, and that the command exits 1 unless the consensus is approved and
   posted.
4. Format with `npx prettier --write` on the touched markdown files only (repository configuration), Australian English
   throughout.

### Dependencies & Criticality

- **Depends on:** Steps 3 to 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent constraint 8 (`lint-docs`, target-native paths, no harness paths or bean or review ids
  added).
- **Potential failure modes for this step:** prettier reflow churn on unrelated lines; inventing behaviour not shipped.
- **Guardrails and early‑abort checks:** run prettier only on edited files; cross-check every doc claim against the
  tests from Step 6.

### Success & Discard Criteria

- **Success:** `uv run task lint-docs` passes and every described behaviour has a test.
- **Discard:** none expected; if a doc file is ambiguous, prefer the README section.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.4   |
| impact_pred         | 5.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 3.88  |

### Step Metrics Rationale

Routine docs work. `EV = 0.90 * 5.0 + 0.5 * 1.0 - 0.3 * 0.4 - 1.0 = 4.50 + 0.50 - 0.12 - 1.0 = 3.88`

---

## Step 8: Validate with targeted tests, the full suite once, and lint

- **Sub‑intent recommendation:** NO
- **Reasoning:** The final gate; cheap and mandatory.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Demonstrate the acceptance criteria with the repository's own commands from the repository root.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. From the repository root:
   `uv run pytest apps/sandbox-executor/tests/test_review_target.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_cli.py apps/sandbox-executor/tests/test_flow.py`.
   Never set `PYTHONPATH` and never run from `apps/sandbox-executor`.
2. Fix failures, then run `uv run task test` exactly once (no repeated runs; this is not a performance change).
3. Run `uv run task lint` and `uv run task lint-docs`; apply `ruff format` to touched Python files only.
4. Grep the diff for `pr merge`, `--auto` and `enable-auto-merge` and confirm none was added; grep for harness paths and
   bean or review ids introduced by this change and confirm none were added.
5. Record in the execution notes: the Step 1 confirmation result, the Step 5 audit table and any existing test whose
   assertion changed.

### Dependencies & Criticality

- **Depends on:** Steps 1 to 7
- **Is Bottleneck:** YES (acceptance gate; p above 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** `ruleset.md` §3; intent constraint 8; `metrics.md` §1.3 bootstrap acceptance criteria.
- **Potential failure modes for this step:** unrelated pre-existing failures in the full suite; `uv.lock` drift.
- **Guardrails and early‑abort checks:** do not add dependencies; if an unrelated test fails, record it with output
  rather than editing it.

### Success & Discard Criteria

- **Success:** targeted modules, `uv run task test`, `uv run task lint` and `uv run task lint-docs` all pass.
- **Discard:** if the full suite shows failures caused by this change that need production redesign, stop and re-plan.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 0.4   |
| impact_pred         | 10.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 7.88  |

### Step Metrics Rationale

`EV = 0.85 * 10.0 + 0.5 * 2.0 - 0.3 * 0.4 - 1.5 = 8.50 + 1.00 - 0.12 - 1.5 = 7.88`

---

### Strategy Rationale

The intent is accepted as is: it is well-specified, already the first slice of a three-part sequence, and the operator
explicitly asked for one plan and one execution branch. Code reading confirms each reported defect: the entrypoint
passes `args.target` verbatim to `git clone --branch` with `check=False`; the empty-diff gate in `run_review_loop` is
`if auto_pr and not effective_dry_run and shutil.which("gh")` and `_execution_effective_diff` treats any non-execution
target (such as `90`) as "not an execution branch", meaning verified; a failed `post_pr_review_consensus` only writes an
ACT-POST failure row and never changes `consensus_reached`; and `_find_git_root` never advances `cur`, a direct
explanation of a 99% CPU hang with no child process. The pending-CI re-probe is a single fixed `time.sleep(15)`, not a
deadline-bounded poll.

Ordering puts the cheap, high-learning confirmation first (Step 1), then the new module (Step 2) that both host and
container paths need, then the call sites (Steps 3 and 4), the loop audit (Step 5), tests (Step 6), docs (Step 7) and
validation (Step 8).

Aggregation: entropy is the sum of step entropies (`0.6 + 2.0 + 2.2 + 1.6 + 0.8 + 1.0 + 0.4 + 0.4 = 9.0`); cost is the
sum of step costs (`2.0 + 4.0 + 4.0 + 3.5 + 1.5 + 3.0 + 1.0 + 1.5 = 20.5`). Impact (85) is a qualitative judgement
rather than a sum: closing a false-approval path on the human review gate is high value, but reviewer dispatch is
deferred to later changes. p_success (0.78) is set slightly below the weakest bottleneck steps (Steps 3 and 4 at 0.80)
because the `flow.py` edits and the entrypoint share a regression risk in existing tests. Learning value (5.0) mostly
comes from Step 1. `EV = 0.78 * 85.0 + 0.5 * 5.0 - 0.3 * 9.0 - 20.5 = 66.30 + 2.50 - 2.70 - 20.5 = 45.60`.

Strong assumptions: `gh` in the container can use `GH_REPO` for repository context before the clone; existing tests that
encode fail-open behaviour are few and can be updated as declared test changes.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1: work only on the plan's execution branch; never commit to `main`.
  - `holon-config/world/constraints.md` §3: ledgers append-only; the new failure rows only append, through
    `try_append_action_record(..., preserve_shape=True)` under `LedgerLock`.
  - `holon-config/world/ruleset.md` §2 and §3: typed, documented public functions; pytest only; test changes declared
    (Step 6 and the updated fail-open assertions).
  - `docs/safety.md`: human review is the final gate; agents never merge; credentials never leak into logs or rows.
- **Potential violations or edge cases:**
  - A test that reaches the host `gh` with a write verb.
  - Error contexts or clone banners carrying a tokenised repository URL.
  - An all-digit branch name is treated as a PR number (existing behaviour, now documented).
  - Edits to `flow.py` inside `run_review_loop` go slightly beyond pure call sites, but the intent's goals 3 to 5
    require them there; they are kept to small helpers.
- **Mitigations built into the plan:**
  - Fakes at `run_gh`, `run_cmd` and dispatcher seams, plus the suite-wide `gh_write_guard`.
  - All error text goes through the existing redactors and `_bounded_error_context`.
  - An explicit diff grep for merge commands, harness paths and bean or review ids before commit.
  - Injected clocks and sleeps so no test sleeps or depends on timing.
- **Residual risk accepted (and why):** The before-clone `gh pr view` in the container is verified only through fakes; a
  real-environment failure fails closed (exit 1 with a FAILED summary), which is the desired direction of failure.
- **Allocated Entropy Budget:** 12.0
- **Predicted Plan Entropy:** 9.0
- **Budget Compliance:** The strategy fits within budget (9.0 is at or below 12.0), so it runs as one plan with no
  sub-intent list.

## Plan Description & Strategy

Make Stage 4 fail closed in one execution branch. Confirm and fix the `_find_git_root` hang. Add `review_target.py` to
resolve branch, PR-number and PR-URL targets to a verified head branch and SHA, and to classify the diff as verified,
empty or unverified. Route `flow.py`'s PR head lookup through it. Run the diff guard unconditionally. Turn a failed
consensus post into a non-approved `post_failed` result. Bound the pending-CI wait. Harden the container entrypoint so
it resolves before cloning, clones the head branch, verifies the head SHA, and on any preparation failure writes one
ACT-REV failure row and exits 1 without a consensus. Hermetic pytest coverage, documentation and one full validation run
complete the change. No reviewer dispatch, no merge and no auto-merge are added.

## Sub-intent list

None
