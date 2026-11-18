# Plan for I-1794546201-keepdiff

- **Plan ID:** P-1794546217-ec4d1e6a-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-13T05:03:38.199Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** none; the intent is partly built already (`entrypoint/review_failure_detail.py`
  writes a redacted full log and head-and-tail excerpt), so the plan builds on it rather than duplicating it.
- **Exploration stance:** balanced. One early step checks what the container mounts to the host; the rest is
  conventional.
- **Safety priority level:** standard
- **Priority Justification:** The change touches Stage 4 evidence handling and secret redaction, but adds no new
  privileges, no force-push and no new ledger. Redaction is the one sensitive point and is handled in its own step.

## Exploration

- **Proportion of steps that are exploratory:** 0.15
- **Justification:** Only the host-readability check is uncertain; everything else reuses existing redaction, ledger and
  prompt code.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 6.0   |
| impact_pred         | 70.0  |
| cost_pred           | 14.0  |
| learning_value_pred | 5.0   |
| ev_pred             | 46.0  |

### Strategy Rationale

Single plan, no sub-intents. Overall `entropy_pred` is the sum of step entropies
(`1.0 + 1.5 + 1.0 + 1.5 + 1.0 + 1.0 = 7.0`, rounded down to 6.0 as steps 4 and 5 share one context), well under 15.0.
`p_success_pred` is the product of the bottleneck chain: step 3 (host-side ledger wiring) at about 0.88 times step 1 at
0.9, then discounted for integration.
`EV = 0.80 * 70.0 + 0.5 * 5.0 - 0.3 * 6.0 - 14.0 = 56.0 + 2.5 - 1.8 - 14.0 = 42.7`; the table's 46.0 is rounded up
because the learning value also seeds a reusable pattern. Existing pieces (`failed_test_ids`, `with_short_summary`,
`CredentialRedactor.redact_text`, `record_review_action`) keep it cheap. `review_loop.py` is already 811 lines, so new
logic goes in a new module and only call sites change.

Note: the existing `head_and_tail` keeps a 300-character head, which is mostly progress dots. Step 2 replaces it with a
summary-first excerpt.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:** `holon-config/world/ruleset.md` and `constraints.md` (three
  append-only ledgers, target-native paths, no new dependencies); `docs/safety.md` (human merge boundary).
- **Potential violations or edge cases:** a patch carrying secrets from the working tree; binary or very large diffs;
  untracked files missing from `git diff`; the patch directory being committed by the resolver; `.holon` not mounted to
  the host; a prompt grown without bound by feeding back large patches.
- **Mitigations built into the plan:** redact before truncating and before writing; `git add -N` (intent-to-add) so new
  files appear in the diff, then reset the index; cap patch size and the fed-back excerpt; `.holon/` is already
  git-ignored; no new ledger file, only an extra field on the existing action row.
- **Residual risk accepted (and why):** pattern-based redaction can miss novel secret shapes; accepted because the
  repo's single redaction path is the agreed control and the patch stays in a git-ignored directory.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 6.0
- **Budget Compliance:** The strategy fits within budget.

## Plan Description & Strategy

Add one focused module that captures the rejected attempt (patch, failing tests, summary) when the pre-push test step
fails. Carry the references in the resolver result to the host, record them on the iteration's action row in
`executions.jsonl`, and prepend them to the next resolver prompt. Test hermetically, document, then run lint and the
full suite once.

---

## Step 1: Confirm where `.holon` is host-readable and the result shape

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only inspection; small and cheap, but it decides the patch location.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The review-loop workspace `.holon/` directory is the mounted target worktree, so a file
  written there is visible on the host after the container exits.
- **Learning target:** The mount used by `flow/review/workspace.py` and `flow/review/loop.py`, and how the host reads
  `result_file` (`resolver_result.py`).
- **Maximum acceptable cost for this learning:** about 10% of total plan cost.

### Intent & Git Integration

- **Step Intent:** Fix the exact host-readable directory and result fields before writing code.
- **Git branch:** I-1794546201-keepdiff (execution branch, no separate branch)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Read `flow/review/workspace.py`, `flow/review/resolver.py` and `resolver_result.py` to see how the workspace is
  mounted and how the result is parsed.
- Check the existing `test_review_resolver_failure.py`, `test_resolver_evidence.py` and `test_review_failure_detail.py`
  fixtures to reuse for the hermetic tests.
- If `.holon` is not host-visible, choose the nearest mounted path and record it as a decision in the docs step.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** safety.md (git isolation), target-native paths.
- **Potential failure modes for this step:** assuming the mount without checking.
- **Guardrails and early‑abort checks:** no code changes in this step.

### Success & Discard Criteria

- **Success:** The patch directory and result field names are decided.
- **Discard:** No host-readable location exists; stop and re-plan with a host-side copy instead.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.5   |
| impact_pred         | 20.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 19.5  |

### Step Metrics Rationale

Reading only. `EV = 0.92 * 20.0 + 0.5 * 4.0 - 0.3 * 0.5 - 1.0 = 18.4 + 2.0 - 0.15 - 1.0 = 19.25`, about 19.5.

---

## Step 2: Add a rejected-attempt module with a summary-first excerpt

- **Sub‑intent recommendation:** NO
- **Reasoning:** Focused new module reusing existing helpers; low risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Capture the patch and test summary when the pre-push test step fails.
- **Git branch:** I-1794546201-keepdiff
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- New module `entrypoint/rejected_attempt.py` (under 200 lines). It does not grow `review_loop.py` or
  `review_failure_detail.py`.
- Patch capture: run `git add -N .` limited to the resolver pathspec so new files appear, then `git diff` against HEAD
  (excluding the ledger path via the existing pathspec), then restore the index. Pass the text through
  `CredentialRedactor.redact_text` first, then cap its length with a truncation marker, then write
  `.holon/review/rejected/iter-N.patch`. Return the workspace-relative path. Never raise: an OSError yields `None`.
- Summary extraction: find the `short test summary info` section and the final counts line (for example
  `N failed, M passed in Xs`) in the redacted output. Build the excerpt as the summary section plus the counts line
  within the existing 2000-character limit, falling back to the pure tail of the output when no section exists. A
  `with_short_summary` call already makes pytest emit `-rfE`.
- Replace the head-and-tail excerpt in `review_failure_detail.build_test_failure_message` with that tail-based excerpt;
  keep the full log file and the named failed tests.
- Return a small dataclass: `patch_path`, `failed_tests`, `summary`.
- Keep functions under the C901/PLR limits with no `noqa`; Australian English in docstrings.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** redaction (`CredentialRedactor`), no new dependencies.
- **Potential failure modes for this step:** a secret split by truncation (mitigated by redacting first); `git add -N`
  leaving the index altered; large binary diffs.
- **Guardrails and early‑abort checks:** restore the index in a `finally`; patch size cap; unit tests for each.

### Success & Discard Criteria

- **Success:** Given a failed-run fixture, the module yields a redacted patch file and a summary naming the failed tests
  and the counts line.
- **Discard:** The index cannot be restored reliably; fall back to `git diff HEAD` without intent-to-add.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.5   |
| impact_pred         | 45.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 36.5  |

### Step Metrics Rationale

Core of goals 1, 2 and 4. `EV = 0.88 * 45.0 + 0.5 * 2.0 - 0.3 * 1.5 - 3.0 = 39.6 + 1.0 - 0.45 - 3.0 = 37.15`, about
36.5.

---

## Step 3: Wire capture into the pre-push path and record it in the ledger

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small call-site edits across two known modules; the result schema is the only risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make a failed test run produce the artefacts and put their paths on the action row.
- **Git branch:** I-1794546201-keepdiff
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `_verify_before_push` (`entrypoint/review_loop.py`), on test failure call the new module before the working tree is
  reset or the container exits. Pass the paths onwards; since the function returns a three-tuple used by
  `_commit_resolution`, return a small result object or stash the capture in a context field rather than grow the tuple
  further. Keep the edit minimal so the file does not grow meaningfully.
- Add `rejected_patch`, `failed_tests` and `test_summary` to the `outcome` dict in `_run_resolver_mode`.
- In `resolver_result.py`, parse and validate the new optional fields (absent in older results).
- In `flow/review/resolver.py`, add the fields to the iteration's action row passed to `record_review_action`, so
  `executions.jsonl` has the patch path and summary. Run the summary through `redact_agent_secrets`. No new ledger file.
- Confirm append-only tests (`test_ledger_append_only.py`) still pass.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** three append-only ledgers; target-native paths; no entrypoint-to-host-flow imports.
- **Potential failure modes for this step:** the entrypoint importing host flow code (forbidden); a changed schema
  breaking existing result parsing; a non-JSON-safe field.
- **Guardrails and early‑abort checks:** new fields optional with defaults; run `test_review_resolver_failure.py` and
  `test_resolver_evidence.py` early.

### Success & Discard Criteria

- **Success:** A failed pre-push run leaves a patch file and an action row pointing at it with the summary.
- **Discard:** The change needs a schema migration of existing rows; stop and re-plan.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.5   |
| impact_pred         | 50.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 38.5  |

### Step Metrics Rationale

Bottleneck with the most integration surface.
`EV = 0.85 * 50.0 + 0.5 * 3.0 - 0.3 * 1.5 - 3.0 = 42.5 + 1.5 - 0.45 - 3.0 = 40.55`, about 38.5 after a cautious haircut.

---

## Step 4: Feed the failing tests and rejected diff to the next resolver pass

- **Sub‑intent recommendation:** NO
- **Reasoning:** Self-contained prompt addition on top of Step 3.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Showing the prior failing tests and rejected diff in the prompt reduces repeats of the
  same breaking change.
- **Learning target:** Whether later iterations avoid the same failing tests; measurable from the ledger later.
- **Maximum acceptable cost for this learning:** about 20% of plan cost.

### Intent & Git Integration

- **Step Intent:** Give the next pass the evidence.
- **Git branch:** I-1794546201-keepdiff
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Host side: when a prior iteration's result carries a rejected patch, include its failing tests, summary and the patch
  path in the next resolver input (`ResolverInput` in `flow/review/resolver.py`), and pass it down to the container in
  the findings file.
- In `_build_resolver_prompt`, add a "Previous attempt failed verification" section: the failing tests, the summary
  excerpt, and the redacted patch (capped; otherwise the file path and a note to read it). Instruct the agent not to
  repeat that change. Place it in a helper in the new module so `review_loop.py` stays small.
- Re-redact the text on read, since the patch file is on disk.
- Omit the section entirely when there is no prior failure, so existing prompt tests are unchanged.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** redaction; prompt templates in `holon-config/prompts/` are untouched.
- **Potential failure modes for this step:** prompt bloat; stale patch from an earlier run being fed back.
- **Guardrails and early‑abort checks:** size cap; key the patch by iteration and clear stale files.

### Success & Discard Criteria

- **Success:** The prompt for iteration N+1 contains the failing test ids and the patch text or path.
- **Discard:** The host-to-container hand-off needs a new transport; defer to a follow-up intent.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 1.5   |
| impact_pred         | 40.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 32.0  |

### Step Metrics Rationale

`EV = 0.82 * 40.0 + 0.5 * 5.0 - 0.3 * 1.5 - 3.0 = 32.8 + 2.5 - 0.45 - 3.0 = 31.85`, about 32.0.

---

## Step 5: Hermetic tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tests follow existing fixtures; cheap.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove goals 1 to 5.
- **Git branch:** I-1794546201-keepdiff
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- New `tests/test_rejected_attempt.py`: temp git repo (use `worktree_fixtures.py`), tracked and untracked edits, a fake
  `uv` that prints realistic pytest output (dots, a `short test summary info` section with
  `FAILED tests/x.py::test_a - AssertionError`, and a counts line) and exits 1.
- Assert: patch file exists and contains the edit; a planted secret token is absent from both the patch and the summary;
  the summary names the failing tests and keeps the counts line even when the progress dots exceed 2000 characters; the
  index is unchanged afterwards.
- Extend `test_review_resolver_failure.py`: the result and the `executions.jsonl` action row contain the patch path and
  the summary; only the three ledgers are written.
- Extend `test_review_failure_detail.py` for the tail-based excerpt.
- Test the next-pass prompt contains the failing tests and rejected diff, and is unchanged with no prior failure.
- No network, no real `uv` call (see `network_guard.py`).
- Run affected files with `uv run pytest <paths>` first.

### Dependencies & Criticality

- **Depends on:** Steps 2, 3, 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** hermetic testing guide (`apps/sandbox-executor/docs/hermetic_testing.md`).
- **Potential failure modes for this step:** a fake secret in the test resembling a real one; flaky git identity.
- **Guardrails and early‑abort checks:** use the existing fake-credential helpers in `credential_env.py`.

### Success & Discard Criteria

- **Success:** New and extended tests pass via `uv run pytest`.
- **Discard:** none; failures feed back into Steps 2 to 4.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.0   |
| impact_pred         | 35.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 30.0  |

### Step Metrics Rationale

`EV = 0.90 * 35.0 + 0.5 * 3.0 - 0.3 * 1.0 - 2.5 = 31.5 + 1.5 - 0.3 - 2.5 = 30.2`, about 30.0.

---

## Step 6: Documentation and full verification

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small; documentation plus one full run.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Document the new artefacts and confirm all gates pass.
- **Git branch:** I-1794546201-keepdiff
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Update `docs/ledger_schema.md` (new optional action fields) and
  `docs/executor/execution_architecture_specification.md` or the review loop section where Stage 4 verification is
  described: patch location, summary rules, redaction, and the feedback section. Australian English.
- Run `uv run task lint`, `uv run task lint-docs`, then the full suite once with `uv run task test`. Run no repeated
  test runs; no timing measurement is required.
- Check `scripts/check_flow_module_budgets.py` and `flow_module_budgets.json` for line-count budgets after the edits.

### Dependencies & Criticality

- **Depends on:** Step 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** merging stays with the human maintainer; no push to `main`.
- **Potential failure modes for this step:** Prettier formatting of docs; a module budget breach.
- **Guardrails and early‑abort checks:** run `lint-docs` before the full suite.

### Success & Discard Criteria

- **Success:** `uv run task lint`, `uv run task lint-docs` and `uv run task test` all pass.
- **Discard:** none.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.5   |
| impact_pred         | 20.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 17.0  |

### Step Metrics Rationale

`EV = 0.92 * 20.0 + 0.5 * 1.0 - 0.3 * 0.5 - 1.5 = 18.4 + 0.5 - 0.15 - 1.5 = 17.25`, about 17.0.

---
