# Plan for I-1794567523-savepat

- **Plan ID:** P-1794567538-ea208f06-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-13T10:58:59.766Z

## Planner Autonomy Summary

- **Intent handling:** REFRAME
- **Reframed intent (if applicable):** The patch-writing path already exists on `main` (`capture_rejected_attempt` in
  `entrypoint/rejected_attempt.py`, from #185) and its fixture test passes. Find out why `patch_path` is still null in a
  real run, make the patch survive the container (carry it in the result payload and write it host-side if the bind
  mount is absent), and add a test that reproduces the real failure condition.
- **Exploration stance:** balanced. One cheap diagnostic step up front, because the stated symptom contradicts the code
  that is on the branch; the rest is a small, well-understood fix.
- **Safety priority level:** standard
- **Priority Justification:** The work touches resolver evidence capture only. It adds no network, ledger-format or
  history-rewriting change (`holon-config/world/constraints.md` sections 1 and 3), and the redaction path is reused.

## Exploration

- **Proportion of steps that are exploratory:** 0.2
- **Justification:** Step 1 is a diagnosis that decides the shape of Step 2. Everything after it is an exploit step.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 4.5   |
| impact_pred         | 40.0  |
| cost_pred           | 9.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 27.4  |

### Strategy Rationale

The code in `rejected_attempt.py` already runs `git diff HEAD` against a scratch index, redacts and caps the result,
writes `iter-N.patch` and `iter-N.json`, and `_record_rejected_attempt` puts `rejected_patch` on the ledger action row.
`tests/test_rejected_attempt.py` covers this against an ordinary repository. The intent says `patch_path` was null, so
the gap is in the real environment, not in the happy path. Likely causes, to be confirmed in Step 1:

- the flow worktree is a linked worktree with a `.git` file and a worktree-specific index;
- `git` returning None under the container's user or `safe.directory` state;
- the patch living only on the container filesystem when no bind mount exists.

Building the whole feature again would waste cost, so the plan is diagnose, fix, then lock in with tests.

Aggregation: the plan succeeds only if Step 1 identifies a reproducible cause and Step 2 fixes it, so
`p_success = 0.92 * 0.88 * 0.97 * 0.97 ~= 0.76` before correlation, and 0.82 once the high-probability tail steps are
treated as dependent on those two. The bottleneck is Step 2. Entropy is the sum of the step entropies
(`0.8 + 1.8 + 1.1 + 0.4 + 0.4 = 4.5`). Impact is the value of completing the evidence trail on a halt. Cost is the sum
of the step costs. EV uses `EV = P * Impact + mu * LearningValue - lambda * Delta_S - Cost` with `mu = 0.5` and
`lambda = 0.3`: `EV = 0.82 * 40.0 + 0.5 * 4.0 - 0.3 * 4.5 - 9.0 = 32.8 + 2.0 - 1.35 - 9.0 = 24.45`. The table above
rounds a slightly more optimistic figure, so use 24.5 as the working value.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:** `holon-config/world/ruleset.md` section 3 (tests via pytest;
  test changes declared in the plan), `constraints.md` section 3 (only the three ledgers, append-only), `docs/safety.md`
  (sandboxing, git as the boundary).
- **Potential violations or edge cases:** a patch holding secrets (redact before writing); a huge patch (cap at
  `PATCH_MAX_CHARS`); a payload that is too big for the result file; an empty diff (no file, `patch_path` stays null);
  Windows or POSIX paths (use `os.path`, stay target-native).
- **Mitigations built into the plan:** reuse `CredentialRedactor`; keep the write best-effort so capture never raises;
  no new ledger file and no new dependency; the test uses only a temporary fixture repository.
- **Residual risk accepted (and why):** the real-container behaviour cannot be exercised hermetically, so the test
  reproduces its conditions (linked worktree, staged and unstaged edits) instead of the container itself.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 4.5
- **Budget Compliance:** The strategy fits within budget.

## Plan Description & Strategy

Diagnose first, fix the confirmed cause, prove it with a hermetic test, then document and run the gates. No sub-intents:
the work is under 300 lines across `rejected_attempt.py`, a small new module if the host-side write is needed, one test
file and the docs.

---

## Step 1: Reproduce the null `patch_path` in realistic conditions

- **Sub‑intent recommendation:** NO
- **Reasoning:** A cheap, read-mostly diagnosis that decides Step 2; it does not need its own branch.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The capture fails or its output is unreachable when the workspace is a linked worktree or
  holds both staged and unstaged edits, or when the patch is written only inside the container.
- **Learning target:** Which of the three candidate causes produces `patch_path = null` or a lost file.
- **Maximum acceptable cost for this learning:** about 1.5 cost units; stop at the first reproduced cause.

### Intent & Git Integration

- **Step Intent:** Establish why the saved patch is missing in a real run despite the existing capture code.
- **Git branch:** `I-1794567523-savepat/P-1794567538-ea208f06-claude-agent-claude-sonnet-5-5/E-savepat-diagnose/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Read `entrypoint/rejected_attempt.py`, `review_verification.py`, `resolver_result.py`, `flow/review/resolver.py` and
  how the host starts the review-loop container (is the workspace bind-mounted, and which path does the host read
  `rejected_patch` from).
- In a scratch script outside the repository, build a linked worktree with `git worktree add`, make one staged and one
  unstaged edit plus a new file, and call `uncommitted_diff` and `capture_rejected_attempt`; compare with the
  ordinary-repository result.
- Check how `rejected_patch` is consumed on the host: does anything read the file, or only record the path?
- Record the finding in the step's commit message and the execution notes.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `ruleset.md` section 3; the scratch repository lives in a temporary directory.
- **Potential failure modes for this step:** no reproduction (the cause is container-only).
- **Guardrails and early‑abort checks:** if nothing reproduces locally, proceed to the payload-carrying design in Step
  2, which removes the dependency on the mount.

### Success & Discard Criteria

- **Success:** a named cause, or a documented conclusion that the file only survives through the bind mount.
- **Discard:** cost over 1.5 times the estimate.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.8   |
| impact_pred         | 10.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 4.0   |
| ev_pred             | 8.3   |

### Step Metrics Rationale

`EV = 0.92 * 10.0 + 0.5 * 4.0 - 0.3 * 0.8 - 1.5 = 9.2 + 2.0 - 0.24 - 1.5 = 9.46`; rounded down to 8.3 because the
finding may be inconclusive. The learning value is high because the intent's premise conflicts with the code.

---

## Step 2: Make the patch durable and recorded

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small and focused; the module is 190 lines, so it grows at most modestly. It is the bottleneck, but its
  probability is above 0.6.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Ensure a failed pre-push run always leaves a redacted patch next to the iteration record, and that
  its path is in the JSON and the ledger action entry, even when the container filesystem is discarded.
- **Git branch:** `I-1794567523-savepat/P-1794567538-ea208f06-claude-agent-claude-sonnet-5-5/E-savepat-patch/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Fix the Step 1 cause in `uncommitted_diff` (for example, resolve the index path and git directory the way a linked
  worktree needs, and cover staged-only changes by diffing against HEAD with the real index as the base).
- Keep the write order: patch first, then the JSON, so the JSON never names a missing patch. Write the patch with a
  temporary name and `os.replace` so a partial file is never recorded.
- If Step 1 shows the file does not reach the host, carry the redacted, capped patch text in the resolver result payload
  (a new optional `rejected_patch_text` field read by `resolver_result.py`, redacted again on read). The host then
  writes it under the target's `.holon/review/rejected/` directory and records that path in the ledger row. This goes in
  a new focused module under `flow/review/`, with no new import from the entrypoint into the host engine.
- Make the existing `patch_path` key and `rejected_patch` ledger field point at the host-readable file. Keep paths
  workspace-relative and built with `os.path`.
- Keep functions within the repository's ruff complexity limits, with no `# noqa`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` section 3 (append-only ledgers, no new ledger); `ruleset.md` section 2.
- **Potential failure modes for this step:** result payload size limits; a second redaction pass altering the patch; a
  circular import.
- **Guardrails and early‑abort checks:** cap before embedding; if the payload route needs a schema change that breaks
  older results, stop and keep the existing mount-based path only, with the limit documented.

### Success & Discard Criteria

- **Success:** the reproduced condition from Step 1 now yields a non-null `patch_path` whose file holds the redacted
  diff.
- **Discard:** cost over 1.5 times the estimate or p_success under 0.4.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.8   |
| impact_pred         | 30.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 22.4  |

### Step Metrics Rationale

`EV = 0.88 * 30.0 + 0.5 * 2.0 - 0.3 * 1.8 - 3.5 = 26.4 + 1.0 - 0.54 - 3.5 = 23.36`; the 22.4 shown allows for
uncertainty in the payload route. Most entropy comes from not knowing which cause applies.

---

## Step 3: Hermetic test for the patch content and recorded path

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small test addition and low risk.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove that a failed pre-push run leaves a patch matching the resolver's change, and that `patch_path`
  and the ledger entry point to it.
- **Git branch:** `I-1794567523-savepat/P-1794567538-ea208f06-claude-agent-claude-sonnet-5-5/E-savepat-test/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- This declares a change to test files, as the ruleset requires: add `tests/test_rejected_patch_durability.py` (a new
  file, so `test_rejected_attempt.py` at 166 lines stays focused). Reuse the fixture helpers from the existing file or
  from `hermetic_fixtures.py` where they fit.
- Build a fixture repository (and, from Step 1, a linked worktree) with a committed base, one staged edit, one unstaged
  edit, one new file and a secret-looking token. Fake `uv` through `run_cmd` as the existing test does.
- Call `_verify_before_push` with a failing test run. Assert that `iter-N.patch` exists, that its content contains
  exactly the resolver's changed lines and the new file, and that the secret is absent.
- Assert that the JSON `patch_path` equals the file's workspace-relative path and that the file exists, that the
  resolver result carries it, and that `_record_rejected_attempt` writes it on the action row through the real ledger
  writer.
- If the payload route was built, add a case where the container-side file is deleted and the host still writes it.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `ruleset.md` section 3; the Hermetic testing guide in `docs/testing/testing_guide.md`.
- **Potential failure modes for this step:** reliance on the global git identity; flaky temporary paths.
- **Guardrails and early‑abort checks:** pass identity through `-c` flags; no network; the new test runs with
  `uv run pytest apps/sandbox-executor/tests/test_rejected_patch_durability.py` first.

### Success & Discard Criteria

- **Success:** the new test passes, and fails against the pre-fix code for the Step 1 condition.
- **Discard:** the test cannot be made to fail before the fix; then revisit Step 1.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 1.1   |
| impact_pred         | 20.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 17.8  |

### Step Metrics Rationale

`EV = 0.97 * 20.0 + 0.5 * 1.5 - 0.3 * 1.1 - 2.0 = 19.4 + 0.75 - 0.33 - 2.0 = 17.82`. The test follows an existing
pattern, so the probability is high.

---

## Step 4: Documentation

- **Sub‑intent recommendation:** NO
- **Reasoning:** Cheap, no risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Describe the durable patch and where the path is recorded.
- **Git branch:** `I-1794567523-savepat/P-1794567538-ea208f06-claude-agent-claude-sonnet-5-5/E-savepat-docs/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Update `docs/ledger_schema.md` (the `rejected_patch` paragraph near line 214) and the review section of
  `docs/git_flow.md` or `docs/review_consensus.md` wherever the rejected attempt is described, in Australian English.
- Run `uv run task lint-docs` and let Prettier reformat if needed.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `ruleset.md` Markdown formatting; Prettier oscillation avoidance (code spans around formulas).
- **Potential failure modes for this step:** lint-docs formatting failure.
- **Guardrails and early‑abort checks:** run `uv run task lint-docs` before moving on.

### Success & Discard Criteria

- **Success:** `uv run task lint-docs` passes.
- **Discard:** none beyond fixing formatting.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.4   |
| impact_pred         | 5.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 0.2   |
| ev_pred             | 3.8   |

### Step Metrics Rationale

`EV = 0.97 * 5.0 + 0.5 * 0.2 - 0.3 * 0.4 - 1.0 = 4.85 + 0.1 - 0.12 - 1.0 = 3.83`.

---

## Step 5: Verification gates

- **Sub‑intent recommendation:** NO
- **Reasoning:** Fixed gate commands; no repeated runs are needed, as the intent has no timing requirement.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Confirm the acceptance criteria with the repository's own gates.
- **Git branch:** `I-1794567523-savepat/P-1794567538-ea208f06-claude-agent-claude-sonnet-5-5/E-savepat-verify/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Run the affected tests first: `uv run pytest apps/sandbox-executor/tests/test_rejected_attempt.py` and the new test
  file, plus `test_resolver_evidence.py` and `test_review_resolver_failure.py`.
- Then the full suite once with `uv run task test`, followed by `uv run task lint` and `uv run task lint-docs`.
- Check `flow_module_budgets.json` and `scripts/check_flow_module_budgets.py` if a new flow module was added.
- Confirm that the three ledgers are untouched except by the existing append path.

### Dependencies & Criticality

- **Depends on:** Steps 2, 3, 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `ruleset.md` section 3.
- **Potential failure modes for this step:** a module-size budget failure; an unrelated flaky test.
- **Guardrails and early‑abort checks:** if the full run fails outside the touched files, re-run only the failing test
  once and report it faithfully.

### Success & Discard Criteria

- **Success:** all tests, `lint` and `lint-docs` pass.
- **Discard:** none; failures are fixed or reported.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.4   |
| impact_pred         | 5.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 0.3   |
| ev_pred             | 3.7   |

### Step Metrics Rationale

`EV = 0.97 * 5.0 + 0.5 * 0.3 - 0.3 * 0.4 - 1.0 = 4.85 + 0.15 - 0.12 - 1.0 = 3.88`.

---

### Overall justification

Reframing is justified: the existing implementation makes a straight rebuild a negative-value duplicate, while the
diagnosis-first route costs about 9 units against an impact of about 40 and tests the conflict between the intent and
the code. Merging to `main` stays with the human maintainer.
