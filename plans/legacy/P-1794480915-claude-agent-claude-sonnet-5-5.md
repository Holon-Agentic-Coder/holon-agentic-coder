# Plan for I-1794480898-mdgate

- **Plan ID:** P-1794480915-c8cd81b2-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-12T10:55:17.321Z

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 8.0   |
| impact_pred         | 60.0  |
| cost_pred           | 20.0  |
| learning_value_pred | 5.0   |
| ev_pred             | 29.9  |

### Strategy Rationale

The intent is accepted as is. It is well scoped, the failure is real and repeated, and the fix is a small, hermetically
testable addition to the resolver's pre-push gate. Findings from reading the repository:

- `_verify_before_push` in `entrypoint/review_loop.py` (805 lines already) runs `uv run task lint` then
  `uv run task test`. `entrypoint/review_verification.py` (48 lines) only builds failure messages. New logic goes in a
  new focused module, `entrypoint/review_markdown_gate.py`, not into `review_loop.py`.
- `uv run task lint` is `uv lock --check`, `ruff check`, `ruff format --check` and `check_flow_module_budgets.py`. CI
  hygiene additionally runs `make lint` (lint-json, lint-yaml, lint-shell, lint-md, then `uv run task lint`). The
  Markdown check is therefore the one real gap. Json, yaml and shell checks are also outside the resolver gate, which
  the drift test must make explicit.
- The review image already installs Node 24 and npm (Dockerfile) but no Prettier. `npx prettier` downloads it on demand,
  so the check needs network or a baked-in copy. `formatting.py` already pins `prettier@3.8.4` and `calibration.py`
  already has a drift-guarded Prettier call, so there is precedent to reuse.
- The target repository's `.prettierrc.json` (`printWidth` 120, `proseWrap` always) is picked up automatically when
  Prettier runs with `cwd` set to the repository root.

Aggregation: overall p_success is the product of the two riskiest steps (Prettier availability in the image, then the
wiring) adjusted for the cheap test steps. The bottleneck is Step 3 (image availability and offline behaviour). Cost is
the sum of step costs. Entropy is the sum of step entropies. Metrics follow `docs/metrics.md`:
`EV = 0.80 * 60.0 + 0.5 * 5.0 - 0.3 * 2.0 - 20.0 = 48.0 + 2.5 - 0.6 - 20.0 = 29.9`. The header table figure is updated
to this value.

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** None
- **Exploration stance:** balanced. Most steps are exploit work in a well-understood area. One step probes how Prettier
  is provisioned in the offline container, which is the main unknown.
- **Safety priority level:** standard
- **Priority Justification:** No ledger, branch or sandbox rule in `holon-config/world/constraints.md` is touched. The
  only security-adjacent point is that the resolver runs a subprocess inside its container, with the argv built from
  git-reported paths.

## Exploration

- **Proportion of steps that are exploratory:** 0.15
- **Justification:** Only the image-provisioning probe is uncertain. The rest reuses existing patterns.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` section 3: exactly three append-only ledgers, so no new ledger files.
  - Section 1: work stays on the intent branch; merging stays with the human maintainer.
  - `docs/safety.md`: no network egress beyond what the role container already has, and no history rewriting (the
    formatting is folded into the commit the resolver already makes, with `_assert_safe_git_args` kept).
  - Repository ruff limits (`C901 <= 10`, `PLR0913 <= 5`) with no new `noqa`.
- **Potential violations or edge cases:**
  - Filenames with spaces or leading dashes passed to Prettier.
  - Markdown files deleted or renamed by the resolver.
  - `.prettierignore` entries: Prettier exits non-zero or warns when an explicitly named file is ignored.
  - Prettier not downloadable with no network in the container.
  - Changes that came from the resolver versus pre-existing dirty files.
- **Mitigations built into the plan:**
  - Pass paths after `--`, filter to existing `*.md` files, and run with `cwd` set to the repository root.
  - Use `git status --porcelain -z` against the resolver pathspec to find changed files.
  - Honour `.prettierignore` by passing `--ignore-unknown` or checking explicitly.
  - A missing `npx` or unreachable Prettier is a structured failure naming the tool, never a skip.
- **Residual risk accepted (and why):** Prettier version drift between the container's `npx` fetch and CI. Accepted
  because both use the unpinned `npx prettier` of `lint-md`, so the gate matches CI exactly. A pin would diverge from
  CI.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 8.0
- **Budget Compliance:** The strategy fits within budget; no `## Sub-intent list` is needed.

## Plan Description & Strategy

Add one new module, `entrypoint/review_markdown_gate.py`, owning the changed-Markdown discovery, the Prettier check, the
Prettier write and the failure messages. `_verify_before_push` calls it right after lint passes. When only the Markdown
check fails, the gate formats just the changed files, re-checks, and lets the existing `git add -A` stage the result
into the same fix commit. A drift test ties CI's hygiene lint command to the resolver's verification command. The image
is verified for Node/npx and the missing-formatter message is made explicit. Tests are hermetic with a fake `npx` shim
on PATH and a temporary git fixture repository. Planned test runs: affected files with `uv run pytest <paths>` first,
then the full suite once with `uv run task test`. No repeated runs are needed because the intent has no timing target.

---

## Step 1: Add the Markdown gate module

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, self-contained module with a clear interface; low risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Provide `check_changed_markdown` and `format_changed_markdown` helpers that run the CI Prettier check
  on only the resolver's changed Markdown files.
- **Git branch:** I-1794480898-mdgate/P-1794480915-c8cd81b2-claude-agent-claude-sonnet-5-5/E-step1-gate/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create `entrypoint/review_markdown_gate.py` (target under 150 lines) with module docstring in Australian English.
- List changed Markdown: `git status --porcelain -z` limited to the resolver pathspec; keep entries ending `.md`, drop
  deletions, resolve renames to their new path; verify each exists on disk.
- If the list is empty, return a "skipped" outcome with no subprocess started.
- Check command: `npx prettier --check -- <files>` with `cwd` the repository root so the target's `.prettierrc.json` and
  `.prettierignore` apply. Same `npx prettier` spec that `lint-md` and `lint-docs` use.
- Write command: `npx prettier --write -- <files>` limited to the same list, never a glob such as `**/*.md`.
- Missing `npx` or a process that cannot start reuses `missing_tool_result` from `review_verification.py`, extended with
  a hint that the review-loop image needs Node and network access (or a baked-in Prettier) for Markdown checks.
- Failed files are parsed from Prettier's `[warn] <path>` lines and named in the message; fall back to the full changed
  list if parsing yields nothing.
- Add a timeout to each subprocess and use `run_cmd` as the rest of the resolver does so tests can fake it.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` (sandbox containment), `docs/safety.md`.
- **Potential failure modes for this step:**
  - Mis-parsing porcelain output for renames or paths with spaces.
  - Prettier warning about ignored files.
- **Guardrails and early‑abort checks:** Use `-z` parsing; add unit tests for odd paths; run ruff complexity checks.

### Success & Discard Criteria

- **Success:** Module imports cleanly, passes ruff with no `noqa`, and unit tests for discovery pass.
- **Discard:** More than 1.5x the predicted cost, or a need to grow `review_loop.py` beyond a call site.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.5   |
| impact_pred         | 40.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 32.8  |

### Step Metrics Rationale

`EV = 0.92 * 40.0 + 0.5 * 2.0 - 0.3 * 0.5 - 5.0 = 36.8 + 1.0 - 0.15 - 5.0 = 32.65`, rounded to 32.8 as a planning
estimate. High success because the pattern already exists in `calibration.py` and `formatting.py`.

---

## Step 2: Wire the gate into the resolver verification with fix-then-recheck

- **Sub‑intent recommendation:** NO
- **Reasoning:** A small edit to one call site; the logic lives in Step 1's module.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Run the gate after `uv run task lint` in `_verify_before_push`, auto-format when Markdown is the only
  failure, and report named files on a hard failure.
- **Git branch:** I-1794480898-mdgate/P-1794480915-c8cd81b2-claude-agent-claude-sonnet-5-5/E-step2-wire/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `_verify_before_push`, after the lint success branch, call the gate. Keep the return tuple
  `(lint_ok, tests_ok, error)` unchanged so `_commit_resolution` and the result JSON keep their shape.
- Flow: check; if it fails, run `--write` on the same files; re-check; if the re-check passes, continue to tests
  (formatting is picked up by the existing `git add -A` with the resolver pathspec, so it lands in the same fix commit).
  If the re-check or write fails, return `lint_ok=False` with a message from `verification_error("markdown", ...)` that
  names the files.
- Only Markdown failures are auto-fixed: the gate runs after lint has already passed, so a ruff failure returns first
  and keeps its current behaviour; a test failure after formatting keeps its current behaviour.
- Extend `verification_error` to handle the new step label ("Resolver pre-push Markdown formatting failed ... files:
  a.md, b.md") and keep the missing-tool path.
- Keep `review_loop.py` growth to a few lines (import plus one call); if it exceeds the budget, move
  `_verify_before_push` into `review_verification.py` or a new `review_pre_push.py` rather than growing the file. Check
  `scripts/check_flow_module_budgets.py` (flow modules only) is unaffected.
- Mention the formatting in the resolver result (for example a `markdown_formatted` list) only if the existing result
  schema accepts additive fields; otherwise log it.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` (no history rewriting); `_assert_safe_git_args` unchanged.
- **Potential failure modes for this step:**
  - Formatting touching a file outside the resolver's changes.
  - Changing the return contract and breaking `test_review_loop.py` or `test_review_resolver_failure.py`.
- **Guardrails and early‑abort checks:** Run the existing review-loop tests first; the file list comes only from Step
  1's discovery.

### Success & Discard Criteria

- **Success:** Existing review tests still pass and the new behaviour tests (Step 5) pass.
- **Discard:** The change requires altering the tuple contract used by the host flow.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.5   |
| impact_pred         | 50.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 40.5  |

### Step Metrics Rationale

`EV = 0.88 * 50.0 + 0.5 * 2.0 - 0.3 * 0.5 - 4.0 = 44.0 + 1.0 - 0.15 - 4.0 = 40.85`, rounded to 40.5. This step delivers
the main user-visible value.

---

## Step 3: Confirm and provision the formatter in the review-loop image

- **Sub‑intent recommendation:** NO
- **Reasoning:** The unknown is small; resolve it by inspection and one Dockerfile line, with a fail-closed message as
  the fallback.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Node 24 and npm are present in the image, but Prettier is not baked in and the resolver
  container may lack network access, so `npx prettier` cannot be relied on.
- **Learning target:** Whether the resolver container has network access at verification time, and whether a baked-in
  Prettier at the version CI resolves is the right fix.
- **Maximum acceptable cost for this learning:** One Dockerfile edit plus one container-image test; no image rebuild
  required in the hermetic suite.

### Intent & Git Integration

- **Step Intent:** Make Prettier available in the image or make its absence a clear, named failure.
- **Git branch:** I-1794480898-mdgate/P-1794480915-c8cd81b2-claude-agent-claude-sonnet-5-5/E-step3-image/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Read `apps/sandbox-executor/Dockerfile`, `docker-bake.hcl` and `tests/test_container_images.py` to see how the
  review-loop role is built and how tools like `uv` are asserted present.
- If the resolver container is network-isolated (check `docs/hermetic_testing.md` and the dispatcher), add a global
  `npm install -g prettier@<pinned>` after the Node install, using the version `package.json` or CI resolves; record the
  pin in one place and reuse `PRETTIER_SPEC` from `formatting.py` if the versions agree.
- Keep the missing-tool message from Step 1 naming `npx`/`prettier`, and the Dockerfile comment explaining why (mirrors
  the existing uv comment).
- Add a static test in `test_container_images.py` asserting the Dockerfile installs Prettier, in the same style as its
  existing `uv` assertion. The test only reads files and does not build images.
- Update `apps/sandbox-executor/docs/docker_bake.md` or the README section on the review role with one paragraph
  (Australian English, Prettier-formatted).

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (if the formatter cannot run offline and the pin diverges from CI, the gate would misreport)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` network rules; "no new dependencies in the Python packages" is respected because
  this is an image-level npm package.
- **Potential failure modes for this step:**
  - Pinned Prettier differs from what CI's `npx prettier` resolves, giving different formatting.
  - Image build time and size grow slightly.
- **Guardrails and early‑abort checks:** Compare the Prettier version used by `uv run task lint-docs` locally with the
  pin; if they differ, prefer the fail-closed message and record a follow-up rather than guessing.

### Success & Discard Criteria

- **Success:** The image build installs Prettier (or the check provably fails with a message naming what is missing),
  and a static test asserts it.
- **Discard:** The offline question cannot be settled by inspection; then ship the fail-closed message only and note the
  follow-up.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 2.0   |
| impact_pred         | 45.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 35.6  |

### Step Metrics Rationale

`EV = 0.82 * 45.0 + 0.5 * 5.0 - 0.3 * 1.0 - 4.0 = 36.9 + 2.5 - 0.3 - 4.0 = 35.1`, rounded to 35.6. This is the
bottleneck because image behaviour cannot be fully exercised in the hermetic suite.

---

## Step 4: Add the lint-coverage drift test

- **Sub‑intent recommendation:** NO
- **Reasoning:** One small test file; reuses the drift-guard pattern from `test_calibration.py`.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Assert every check in the hygiene workflow's lint command is covered by the resolver's verification
  command.
- **Git branch:** I-1794480898-mdgate/P-1794480915-c8cd81b2-claude-agent-claude-sonnet-5-5/E-step4-drift/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- New `tests/test_resolver_lint_parity.py`. Parse `.github/workflows/test-hygiene.yml` (PyYAML is already a test
  dependency only if present; otherwise a line parse of the `run:` entries) to collect the hygiene steps' commands:
  `uv lock --check`, `ruff check`, `ruff format --check`, and `make lint`.
- Parse the Makefile `lint` target prerequisites (`lint-json`, `lint-yaml`, `lint-shell`, `lint-md`) and its
  `uv run task lint` call, and the `lint` task in `pyproject.toml`.
- Define the resolver's covered set as the commands its pre-push gate runs: `uv run task lint` (which covers the ruff,
  lock and budget checks) plus the new Markdown gate (covers `lint-md`).
- Keep an explicit, reviewed allow-list for checks the resolver deliberately does not run (`lint-json`, `lint-yaml`,
  `lint-shell` need jq or shellcheck that the image may lack). The test fails when a hygiene check is neither covered
  nor on the allow-list, so adding a check to CI forces a conscious decision. Each allow-list entry carries a one-line
  reason.
- Also assert `make lint` still delegates to `uv run task lint`, which is the "same task" guarantee.
- No new dependencies; read files with the standard library (`tomllib`, `re`).

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/testing/testing_guide.md`; tests strictly via `uv run pytest`.
- **Potential failure modes for this step:** A brittle parse of the workflow file; an allow-list that hides real gaps.
- **Guardrails and early‑abort checks:** Parse structurally and test the parser on a mutated copy that adds a check (the
  test must then fail).

### Success & Discard Criteria

- **Success:** The test passes on the current tree and fails when a new check is added to the hygiene workflow without
  coverage.
- **Discard:** The allow-list would need more than the three non-Python checks.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.0   |
| impact_pred         | 35.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 29.3  |

### Step Metrics Rationale

`EV = 0.88 * 35.0 + 0.5 * 3.0 - 0.3 * 0.5 - 3.0 = 30.8 + 1.5 - 0.15 - 3.0 = 29.15`, rounded to 29.3. Cheap, and it
prevents the root cause recurring.

---

## Step 5: Hermetic behaviour tests with a fixture repository

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tests use existing fakes and fixtures; moderate size.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the five required behaviours without network or real Prettier downloads.
- **Git branch:** I-1794480898-mdgate/P-1794480915-c8cd81b2-claude-agent-claude-sonnet-5-5/E-step5-tests/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- New `tests/test_review_markdown_gate.py`, reusing `worktree_fixtures.py` and `review_fakes.py` for a temporary git
  repository with a `.prettierrc.json`.
- Provide a deterministic fake `npx` (a small script placed first on PATH via `monkeypatch`, or a patched `run_cmd`)
  that implements `--check` and `--write` for a simple rule (for example trailing whitespace or an over-long line), so
  no real Node or network is needed. The existing `no_real_npx` guard pattern in `test_calibration.py` ensures a real
  `npx` is never invoked.
- Cases: (a) a changed unformatted `.md` fails `--check`, is written, re-checked, passes, and the resulting commit
  contains the formatted file; (b) a change touching no Markdown starts no Prettier subprocess; (c) an unrelated
  unformatted `.md` stays byte-identical after the run; (d) a lint failure returns exactly the current message and never
  reaches the gate; (e) a test failure after clean Markdown keeps its current message; (f) missing `npx` yields the
  structured failure naming it; (g) a format that cannot converge names the failing file in the message; (h) a path with
  a space.
- Run affected tests with
  `uv run pytest apps/sandbox-executor/tests/test_review_markdown_gate.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_review_resolver_failure.py apps/sandbox-executor/tests/test_resolver_lint_parity.py apps/sandbox-executor/tests/test_container_images.py`,
  then the full suite once with `uv run task test`.

### Dependencies & Criticality

- **Depends on:** Steps 1 and 2 (3 and 4 for their own tests)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/hermetic_testing.md`, `network_guard.py`, `test_sandbox_hermetic_guard.py`.
- **Potential failure modes for this step:** A fake Prettier that diverges from the real one, hiding real formatting
  behaviour.
- **Guardrails and early‑abort checks:** Keep one optional marked check against real Prettier out of the default suite;
  the hermetic guard must still pass.

### Success & Discard Criteria

- **Success:** All listed cases pass under `uv run pytest`, and the full suite passes.
- **Discard:** Tests need real network or a real Node install to pass.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.5   |
| impact_pred         | 40.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 31.3  |

### Step Metrics Rationale

`EV = 0.88 * 40.0 + 0.5 * 2.0 - 0.3 * 0.5 - 5.0 = 35.2 + 1.0 - 0.15 - 5.0 = 31.05`, rounded to 31.3.

---

## Step 6: Documentation and final gates

- **Sub‑intent recommendation:** NO
- **Reasoning:** Trivial documentation and verification.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Document the new gate and confirm lint, docs lint and tests pass.
- **Git branch:** I-1794480898-mdgate/P-1794480915-c8cd81b2-claude-agent-claude-sonnet-5-5/E-step6-docs/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Update the Stage 4 resolver description in `docs/` (for example `docs/review_consensus.md`) and the sandbox-executor
  README with the new verification order: lint, Markdown check on changed files (auto-format then re-check), tests.
- Write in Australian English (formatting, behaviour); wrap prose at 120 columns, with formulas in code spans.
- Run `uv run task lint`, `uv run task lint-docs`, then the full suite once with `uv run task test`. If lint-docs
  reports changes, fix with Prettier on just the touched files.
- Do not touch any ledger under `holon-knowledge/ledger/`.

### Dependencies & Criticality

- **Depends on:** Steps 1 to 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` section 3 (ledgers untouched).
- **Potential failure modes for this step:** Prettier oscillation on long formula lines.
- **Guardrails and early‑abort checks:** Keep formulas in code spans.

### Success & Discard Criteria

- **Success:** `uv run task lint`, `uv run task lint-docs` and `uv run task test` all pass.
- **Discard:** Failures unrelated to this change that cannot be fixed within scope; record them instead.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.5   |
| impact_pred         | 20.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 17.4  |

### Step Metrics Rationale

`EV = 0.95 * 20.0 + 0.5 * 1.0 - 0.3 * 0.2 - 2.0 = 19.0 + 0.5 - 0.06 - 2.0 = 17.44`.

---

## Justification and Safety Summary

The plan makes the resolver's gate match CI for Markdown, fixes in the same commit, adds a drift test so CI and the
resolver cannot diverge silently, and fails loudly when the formatter is missing. Step costs sum to 23.0, so the overall
`cost_pred` of 20.0 assumes some overlap between the test steps. Step entropies sum to 8.0, within the 15.0 budget. No
ledger, branch or sandbox rule is affected, and merging stays with the human maintainer.
