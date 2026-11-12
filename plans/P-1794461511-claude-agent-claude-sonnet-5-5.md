# Plan for I-1794461494-failnames

- **Plan ID:** P-1794461511-3cca99da-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-12T05:31:52.129Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** None.
- **Exploration stance:** conservative. The change is one small, well-bounded seam (`verification_error` and its single
  caller `_verify_before_push`), so a direct implementation with hermetic tests is the cheapest sound approach.
- **Safety priority level:** elevated
- **Priority Justification:** The kept and written output is raw test output that can carry secrets, so redaction must
  cover both the bounded text and the full file (`docs/safety.md` redaction rules; `world/constraints.md` on ledgers and
  git flow). Writing under `.holon/` must stay git-ignored and never reach a commit.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** The design is known. The only unknown (does the downstream halt reason re-truncate the message?) is
  settled by reading code in Step 1.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 3.2   |
| impact_pred         | 55.0  |
| cost_pred           | 10.0  |
| learning_value_pred | 4.0   |
| ev_pred             | 40.6  |

### Strategy Rationale

Findings from reading the code: `verification_error` (in `entrypoint/review_verification.py`) calls the supplied `bound`
callable, which is `_bounded_error_context` (redact, then keep the first 2000 characters). Downstream,
`resolver_error_text` and the host halt reason apply a further ~2000-character bound (the tail budget is half of it). So
the failed test names must sit at the front of the message and be short, not only appended after a head excerpt, or they
would be cut again. The design is therefore: a new focused module extracts failed ids from the redacted output, writes
the full redacted output to `.holon/`, and composes a message of the form
`Resolver pre-push test failed (exit code N); failed: <ids> (+K more); full output: <path>: <head ... tail>` within the
existing 2000-character bound. `.holon/` is already in `.gitignore`.

Aggregate: steps are sequential and every step is on the critical path, so overall `p_success_pred` is the product of
the step probabilities (`0.97 * 0.93 * 0.97 * 0.97 = 0.85`, rounded up to 0.88 because Steps 2 and 3 share one test
harness and failures there are correlated). Entropy is the sum of the step entropies (`0.4 + 1.2 + 0.8 + 0.6 + 0.2`).
Cost is the sum. EV uses `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S - Cost` with mu 0.5 and
lambda 0.3 as an assumption: `EV = 0.88 * 55.0 + 0.5 * 4.0 - 0.3 * 3.2 - 10.0 = 48.4 + 2.0 - 0.96 - 10.0 = 39.44`,
reported as about 40. The bottleneck is Step 2 (extraction robust to xdist output and ANSI).

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:** `docs/safety.md` (credential redaction);
  `world/constraints.md` (three append-only ledgers, branch discipline, human-only merge).
- **Potential violations or edge cases:** secret split across the head and tail cut; test ids containing secrets
  (parametrised ids); xdist `[gw3]` prefixed lines; ANSI colour codes; output with no FAILED lines (collection error,
  timeout); the `.holon/` write failing (read-only mount); message exceeding the downstream 2000-character bound; the
  file path being container-absolute rather than host-visible.
- **Mitigations built into the plan:** redact the whole output once, before extraction, truncation and file writing; the
  names block is capped by count and by characters; a failed file write degrades to a message noting it was not written,
  never an exception; the path is reported relative to the workspace (`.holon/...`) so it is target-native; no new
  ledger and no new dependency; the lint and missing-tool branches are untouched and test-pinned.
- **Residual risk accepted (and why):** a secret pattern unknown to `CredentialRedactor` would survive, as it does in
  every other Stage 4 record; reusing the canonical redactor is the project standard.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 3.2
- **Budget Compliance:** The strategy fits within budget.

## Plan Description & Strategy

Add a focused module `entrypoint/review_failure_detail.py` (so `review_verification.py` and `review_loop.py`, already
802 lines, do not grow much). It owns: failed-test-id extraction, bounded head-and-tail excerpting, and writing the full
redacted output file. `verification_error` gains an optional keyword argument for the workspace directory and uses the
module for the `test` step only. Lint and could-not-run paths keep byte-identical messages. `_verify_before_push` passes
`repo_dir` and supplies `PYTEST_ADDOPTS` with `-rf` appended when absent (the gate command stays `uv run task test`).
Tests go in a new `tests/test_review_failure_detail.py`. Docs get a short note in the testing or architecture doc. Order
of work: Step 1 read-only confirmation, Step 2 module, Step 3 wiring, Step 4 tests, Step 5 docs and full gates.

---

## Step 1: Confirm the truncation seams and the `.holon/` ignore rule

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only, minutes of work; it fixes the message budget the design depends on.
- **Step Type:** INFO_GATHERING
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Establish where the error text is re-bounded after `verification_error` and confirm `.holon/` is
  ignored, so the message layout survives to the halt message, stage result and checkpoint.
- **Git branch:** I-1794461494-failnames/P-1794461511-3cca99da-claude-agent-claude-sonnet-5-5/E-step1/\_ (or the plan
  branch)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Read `resolver_result.resolver_error_text`, `OUTPUT_TAIL_MAX_CHARS`, `flow/review/resolver.py` and the halt-reason
  builder to find every cap applied to the resolver error; record the effective character budget.
- Confirm `git check-ignore .holon/review/x` succeeds and that `_RESOLVER_PATHSPEC` staging cannot add `.holon/`.
- Check `run_cmd` for an `env` parameter; if absent, plan to use `os.environ` copy via a small wrapper or extend it.
- Check how existing tests fake `run_cmd` (`test_review_resolver_failure.py`) to reuse the pattern.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` (redaction), `world/constraints.md` section 1.
- **Potential failure modes for this step:** a further cap below 2000 characters would force a shorter names block.
- **Guardrails and early‑abort checks:** set the names-block cap from the measured budget, not the guess.

### Success & Discard Criteria

- **Success:** the effective budget and the env-passing approach are written into the Step 2 constants.
- **Discard:** not applicable (read-only).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.4   |
| impact_pred         | 5.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 4.6   |

### Step Metrics Rationale

Pure reading; low cost. `EV = 0.97 * 5.0 + 0.5 * 2.0 - 0.3 * 0.4 - 1.0 = 4.85 + 1.0 - 0.12 - 1.0 = 4.73`, about 4.6.

---

## Step 2: Add the failure-detail module

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, pure-function module with one filesystem write; testable in isolation.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Extract failed test ids, build a bounded head-and-tail excerpt, and write the full redacted output.
- **Git branch:** I-1794461494-failnames/P-1794461511-3cca99da-claude-agent-claude-sonnet-5-5/E-step2/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- New `entrypoint/review_failure_detail.py`, no imports from the host flow engine; redaction is passed in or imported
  from `sandbox_executor.redaction` (already used by `review_loop.py`).
- Redact the full output once first (`redact_agent_secrets` plus the canonical redactor if they differ; check which one
  `_bounded_error_context` uses, `CredentialRedactor.redact_text`).
- Extract ids: strip ANSI escapes; match lines starting `FAILED ` or `ERROR ` in the short test summary
  (`FAILED tests/x.py::test_a - message`), keep the node id up to the first `-`; also accept `[gwN]` prefixed forms;
  de-duplicate preserving order. Handle the case with no matches (empty list).
- Format the names block as `failed: id1, id2` capped at a stated maximum count (default 10) and a character cap (about
  600), appending `(+K more)` for omitted ids.
- Excerpt: when the redacted output exceeds the remaining budget, keep a short head (session header lines) and the tail
  (summary), joined by an omission marker with the count of omitted characters.
- Write the full redacted output to `<repo_dir>/.holon/review/verification/<step>-<timestamp-free name>.log` (a fixed
  name per step, overwritten each time, to avoid clutter and keep it deterministic); create the directory with
  `os.makedirs(exist_ok=True)`; catch `OSError` and return `None`. Build paths with `os.path.join` (target-native);
  report the path relative to the workspace root with forward slashes avoided in favour of `os.path` output.
- Keep each function under the ruff complexity limits.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (extraction correctness and redaction order decide acceptance criteria 1, 2 and 4)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` redaction; `world/constraints.md` ledgers (no new ledger file).
- **Potential failure modes for this step:** regex misses xdist or ANSI forms; redact-after-truncate leak; unbounded
  names block; write failure raising.
- **Guardrails and early‑abort checks:** redact before every other operation; every cap has a unit test; the write is
  wrapped so it cannot raise.

### Success & Discard Criteria

- **Success:** unit-level behaviour passes for 3600-line output with 40 FAILED lines, ANSI and xdist forms.
- **Discard:** if extraction needs more than regex over summary lines (for example JUnit XML), stop and fall back to
  tail-only, which still satisfies goal 1.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 1.2   |
| impact_pred         | 35.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 30.2  |

### Step Metrics Rationale

Core of the value. `EV = 0.93 * 35.0 + 0.5 * 3.0 - 0.3 * 1.2 - 3.0 = 32.55 + 1.5 - 0.36 - 3.0 = 30.69`, about 30.2.

---

## Step 3: Wire it into `verification_error` and `_verify_before_push`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Two small edits; behaviour for lint and missing tool must stay identical.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Use the module for the failed test step, name the failures and the file in the message, and make
  pytest print `-rf`.
- **Git branch:** I-1794461494-failnames/P-1794461511-3cca99da-claude-agent-claude-sonnet-5-5/E-step3/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Extend `verification_error` with an optional `workspace` keyword (keeps the three-parameter call compatible, so
  `UV_MISSING_ERROR` in existing tests is unchanged). For `step == "test"` with a real failure, return
  `Resolver pre-push test failed (exit code N); failed: ...; full output: <path>: <excerpt>`; the prefix stays exactly
  `Resolver pre-push test failed (exit code N)` so existing assertions hold. Lint and the could-not-run branch keep the
  existing code path.
- In `_verify_before_push` pass `workspace=repo_dir`, and run the test command with an env where `PYTEST_ADDOPTS` gains
  `-rf` unless it already contains `-r` with `f`, `a`, or `A`. Confirm `run_cmd` accepts `env`; if not, add a minimal
  optional parameter there (keeping the redacted echo).
- Ensure the whole message stays at most the 2000-character bound so the names survive into the stage result, checkpoint
  and halt message (budget from Step 1).

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `world/constraints.md` section 1 (nothing committed: `.holon/` ignored); `docs/safety.md`.
- **Potential failure modes for this step:** changed signature breaks callers; `PYTEST_ADDOPTS` clobbered; message
  re-truncated downstream.
- **Guardrails and early‑abort checks:** run `uv run pytest apps/sandbox-executor/tests/test_review_resolver_failure.py`
  first; `git status` after a failing run shows no `.holon/` change.

### Success & Discard Criteria

- **Success:** existing resolver-failure tests pass unchanged; the message shows names and path.
- **Discard:** if `run_cmd` env plumbing balloons, append `-rf` by exporting in `os.environ` for the call only.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.8   |
| impact_pred         | 15.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 12.5  |

### Step Metrics Rationale

`EV = 0.97 * 15.0 + 0.5 * 1.0 - 0.3 * 0.8 - 2.0 = 14.55 + 0.5 - 0.24 - 2.0 = 12.81`, about 12.5.

---

## Step 4: Hermetic tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** Fakes already exist; one new test file.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Pin every acceptance criterion with a hermetic test.
- **Git branch:** I-1794461494-failnames/P-1794461511-3cca99da-claude-agent-claude-sonnet-5-5/E-step4/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- New `tests/test_review_failure_detail.py` (keeps `test_review_resolver_failure.py` from growing).
- Fake `run_cmd` returns lint success, then a failing test result with output far over 2000 characters: a session
  header, thousands of progress dots, a planted fake token (using the project's `leak_assertions.py` helper and a known
  secret pattern) in the middle and in the summary, then `FAILED` lines for more ids than the cap.
- Assert: halt message and the `ResolverInput` stage result (through `_run_resolver_mode` with the result file) contain
  the first N ids and `(+K more)`; message length within the bound; the output file exists under `tmp/.holon/`, holds
  the whole output, and no secret survives in either the message or the file; the path is named.
- Assert `git check-ignore` holds for the written path in a temporary repository.
- Assert a lint failure yields exactly the prior message and writes no file; a missing `uv` yields exactly
  `UV_MISSING_ERROR`-style text; an unwritable `.holon` degrades gracefully.
- Assert `PYTEST_ADDOPTS` gains `-rf`, and is left alone when `-rA` is present.
- Run
  `uv run pytest apps/sandbox-executor/tests/test_review_failure_detail.py apps/sandbox-executor/tests/test_review_resolver_failure.py`
  (no repetition: no timing concern).

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/hermetic_testing.md` (no network, no real uv; temporary directories only).
- **Potential failure modes for this step:** test writes outside `tmp_path`; real secrets in fixtures.
- **Guardrails and early‑abort checks:** use synthetic token strings; the sandbox hermetic guard test must stay green.

### Success & Discard Criteria

- **Success:** new and neighbouring tests pass.
- **Discard:** none; failures are fixed in Steps 2 or 3.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.6   |
| impact_pred         | 20.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 17.0  |

### Step Metrics Rationale

`EV = 0.97 * 20.0 + 0.5 * 2.0 - 0.3 * 0.6 - 3.0 = 19.4 + 1.0 - 0.18 - 3.0 = 17.22`, about 17.0.

---

## Step 5: Documentation and full gates

- **Sub‑intent recommendation:** NO
- **Reasoning:** Short doc edit, then the standard gate run.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Document the halt message layout and the `.holon/review/verification/` file, then run all gates once.
- **Git branch:** I-1794461494-failnames/P-1794461511-3cca99da-claude-agent-claude-sonnet-5-5/E-step5/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add a short Australian-English paragraph to the Stage 4 section of `docs/architecture.md` or
  `docs/testing/testing_guide.md` (whichever already describes pre-push verification): message layout, caps, file
  location, redaction, git-ignored.
- Run `uv run pytest` on the affected tests first, then the full suite once with `uv run task test`, then
  `uv run task lint` and `uv run task lint-docs` (prettier wraps at 120 columns). Check
  `scripts/check_flow_module_budgets.py` still passes (new module sits under `entrypoint/`, not the flow engine).
- Confirm no ledger files changed and `git status` is clean of `.holon/`.

### Dependencies & Criticality

- **Depends on:** Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `world/constraints.md` (ledgers untouched; merge stays human).
- **Potential failure modes for this step:** prettier reflow of the new doc text; the full suite flaking under xdist.
- **Guardrails and early‑abort checks:** run prettier locally before the gate; investigate any flake by name rather than
  re-running blindly.

### Success & Discard Criteria

- **Success:** all four commands pass.
- **Discard:** none.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.2   |
| impact_pred         | 5.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 4.0   |

### Step Metrics Rationale

`EV = 0.97 * 5.0 + 0.5 * 0.5 - 0.3 * 0.2 - 1.0 = 4.85 + 0.25 - 0.06 - 1.0 = 4.04`, about 4.0.

---

## Sub-intent list

None
