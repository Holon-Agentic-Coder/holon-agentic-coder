# Plan for I-1794124652-post-consensus-review-once-per-pr-exit

- **Plan ID:** P-1794124844-claude-agent-claude-fable-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-fable-5 (version: 2.1.302)
- **Created At:** 2026-11-08T08:00:45.532Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A — the intent is well-scoped, precisely specified, and matches the observed
  defect in `post_pr_review_consensus()` (flow.py lines 1498–1684: an unconditional `gh pr comment` in "Action 1"
  followed by a `gh pr review` submission in "Action 2", producing two identical posts on every PR).
- **Exploration stance:** conservative — this is a surgical behavioural fix with explicit acceptance criteria, a known
  root cause, and an existing hermetic test harness (`shutil.which` + `subprocess.run` mocks). Exploration would add
  entropy without learning value.
- **Safety priority level:** standard
- **Priority Justification:** No constraint in `holon-config/world/constraints.md` or `docs/safety.md` elevates this
  intent: predicted entropy is well under 10 (process-sandbox tier per constraints.md §2), no ledger files are touched
  (constraints.md §3 is respected by not modifying `holon-knowledge/ledger/*.jsonl`), no estimator, routing or
  governance change is involved, and merging to `main` remains the human maintainer's decision (safety.md Invariant 4,
  intent constraint "merging stays with the human maintainer"). Test-file changes are explicitly declared here, as
  required by `holon-config/world/ruleset.md` §3 ("Any change to test files must be explicitly declared in the planning
  step").

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** The failure mode, the fix, and the fallback ladder are fully specified by the intent. The repo
  already contains hermetic test patterns for exactly this function, so the correct move is pure exploitation of a known
  pattern (KB pattern: mock `shutil.which` and `subprocess.run`, assert on `call_args_list`). The only mild uncertainty
  — how callers consume the return dictionary — was resolved during planning by reading the two consumers
  (`_posting_failure_context` at flow.py:3382 and the `posted` computation at flow.py:4739).

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 5.8   |
| impact_pred         | 70.0  |
| cost_pred           | 12.0  |
| learning_value_pred | 3.0   |
| ev_pred             | 50.76 |

### Strategy Rationale

The intent is accepted as-is: it names the exact function, the exact defect, the exact replacement behaviour, and the
exact files in scope, and planning-time inspection confirmed every claim. `post_pr_review_consensus()` currently
dual-posts ("Action 1: Top-Level Discussion Comment" via `gh pr comment`, then "Action 2: Official Review Submission"
via `gh pr review`), and its docstring even advertises this as intended behaviour ("Dual-post review consensus"). The
fix is a linear fallback ladder with one visible post per PR exit.

Strategy choices that shape the steps:

1. **Preserve the return-dictionary shape** (`target`, `gh_available`, `diagnostics`, `discussion_comment`,
   `official_review`, `dual_posted`). Two production consumers depend on it: `_posting_failure_context()` (flow.py:3382
   iterates `discussion_comment` and `official_review`) and the ACT-POST `posted` computation (flow.py:4739 reads
   `dual_posted` or `official_review.success`). Roughly a dozen tests patch the function with
   `return_value={"dual_posted": True}`. Keeping the keys and repurposing semantics (`official_review` = the primary
   `gh pr review` attempt; `discussion_comment` = the last-resort `gh pr comment` fallback, normally `attempted: False`;
   `dual_posted` = "the report was posted exactly once") confines the blast radius to the one function plus its direct
   tests, instead of rippling through ledger-recording code and its tests.
2. **Leave the ACT-POST ledger action wording and labels untouched** (flow.py:4744 "Review consensus dual-posted…",
   label `dual-post`). Tests outside the behavioural change (test_flow.py:1965, test_flow.py:3359) assert on these
   strings, ledger consumers may grep for them, and the intent's goal does not ask for ledger-text changes. This keeps
   the three append-only ledgers' row shapes stable (constraints.md §3).
3. **Explicit interpretation recorded for the executor:** when `--approve` fails with a non-422 error, the current code
   performs no fallback. Under the new single-post model, any unsuccessful end state of the `gh pr review` ladder
   (non-422 `--approve` failure, failed `--comment` submission, or failed 422 fallback) proceeds to the last-resort
   `gh pr comment`, because the intent's acceptance criterion is that the report lands exactly once; a failed review
   submission posts nothing, so the last resort cannot double-post. `self_approval_fallback` stays `False` for non-422
   failures.

Metric aggregation: `entropy_pred` and `cost_pred` are sums over steps (`0.3 + 2.5 + 1.8 + 0.8 + 0.4 = 5.8` and
`1.0 + 4.0 + 3.5 + 2.5 + 1.0 = 12.0`). `impact_pred` is not summed: the plan's impact equals the deliverable of the
bottleneck Step 2 (the behavioural fix, 70 on the 0–100 convention used in this repo's ledger), since the other steps
only protect or validate that deliverable. `p_success_pred = 0.90` is a qualitative joint over the three failure-capable
steps (Steps 2–4, individually 0.90–0.93); they are strongly correlated (the same mocks and the same function), so the
joint is above the naive independent product (`0.93 * 0.90 * 0.92 ≈ 0.77`). `learning_value_pred` is modest (3.0): the
main epistemic gain is calibrating how safely a return-shape-preserving behaviour change lands against a large mocked
test surface. EV uses the bootstrap constants `lambda = 0.3`, `mu = 0.5` from `holon-config/metrics/ev_config.json`:
`EV = 0.90 * 70.0 + 0.5 * 3.0 - 0.3 * 5.8 - 12.0 = 63.0 + 1.5 - 1.74 - 12.0 = 50.76`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `constraints.md §1` (branch isolation): all work happens on
    `I-1794124652-post-consensus-review-once-per-pr-exit/P-1794124844-claude-agent-claude-fable-5/E-{ts}-...` execution
    branches; no checkout of `main`.
  - `constraints.md §2` (sandbox tiers): predicted entropy 5.8 < 10 → process-sandbox tier is sufficient.
  - `constraints.md §3` / safety.md Invariant 6 (ledger immutability): no edits to `holon-knowledge/ledger/*.jsonl`; the
    three append-only ledgers are kept.
  - `ruleset.md §2` (PEP 8, strict typing, docstrings): the rewritten function keeps its full signature typing and gets
    a corrected docstring in Australian English.
  - `ruleset.md §3` (test boundary): test changes are declared in Steps 3 of this plan.
  - Intent constraints: Australian English, no new dependencies, `uv run pytest`, `uv run task lint`,
    `uv run task lint-docs`, target-native paths, human-only merging.
- **Potential violations or edge cases:**
  - Accidentally breaking the ACT-POST recording path if the return shape drifts (flow.py:3382, flow.py:4739).
  - Hidden test assertions on the old dual-post behaviour outside the two in-scope test files (e.g. integration tests
    patching the function) — mitigated because patched mocks bypass the real implementation.
  - Ruff flagging now-unused `noqa: PLR0912/PLR0915` codes after the function is simplified (RUF100-style failures in
    `uv run task lint`).
- **Mitigations built into the plan:**
  - Step 1 pins the green baseline and enumerates every consumer before any edit.
  - Return-dictionary keys preserved; semantics documented in the docstring.
  - Ledger action strings left untouched.
  - Step 4 runs the targeted tests first, then the full suite once, then both lint tasks.
- **Residual risk accepted (and why):** Behaviour on a real GitHub PR (actual HTTP 422 text variants from `gh`) cannot
  be exercised hermetically; the detection predicate ("422" / "Can not approve your own pull request" /
  "PullRequestReviewAuthorError") is kept verbatim from the current code, which is already field-proven, so the residual
  risk is low and accepted.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 5.8
- **Budget Compliance:** The strategy fits within budget (5.8 ≤ 15.0); no sub-intent decomposition is needed.

## Plan Description & Strategy

Five linear steps on one execution branch: (1) verify the baseline and enumerate consumers, (2) rewrite
`post_pr_review_consensus()` into a single-post fallback ladder, (3) update the hermetic unit tests in
`apps/sandbox-executor/tests/test_flow.py` and `apps/sandbox-executor/tests/test_review_loop.py` to assert exactly-once
posting, (4) validate with targeted pytest, one full-suite run, and both lint tasks, (5) commit with a conventional
message and record the execution artefact. No sub-intents: the change is small, low-entropy, and self-contained, so
decomposition would only add git and pipeline overhead.

The new control flow inside `post_pr_review_consensus()`:

1. Compute `approved` exactly as today (ensemble verdict plus at least one completed reviewer — the existing
   never-self-promote guard stays byte-identical).
2. If `gh` is missing, return the existing diagnostics shape with both steps unattempted.
3. Primary attempt: `gh pr review <target> --approve --body <report>` when approved, else
   `gh pr review <target> --comment --body <report>`.
4. If the `--approve` attempt fails and stderr matches the 422 self-approval predicate, retry once with
   `gh pr review <target> --comment --body <report>` and set `self_approval_fallback = True`.
5. Only if the review ladder ends unsuccessfully, post the last-resort `gh pr comment <target> --body <report>`
   (recorded under the existing `discussion_comment` key, `attempted: True` only in this branch).
6. Return the same keys as today; `dual_posted` now means "the consensus report is visible on the PR exactly once".

---

## Step 1: Baseline verification and consumer enumeration

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only, cheap, zero risk; it exists to pin the green baseline so later failures are attributable to
  this intent's edits and not pre-existing drift.
- **Step Type:** INFO_GATHERING
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Confirm the current dual-post implementation, its two production consumers, and a green test baseline
  before touching anything.
- **Git branch:**
  I-1794124652-post-consensus-review-once-per-pr-exit/P-1794124844-claude-agent-claude-fable-5/E-{ts}-baseline/\_
  (execution branch; a single execution branch may carry Steps 1–5)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Rebase the plan branch from the intent branch per constraints.md §1 rebase discipline, then cut the execution branch.
2. Read `post_pr_review_consensus()` (flow.py ~1498–1684) and confirm the two subprocess call sites ("Action 1" comment,
   "Action 2" review) and the 422 predicate.
3. Enumerate consumers of the return dictionary: `_posting_failure_context()` (flow.py ~3363–3388) and the ACT-POST
   block (flow.py ~4712–4760, especially the `posted` expression at ~4739); confirm no other reader exists via a
   repo-wide grep for `dual_posted`, `discussion_comment`, `official_review`.
4. Enumerate the directly affected tests: test_flow.py ~1732–1859 (six `post_pr_review_consensus` unit tests) and
   test_review_loop.py ~300–406 (five unit tests) plus the loop-exit test at ~1960 (stub-based, expected unaffected).
5. Run the two in-scope test files once via
   `uv run pytest tests/test_flow.py tests/test_review_loop.py -n auto --dist worksteal` from `apps/sandbox-executor`
   and record pass counts and pytest-reported time as the baseline.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO (a dirty baseline would pause, not doom, the plan — it would surface pre-existing breakage to
  report back)

### Safety & Constraint Considerations

- **Relevant rules:** constraints.md §1 (rebase discipline), safety.md Invariant 1 (git discipline).
- **Potential failure modes for this step:** baseline tests already failing (pre-existing drift); rebase conflict.
- **Guardrails and early‑abort checks:** if the baseline is red for reasons unrelated to this intent, stop and report
  rather than fixing out-of-scope failures; if rebase conflicts persist beyond 3 attempts, escalate per safety.md
  failure containment §2.

### Success & Discard Criteria

- **Success:** Baseline run of the two test files passes; consumer list confirmed to be exactly the two known sites.
- **Discard:** Baseline red for out-of-scope reasons, or an unexpected third consumer of the return dictionary that
  would widen scope beyond the intent's three files.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.3   |
| impact_pred         | 5.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 4.06  |

### Step Metrics Rationale

Read-only plus one test invocation: near-certain success, negligible entropy (no file mutations beyond branch
bookkeeping). `EV = 0.98 * 5.0 + 0.5 * 0.5 - 0.3 * 0.3 - 1.0 = 4.90 + 0.25 - 0.09 - 1.0 = 4.06`.

---

## Step 2: Rewrite post_pr_review_consensus() as a single-post fallback ladder

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is the core of the intent but it is a single-function rewrite (~90 affected lines) with a fully
  specified target behaviour and preserved interface; a sub-intent would add pipeline overhead with no risk reduction.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Replace the unconditional dual-post with: `gh pr review --approve` (when approved) →
  422-self-approval fallback to `gh pr review --comment` → last-resort `gh pr comment` only when the review ladder
  failed; exactly one visible post per PR exit.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/flow.py`, keep the function signature, the `clean_target` computation,
   the `approved` derivation (ensemble verdict + completed reviewer guard, unchanged), the
   `build_pr_review_consensus_report` call, and the `gh` availability check with its existing early-return shape.
2. Delete the unconditional "Action 1: Top-Level Discussion Comment" block as the first action.
3. Make the `gh pr review <target> {--approve|--comment} --body <report>` submission the primary and normally only post,
   retaining the existing 60-second timeout, `cwd=repo_dir`, `gh_env`, and `check=False` conventions.
4. Keep the existing 422 predicate verbatim ("422" in stderr, or "Can not approve your own pull request", or
   "PullRequestReviewAuthorError"); when it matches an `--approve` failure, set `self_approval_fallback = True`,
   `submitted_flag = --comment`, and retry once with `gh pr review --comment`.
5. If the review ladder ends without success (non-422 `--approve` failure, failed `--comment` submission, failed 422
   retry, or a subprocess exception), run the last-resort `gh pr comment <target> --body <report>` once and record it
   under the `discussion_comment` key with `attempted: True`; in every successful-review path, `discussion_comment`
   reports `attempted: False` with an explanatory output string.
6. Preserve the return keys exactly (`target`, `gh_available`, `diagnostics`, `discussion_comment`, `official_review`
   with `flag`/`success`/`self_approval_fallback`/`output`, `dual_posted`); redefine `dual_posted` as "posted exactly
   once" = `review_success or comment_success`, which keeps the `posted` expression at flow.py:4739 truthful without
   touching it.
7. Rewrite the docstring in Australian English: single-post behaviour, the fallback ladder, the Bean 0034 human-only
   merge note retained; drop the "Dual-post"/"Always posts a top-level discussion comment" wording; update the
   `diagnostics` strings to describe single posting (e.g. success text no longer requires both legs).
8. Re-evaluate the `# noqa: PLR0912, PLR0913, PLR0915` suppressions: the simplified function may no longer need
   PLR0912/PLR0915 — remove any suppression ruff reports as unused so `uv run task lint` stays clean.
9. Do not touch the ACT-POST ledger wording, labels, or `_posting_failure_context()`; do not modify anything in
   `holon-knowledge/ledger/`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (p_success_pred 0.93 ≥ 0.6, so no forced sub-intent)

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md §2 (typing, docstrings, PEP 8), constraints.md §3 (ledger immutability — untouched),
  intent constraints (Australian English, no new dependencies, target-native paths).
- **Potential failure modes for this step:** return-shape drift breaking flow.py:3382/4739; losing the
  never-self-approve guard; 422 predicate weakened; double-post resurrected via the last-resort comment running after a
  successful review.
- **Guardrails and early‑abort checks:** the last-resort comment is syntactically reachable only from the review-failure
  branch; the `approved` derivation is copied verbatim; Step 4's targeted tests assert `subprocess.run` call counts
  (exactly one post call on the happy path).

### Success & Discard Criteria

- **Success:** Function compiles, posts at most one visible message per invocation, keeps the documented return keys,
  and the never-approve-without-ensemble guard is byte-identical in effect.
- **Discard:** If preserving the return shape proves impossible without touching ledger-recording code (would exceed
  intent scope), stop and report; cost exceeding `1.5x` of `cost_pred` likewise aborts.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 2.5   |
| impact_pred         | 70.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 61.35 |

### Step Metrics Rationale

Single-function rewrite against a precise spec with proven subprocess conventions already in the file; main risk is
subtle return-semantics drift, held down by Step 1's consumer enumeration. Entropy 2.5 reflects ~90 changed lines in one
hot module. `EV = 0.93 * 70.0 + 0.5 * 2.0 - 0.3 * 2.5 - 4.0 = 65.1 + 1.0 - 0.75 - 4.0 = 61.35`.

---

## Step 3: Update hermetic unit tests to assert exactly-once posting

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mechanical adaptation of eleven existing mock-based tests in the two in-scope files; cheap, contained,
  and reuses the repo's established mock pattern. Declared here to satisfy ruleset.md §3's test-change boundary.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the unit tests assert single posting: exactly one `subprocess.run` call on the happy path,
  review-first ordering, 422 fallback producing one visible post, and `gh pr comment` only after review failure.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `tests/test_flow.py` — rename/rework `test_post_pr_review_consensus_dual_posting` (~1732) to a single-post test:
   `mock_run.call_count == 1`, the only call is `gh pr review <target> --approve`, no `gh pr comment` invocation,
   `dual_posted` True, `discussion_comment.attempted` False.
2. `test_post_pr_review_consensus_self_approval_422_fallback` (~1763): side effects become [422-failing approve,
   succeeding review-comment]; assert `call_count == 2`, both calls are `gh pr review`, `self_approval_fallback` True,
   flag `--comment`, exactly one successful post, no `gh pr comment`.
3. Repurpose `test_post_pr_review_consensus_comment_failure_proceeds_to_review` (~1796) into the new last-resort test:
   review `--comment` submission fails (non-422), then `gh pr comment` runs and succeeds; assert ordering (review
   first), `discussion_comment.attempted` True and success True, `dual_posted` True.
4. `test_post_pr_review_consensus_non_422_failure_no_fallback` (~1811): non-422 `--approve` failure must not set
   `self_approval_fallback` and must not retry `gh pr review --comment`; per the recorded interpretation it proceeds to
   the last-resort `gh pr comment` — assert that call order and that `official_review.success` is False.
5. `..._when_gh_missing_returns_diagnostics` (~1825) keeps its shape assertions (both legs unattempted).
   `..._error_output_surfaces_diagnostic_context` (~1841): side effects become [failing review, failing last-resort
   comment]; keep the exit-code and stderr surfacing assertions, adjust the expected call list and the `diagnostics`
   wording to the new single-post phrasing.
6. `tests/test_review_loop.py` — `test_post_pr_review_consensus_detects_gh_and_executes_commands` (~300):
   `call_count == 1` with the review call first; `..._never_approves_without_a_completed_approving_ensemble` (~328) and
   `..._approved_flag_alone_never_approves` (~353): the review call moves from `call_args_list[1]` to
   `call_args_list[0]`, assertions otherwise unchanged (guard stays authoritative); `..._diagnostics_when_gh_missing`
   (~372) unchanged in substance; `..._diagnostics_when_gh_fails_nonzero` (~387): side effects become [failing review,
   failing last-resort comment], `dual_posted` False, diagnostics wording updated.
7. Leave `test_post_pr_review_consensus_called_only_once_at_loop_exit` (~1960) and every test that patches
   `post_pr_review_consensus` with `return_value={"dual_posted": True}` untouched — the preserved key makes them
   forward-compatible.
8. Keep all docstrings and new assertion messages in Australian English; no new test dependencies.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES (acceptance criterion 3 requires these tests to pass; p_success_pred 0.90 ≥ 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md §3 (test changes declared in planning — satisfied by this step), intent constraint
  "tests strictly via `uv run pytest`".
- **Potential failure modes for this step:** missed assertion on old call ordering elsewhere in the two files;
  over-asserting on diagnostics strings that Step 2 phrases differently.
- **Guardrails and early‑abort checks:** grep both files for `call_args_list`, `gh pr comment`, `dual_posted` and
  `Review posting` before finishing; align diagnostics-string assertions with the actual Step 2 wording rather than
  guessing.

### Success & Discard Criteria

- **Success:** All eleven adapted tests encode exactly-once posting and pass locally against the Step 2 implementation.
- **Discard:** If test adaptation reveals the implementation posts twice in any path, return to Step 2 rather than
  weakening assertions; abort if cost exceeds `1.5x` of `cost_pred`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.8   |
| impact_pred         | 30.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 1.5   |
| ev_pred             | 23.71 |

### Step Metrics Rationale

Eleven focused tests across two files following an in-repo mock pattern; moderate chance of one or two
assertion-ordering slips that cost an extra iteration, hence 0.90.
`EV = 0.90 * 30.0 + 0.5 * 1.5 - 0.3 * 1.8 - 3.5 = 27.0 + 0.75 - 0.54 - 3.5 = 23.71`.

---

## Step 4: Validation — targeted tests, full suite once, lint gates

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard verification gate; cheap relative to the assurance it buys, and mandated by the acceptance
  criteria and intent constraints.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove acceptance criteria: targeted tests green, full `apps/sandbox-executor` suite green via
  `uv run pytest`, and both lint tasks pass.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. From `apps/sandbox-executor`, run the affected tests first:
   `uv run pytest tests/test_flow.py tests/test_review_loop.py -n auto --dist worksteal`.
2. Then run the full suite once via `uv run task test` (the repo's task wrapper around `uv run pytest`); this intent has
   no performance or timing target, so per the test-run discipline there are no repeated runs — one targeted run, one
   full run. Report each run's pass/fail counts and pytest-reported time.
3. Run `uv run task lint` and `uv run task lint-docs`; fix any ruff findings confined to the in-scope files (notably
   unused `noqa` codes from Step 2's simplification) and any docs-lint fallout if a touched docstring feeds rendered
   docs.
4. If anything outside the three in-scope files fails, diagnose whether the preserved return shape actually drifted; fix
   within flow.py's changed function or the two test files only.

### Dependencies & Criticality

- **Depends on:** Steps 2, 3
- **Is Bottleneck:** YES (acceptance criteria are verified here; p_success_pred 0.92 ≥ 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** intent constraints (`uv run pytest` only, both lint tasks must pass), ruleset.md §3 (pytest as the
  standard runner).
- **Potential failure modes for this step:** full-suite failures in ledger/record tests if `dual_posted` semantics leak;
  lint failures from stale suppressions; docs-lint failures from docstring phrasing.
- **Guardrails and early‑abort checks:** targeted-first ordering catches most regressions cheaply; any failure outside
  the intent's three files that is not attributable to this change stops the step with a report instead of scope creep.

### Success & Discard Criteria

- **Success:** Targeted run green, full `uv run task test` green, `uv run task lint` and `uv run task lint-docs` both
  exit zero.
- **Discard:** Repeated full-suite failures traceable to the preserved-shape assumption being wrong (would require
  re-planning), or cost exceeding `1.5x` of `cost_pred`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.8   |
| impact_pred         | 20.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 16.16 |

### Step Metrics Rationale

Validation of already-aligned code and tests; the preserved return shape makes wide fallout unlikely. One full-suite run
plus two lint tasks dominates the cost.
`EV = 0.92 * 20.0 + 0.5 * 1.0 - 0.3 * 0.8 - 2.5 = 18.4 + 0.5 - 0.24 - 2.5 = 16.16`.

---

## Step 5: Commit and execution artefact

- **Sub‑intent recommendation:** NO
- **Reasoning:** Bookkeeping required by the git flow; trivial risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Commit the scoped change on the execution branch with a conventional message and record the execution
  artefact per the repo's flow; leave the PR merge to the human maintainer.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Confirm the diff touches only the three in-scope files (plus the execution artefact under `executions/` that the flow
   itself writes).
2. Commit with a conventional, Australian-English message describing the single-post fallback ladder, ending with the
   required attribution line (Co-Authored-By: Claude Fable 5).
3. Record predicted-versus-actual notes for calibration in the execution artefact; append-only ledger writes remain the
   flow's responsibility — never edit existing ledger lines.
4. Do not merge anything: raising the execution/plan merges follows the flow's Stage mechanics, and the intent-to-main
   merge stays with the human maintainer (safety.md Invariant 4).

### Dependencies & Criticality

- **Depends on:** Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** constraints.md §1 (commit boundaries), constraints.md §3 (append-only ledgers), safety.md
  Invariant 1 and 4.
- **Potential failure modes for this step:** accidental staging of out-of-scope files.
- **Guardrails and early‑abort checks:** explicit `git status`/diff review before committing; stage only the three files
  plus flow-generated artefacts.

### Success & Discard Criteria

- **Success:** Clean, scoped commit on the execution branch; working tree clean; artefact recorded.
- **Discard:** N/A (if earlier steps succeeded this step cannot meaningfully fail; a staging error is corrected in
  place).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.4   |
| impact_pred         | 10.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 8.83  |

### Step Metrics Rationale

Routine git bookkeeping under explicit commit-boundary rules.
`EV = 0.97 * 10.0 + 0.5 * 0.5 - 0.3 * 0.4 - 1.0 = 9.7 + 0.25 - 0.12 - 1.0 = 8.83`.

---

## Sub-intent list

None
