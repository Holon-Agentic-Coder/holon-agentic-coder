# Plan for I-1794663286-hygiene

- **Plan ID:** P-1794663302-17aba811-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-14T13:35:03.863Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS (with two scoped clarifications, see Strategy Rationale)
- **Reframed intent (if applicable):** Not reframed. Clarifications: (a) the repository's own structured review finding
  ids used as test data and in prompts (for example `R1-F1`, `F-1`, `FIND-CI-001`) are product data, not citations, and
  stay; only _citations_ of past review-loop findings in comments, docstrings and help text (for example `C10-N1`,
  `C21-N3 (R1-N1/R2-N5)`, `#94 C21`, `review finding N2`) are rewritten. (b) Because the review ensemble itself writes
  its scratch under the hidden agent-state directory, that scratch root moves to the already-ignored repository-native
  `tmp/review/`; one `.gitignore` line for the hidden directory is kept (allowlisted) so the outside tooling's state
  stays untracked.
- **Exploration stance:** conservative. The work is mechanical hygiene with a strong, cheap verifier (git grep plus the
  new test); there is little to learn from risky alternatives, so effort goes to completeness and not to novelty.
- **Safety priority level:** elevated
- **Priority Justification:** `holon-config/world/constraints.md` section 3 (ledger immutability) and the intent's "keep
  exactly the three append-only ledgers" rule: a careless repository-wide find-and-replace could touch
  `holon-knowledge/ledger/*.jsonl`, `plans/` or `executions/`. The review scratch-root rename also changes a runtime
  path used by the review ensemble's containment checks (symlink refusal), which is security-relevant code.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** The inventory below was gathered during planning with git grep; every change is deterministic and
  verified by grep and tests. Step 1 is a cheap re-inventory (INFO_GATHERING, EXPLOIT), not exploration.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 10.0  |
| impact_pred         | 70.0  |
| cost_pred           | 14.0  |
| learning_value_pred | 4.0   |
| ev_pred             | 42.4  |

### Strategy Rationale

**Inventory gathered while planning** (tracked tree, excluding `holon-knowledge/ledger/`, `plans/`, `executions/`):

- Local-notes directory `todo/`: three tracked files (`ab_measure_all_methods.py`, `ab_real_api_results.json`,
  `ab_real_api_results.md`). The results are a single failed run (HTTP 404 "No endpoints found") that also carries an
  upstream account `user_id`; they are superseded and are deleted rather than moved. The script is exercised by seven
  tests in `apps/sandbox-executor/tests/test_token_reduction.py` that load it from `todo/` by path, so it moves to
  `scripts/ab_measure_all_methods.py`. Further `todo/` references: `.gitignore` (two wire-log/cache lines),
  `.agents/AGENTS.md` (scratch-script boundary), `token_reduction/mitm_addon.py` (default `WIRE_LOG_DIR`),
  `tests/test_calibration.py` (one `is_metadata_path` probe), `docs/optimisation/token_reduction_ab_testing.md`,
  `docs/optimisation/mitm_telemetry_metrics_plan.md` and `docs/optimisation/token_reduction_measurement_plan.md` (the
  latter two also name scripts `ab_benchmark_*.py` that do not exist in the tree).
- Hidden agent-state directory `.subagent/`: tracked `.subagent/.gitkeep` (empty); `.gitignore` and `.prettierignore`
  entries; `review_ensemble.py` uses it as `REVIEW_ROOT_REL = ".subagent/review"` plus docstrings and error messages;
  `scaffold.py` and `holon-config/prompts/review_loop.template.md` (kept in parity by `test_prompt_templates.py`);
  `apps/sandbox-executor/README.md`, `docs/architecture.md`, `docs/git_flow.md`; tests `test_host_worktree_sandbox.py`,
  `test_review_ensemble.py`, `test_review_report.py`.
- External task identifiers: no `Bean NNNN` in capitalised form remains; one lower-case `bean 0019` in
  `.github/workflows/test-stress.yml`. The hygiene test therefore matches case-insensitively.
- Review-loop finding citations (`C<n>[A-Z]-N<n>`, `R<n>-N<n>`, `#94 C<n>`, `review finding N<n>`): about 61 occurrences
  in `local_llm.py`, `cli/__init__.py` and 16 test modules (`test_ci.py`, `test_records.py`, `stages/test_review.py`,
  `test_dispatch.py`, `test_pipeline.py`, `test_budgets.py`, `test_calibration.py`, `test_cli.py`, `test_local_llm.py`,
  `test_planner.py`, `test_redaction.py`, `test_review_loop.py`, `test_token_reduction.py`, `test_turn_supervisor.py`,
  `test_url_message_fuzz.py`). The KB and wisdom stores are clean.

**Why one plan, no sub-intents:** the touch set is about 30 files but every edit is either a path rename or a comment
rewrite, verified by the same grep. Summed step entropy is `10.0`, below the `15.0` budget. Splitting would create
merge-order coupling (the hygiene test only passes once all cleanup lands) without reducing risk.

**Aggregation:** p_success is a qualitative product of the step probabilities dominated by the bottleneck Step 4
(scratch root rename, `0.88`) and Step 7 (full-suite plus lint gates, `0.92`). Treated as independent the product is
`P = 0.97 * 0.95 * 0.95 * 0.88 * 0.97 * 0.95 * 0.92 = 0.64`, but failures in Steps 2 to 6 are cheap to repair inside
Step 7's loop, so the recoverable estimate is `0.82`. Entropy is the sum of step entropy. Cost is the sum of step cost.
Impact is a qualitative judgement (precondition for in-repository issue tracking plus a regression guard). Learning
value is the max-plus-small-increments of steps.

`EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost` with `mu = 0.5`, `lambda = 0.3`:
`EV = 0.82 * 70.0 + 0.5 * 4.0 - 0.3 * 10.0 - 14.0 = 57.4 + 2.0 - 3.0 - 14.0 = 42.4`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` section 3: ledgers under `holon-knowledge/ledger/` are append-only; this plan
    neither reads-for-edit nor writes them.
  - `holon-config/world/constraints.md` section 1: commits stay on the execution branch; merge to `main` is the human
    maintainer's.
  - `docs/safety.md`: no network access in tests; the new test only runs `git ls-files` on the local checkout.
  - Intent constraints: Australian English, no new dependencies, tests via `uv run pytest`, exactly three ledgers.
- **Potential violations or edge cases:**
  - A bulk `sed` touching `plans/`, `executions/` or the ledgers.
  - Rewriting repository-native finding ids (`R1-F1`, `FIND-CI-001`) that are assertion data, breaking tests.
  - Renaming the review scratch root weakens the symlink-containment checks or leaves the scratch tracked.
  - Removing the `.gitignore` entry for the hidden directory, after which the outside tooling's state could be committed
    by `git add -A`.
  - The hygiene test matching its own source (self-reference) or binary files.
- **Mitigations built into the plan:**
  - All edits are by explicit file list; every grep and the test use the pathspec excludes
    `:(exclude)holon-knowledge/ledger`, `:(exclude)plans`, `:(exclude)executions`; Step 7 checks
    `git diff --stat main -- holon-knowledge/ledger plans executions` is empty (except this plan file, committed by the
    flow).
  - Finding-id rewrites are limited to comments, docstrings and help text; no string literal used in an assertion is
    changed.
  - The new scratch root `tmp/review` is already covered by the existing `tmp/` ignore rule; containment tests in
    `test_review_ensemble.py` (symlinked root cases) are kept and re-pointed, not deleted.
  - One `.gitignore` line for the hidden directory is kept and listed in the test's documented allowlist.
  - Forbidden tokens in the test are assembled from fragments so the test file contains no literal match.
- **Residual risk accepted (and why):** Developers who keep local notes in the old local-notes directory will now see
  them as untracked; the PR description recommends `.git/info/exclude` (untracked, local). Accepted because the intent
  forbids naming that directory in tracked files.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 10.0
- **Budget Compliance:** The strategy fits within budget (`0.5 + 2.0 + 1.0 + 2.5 + 1.5 + 1.5 + 1.0 = 10.0 <= 15.0`).

## Plan Description & Strategy

Clean the tracked tree in four mechanical passes (local-notes script and results, local-notes path defaults, hidden
agent-state directory, cited identifiers), then lock the result in with a hermetic hygiene test, then run the targeted
tests, the full suite once, and both lint gates. Touch set at module granularity:

- Move: `todo/ab_measure_all_methods.py` to `scripts/ab_measure_all_methods.py`. Delete:
  `todo/ab_real_api_results.json`, `todo/ab_real_api_results.md`, `.subagent/.gitkeep`.
- Source: `sandbox_executor/review_ensemble.py`, `sandbox_executor/scaffold.py`,
  `sandbox_executor/token_reduction/mitm_addon.py`, `sandbox_executor/local_llm.py` (docstring),
  `sandbox_executor/cli/__init__.py` (comment only).
- Config and docs: `.gitignore`, `.prettierignore`, `.agents/AGENTS.md`, `.github/workflows/test-stress.yml` (comment),
  `holon-config/prompts/review_loop.template.md`, `apps/sandbox-executor/README.md`, `docs/architecture.md`,
  `docs/git_flow.md`, `docs/optimisation/token_reduction_ab_testing.md`,
  `docs/optimisation/mitm_telemetry_metrics_plan.md`, `docs/optimisation/token_reduction_measurement_plan.md`.
- Tests: the existing modules listed in the inventory, plus a new focused module
  `apps/sandbox-executor/tests/test_repository_hygiene.py`.

Files outside the intent's literal scope list (`local_llm.py`, `cli/__init__.py`, `.agents/AGENTS.md`,
`.github/workflows/test-stress.yml`, `holon-config/prompts/review_loop.template.md`) are included because acceptance
criterion 1 covers every tracked file, and the template must stay in parity with `scaffold.py`.

---

## Step 1: Re-inventory the leaks and take a test baseline

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only, minutes of work; it guards against the tree having moved since planning.
- **Step Type:** INFO_GATHERING
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Confirm the inventory in the Strategy Rationale still matches the branch head and record a baseline.
- **Git branch:** `I-1794663286-hygiene/P-1794663302-17aba811-claude-agent-claude-opus-5-5/E-<timestamp>-hygiene/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run git grep with pathspec excludes for the ledgers, `plans` and `executions` for: the local-notes prefix (`todo/`),
   the hidden directory (`.subagent`), `[Bb]ean [0-9]{4}` (case-insensitive), and the finding-citation patterns
   `\bC[0-9]+[A-Z]?-N[0-9]+\b`, `\bR[0-9]+-N[0-9]+\b`, `#[0-9]+ C[0-9]+\b`, `review finding N[0-9]`.
2. Record per-file counts in the execution notes; if new files appear, add them to Steps 2 to 5's file lists.
3. Run
   `uv run pytest apps/sandbox-executor/tests/test_token_reduction.py apps/sandbox-executor/tests/test_review_ensemble.py apps/sandbox-executor/tests/test_host_worktree_sandbox.py apps/sandbox-executor/tests/test_prompt_templates.py apps/sandbox-executor/tests/test_review_report.py`
   to confirm a green baseline for the most affected modules.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` section 3.
- **Potential failure modes for this step:** baseline already red for unrelated reasons.
- **Guardrails and early‑abort checks:** if the baseline is red, record the failing ids and do not attribute them to
  this change later; abort only if the failures sit in modules this plan edits.

### Success & Discard Criteria

- **Success:** inventory confirmed or updated; baseline recorded.
- **Discard:** none (read-only).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.5   |
| impact_pred         | 5.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 3.7   |

### Step Metrics Rationale

`EV = 0.97 * 5.0 + 0.5 * 1.0 - 0.3 * 0.5 - 1.0 = 4.85 + 0.5 - 0.15 - 1.0 = 4.2`, rounded down to `3.7` for the chance
that the inventory needs widening. Low impact on its own; it de-risks every later step.

---

## Step 2: Move the A/B measurement script to scripts/ and retire the superseded results

- **Sub‑intent recommendation:** NO
- **Reasoning:** Self-contained file move with seven existing tests as a verifier; low risk.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The A/B runner lives at a repository-native path, its outputs default to an ignored repository-native
  directory, and the documents point at it.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `git mv todo/ab_measure_all_methods.py scripts/ab_measure_all_methods.py`.
2. `git rm todo/ab_real_api_results.json todo/ab_real_api_results.md`: a single failed run (404) that embeds an upstream
   account id; superseded, so deleted rather than moved into `holon-knowledge/` (the knowledge base holds KB and wisdom
   records, not raw run output). Note the deletion and reason in the commit message.
3. In the moved script, change defaults from the local-notes prefix to `tmp/`: `--wire-log-dir` default
   `tmp/mitm_wire_logs`, cache default `tmp/cache` (help text too), `--output` default `tmp/scorecard_report.md`; in the
   workspace-reset `git clean` exclusion list replace the local-notes and hidden-directory excludes with
   `--exclude=tmp/` (keep `.venv` and `docs/`). Update the module docstring to Australian English where touched.
4. In `tests/test_token_reduction.py` replace the seven `parent.parent.parent.parent / <local-notes> / ...` loaders with
   one module-level helper (for example `_load_ab_runner()`) resolving
   `REPO_ROOT / "scripts" / "ab_measure_all_methods.py"`; rename nothing else. If any test asserts a default path,
   update it to the `tmp/` value.
5. Docs: in `docs/optimisation/token_reduction_measurement_plan.md` (action-item text near the "automated runner" bullet
   and the Gantt label) name `scripts/ab_measure_all_methods.py`. In `docs/optimisation/token_reduction_ab_testing.md`
   and `docs/optimisation/mitm_telemetry_metrics_plan.md` the referenced `ab_benchmark_*.py` scripts and result files do
   not exist: replace those commands with the real runner invocation
   (`uv run python scripts/ab_measure_all_methods.py ...` using only flags the script's argparse defines) and describe
   results as written to the `--output` path under `tmp/`; where a removed script had no equivalent, state plainly that
   the manual real-API benchmark is not shipped.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 5 (no new dependencies); `docs/safety.md` (no network in tests).
- **Potential failure modes for this step:** a test imports the script by module name from `sys.path`; ruff flags the
  script under `scripts/` with a rule not applied before (it was already linted by `ruff check .` from the root, so
  unlikely); `check_flow_module_budgets.py` covers only flow modules.
- **Guardrails and early‑abort checks:** run `uv run pytest apps/sandbox-executor/tests/test_token_reduction.py` and
  `uv run ruff check scripts/` immediately after the move.

### Success & Discard Criteria

- **Success:** the seven A/B tests pass from the new location; no tracked file under the local-notes prefix remains.
- **Discard:** if the script needs functional rewrites beyond path defaults to pass, stop and keep the move minimal.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 2.0   |
| impact_pred         | 15.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 10.65 |

### Step Metrics Rationale

`EV = 0.95 * 15.0 + 0.5 * 1.0 - 0.3 * 2.0 - 3.0 = 14.25 + 0.5 - 0.6 - 3.0 = 11.15`, reduced to `10.65` for doc rewrite
effort. Entropy reflects three docs and one test module touched.

---

## Step 3: Re-point the remaining local-notes path defaults to tmp/

- **Sub‑intent recommendation:** NO
- **Reasoning:** Four small edits with an existing ignore rule already covering the destination.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** No source, config or document names the local-notes directory.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `token_reduction/mitm_addon.py`: default `WIRE_LOG_DIR` becomes `tmp/mitm_wire_logs` (the container path
   `/tmp/wire_logs` set by the CLI is unaffected).
2. `.gitignore`: delete the two local-notes lines (wire logs and cache); `tmp/` already ignores the new defaults.
3. `docs/optimisation/token_reduction_measurement_plan.md`: replace every local-notes path in prose and in the shell
   snippets (`mkdir`, `chown`/`chmod`, `-v` mounts) with `tmp/` equivalents; keep `~/.holon/proxy-ca` as is.
4. `.agents/AGENTS.md`: scratch-script boundary becomes "`/tmp` or the git-ignored `tmp/` directory".
5. `tests/test_calibration.py`: the `is_metadata_path` negative probe uses `tmp/scratch.py` (any non-metadata path keeps
   the assertion meaningful; confirm by reading `is_metadata_path`).
6. Grep `test_token_reduction.py` for an assertion on the old wire-log default and update it.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 2 wording on ignore entries; `docs/safety.md` (wire logs may contain prompts, so they
  must stay ignored).
- **Potential failure modes for this step:** wire logs written to a non-ignored path.
- **Guardrails and early‑abort checks:** `git check-ignore -v tmp/mitm_wire_logs/x tmp/cache/x` must report the `tmp/`
  rule before committing.

### Success & Discard Criteria

- **Success:** git grep for the local-notes prefix (with historical excludes) returns nothing; `check-ignore` passes.
- **Discard:** none expected; revert the single file if a test depends on the old default in a way that needs redesign.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.0   |
| impact_pred         | 8.0   |
| cost_pred           | 1.5   |
| learning_value_pred | 0.5   |
| ev_pred             | 6.0   |

### Step Metrics Rationale

`EV = 0.95 * 8.0 + 0.5 * 0.5 - 0.3 * 1.0 - 1.5 = 7.6 + 0.25 - 0.3 - 1.5 = 6.05`, rounded to `6.0`.

---

## Step 4: Move the review scratch root out of the hidden agent-state directory

- **Sub‑intent recommendation:** NO
- **Reasoning:** The riskiest step (runtime path plus containment checks), but well covered by existing symlink and
  survival tests; splitting it off would leave the hygiene test unable to pass on its own branch.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The repository no longer tracks, uses or documents the hidden agent-state directory, apart from one
  `.gitignore` line that keeps the outside tooling's state untracked.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `git rm .subagent/.gitkeep`.
2. `review_ensemble.py`: `REVIEW_ROOT_REL = "tmp/review"`. Update the module docstring, the reviewer instruction line
   ("write nothing under `tmp/review/` except the report path"), the helper docstrings and the containment error text
   (for example "a symlinked tmp or tmp/review is the likely cause"); derive the message from `REVIEW_ROOT_REL` and its
   parent so the text cannot drift. Keep the resolve-and-refuse logic unchanged.
3. `scaffold.py` and `holon-config/prompts/review_loop.template.md` (identical edits so `test_prompt_templates.py`
   parity holds): namespaced artefacts move under `tmp/review/` (for example
   `tmp/review/<repo>_pr<N>_<purpose>_iter_<iteration>_{sha}.<ext>` and `tmp/review/<repo>_pr<N>_state.json`), the
   on-disk report trailer sentence and reviewer rules 8 and 10 name `tmp/review/`.
4. `.prettierignore`: drop the hidden-directory line (prettier already honours `.gitignore`). `.gitignore`: keep exactly
   one hidden-directory line; it is the only allowlisted occurrence (Step 6).
5. Docs: `apps/sandbox-executor/README.md` (two places), `docs/architecture.md`, `docs/git_flow.md` name `tmp/review/`.
6. Tests: in `test_host_worktree_sandbox.py`, `test_review_ensemble.py` and `test_review_report.py` replace path parts
   (`".subagent", "review"` becomes `"tmp", "review"`), expected relative paths and expected messages; the parametrised
   symlink cases become `("tmp", False), ("tmp/review", False), ("tmp/review", True)`; the `.gitignore` assertion at the
   end of the artefacts section checks that `tmp/` is ignored (via the ignore line list or `git check-ignore`). Update
   the inline-script fixture that creates the scratch log and the sorted-untracked assertion accordingly.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (the hygiene test and acceptance criterion 1 need it; p is above 0.6 so no sub-intent)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` sandbox containment; review scratch must stay inside the repository and
  untracked.
- **Potential failure modes for this step:** a root-level `tmp` that already exists as a file or symlink in a user
  worktree; containment message assertions drifting; prompt parity test failing on one-sided edits; `tmp/` also used by
  another tool and cleaned mid-review.
- **Guardrails and early‑abort checks:** run
  `uv run pytest apps/sandbox-executor/tests/test_review_ensemble.py apps/sandbox-executor/tests/test_host_worktree_sandbox.py apps/sandbox-executor/tests/test_review_report.py apps/sandbox-executor/tests/test_prompt_templates.py`
  after the edit; confirm the symlinked-root refusal cases still fail closed. Grep the source for any other consumer of
  the old path (none found during planning) before committing.

### Success & Discard Criteria

- **Success:** the four modules pass; git grep for the hidden directory returns only the single `.gitignore` line.
- **Discard:** if containment semantics cannot be preserved with `tmp/review` (for example a test proves `tmp` is
  routinely a symlink in sandboxes), stop and choose another ignored native root such as `.holon/review`, recording why.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 2.5   |
| impact_pred         | 20.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 1.5   |
| ev_pred             | 13.6  |

### Step Metrics Rationale

`EV = 0.88 * 20.0 + 0.5 * 1.5 - 0.3 * 2.5 - 3.5 = 17.6 + 0.75 - 0.75 - 3.5 = 14.1`, trimmed to `13.6` for the parity and
message-assertion churn. Highest entropy because it changes a runtime path and a reviewer prompt.

---

## Step 5: Replace cited task and review-finding identifiers with plain descriptions

- **Sub‑intent recommendation:** NO
- **Reasoning:** Comment and docstring edits only; wide but shallow.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Comments, docstrings and help text state the behaviour they guard instead of citing an outside id.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. For each hit of `\bC[0-9]+[A-Z]?-N[0-9]+\b`, `\bR[0-9]+-N[0-9]+\b`, `#[0-9]+ C[0-9]+\b` and `review finding N[0-9]`
   in `local_llm.py`, `cli/__init__.py` and the 16 test modules from the inventory: delete the id. Where the sentence
   already describes the behaviour (for example "never as a pass (C10-N1)"), just drop the parenthetical; where the id
   was the subject (for example a docstring starting "C15-N1:"), rewrite to a short behavioural sentence, keeping
   Australian English.
2. `local_llm.py` docstring: replace "the #94 C11-N1 guard" with a description (the guard that withholds the host when a
   `/`, `?` or `#` precedes the last `@`). `cli/__init__.py`: replace "(#94 C21)" with nothing or "by design".
3. `.github/workflows/test-stress.yml`: replace "the bean 0019 incident deleted the ..." with a neutral description of
   the earlier incident (a run that deleted the workspace).
4. Leave repository-native finding ids used as data (`R1-F1`, `R2-F2`, `F-1`, `FIND-CI-001`, `FIND-R2-001-01`) and
   `noqa` rule codes untouched.
5. Do not touch any file under `holon-knowledge/ledger/`, `plans/` or `executions/`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** ledger immutability; intent goal 3.
- **Potential failure modes for this step:** an id inside a test docstring that is asserted somewhere (none seen);
  accidental edit of an assertion literal; line length (ruff `E501`) after rewording.
- **Guardrails and early‑abort checks:** `git diff --word-diff` review limited to comment lines; `uv run ruff check` and
  `uv run ruff format --check` on touched files.

### Success & Discard Criteria

- **Success:** zero hits for the citation patterns outside the historical records; ruff clean.
- **Discard:** none expected.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 1.5   |
| impact_pred         | 12.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 0.5   |
| ev_pred             | 8.94  |

### Step Metrics Rationale

`EV = 0.97 * 12.0 + 0.5 * 0.5 - 0.3 * 1.5 - 2.5 = 11.64 + 0.25 - 0.45 - 2.5 = 8.94`.

---

## Step 6: Add the hermetic repository hygiene test

- **Sub‑intent recommendation:** NO
- **Reasoning:** One new focused test module; reusable guard but small.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** A test fails whenever a tracked file outside the historical records lives under, or mentions, a
  forbidden prefix, or contains an external task id or review-finding citation.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. New module `apps/sandbox-executor/tests/test_repository_hygiene.py` with a module docstring explaining the rule and
   the allowlist.
2. Constants: `HISTORICAL_PREFIXES = ("holon-knowledge/ledger/", "plans/", "executions/")`; forbidden path prefixes
   assembled from fragments (local-notes directory and hidden agent-state directory) so the module never matches itself;
   compiled regexes for the prefixes as path-like tokens (preceded by start, whitespace, quote, backtick, slash or `=`),
   case-insensitive `bean[ _-]?[0-9]{4}`, and the finding-citation forms from Step 5.
3. `ALLOWLIST`: a short tuple of `(path, reason)` pairs, initially only `.gitignore` for the single hidden-directory
   ignore line; the check for an allowlisted file permits only lines exactly equal to the allowed entry.
4. Pure helper `find_hygiene_violations(path, text)` returning a list of `"path:line: reason"` strings, plus a path
   check for tracked files under a forbidden prefix.
5. Tracked-file listing: `git ls-files -z` run on `REPO_ROOT` with `subprocess.run` (local, no network); skip the module
   if `.git` is absent. Ignore gitlinks (the `holon-coherence` submodule), symlinks, and files that do not decode as
   UTF-8 (binary).
6. Tests: (a) the real tree has no violations (message lists every hit); (b) parametrised synthetic cases proving the
   detector fires for each forbidden prefix as a path and as text, for `Bean 1234` and lower-case `bean 0019`, and for a
   `C10-N1` citation; (c) negatives proving `R1-F1`, `FIND-CI-001`, `noqa: F401`, `beans 12` and `mytodo/` do not fire;
   (d) historical prefixes and allowlisted lines are skipped. Keep each function within the ruff complexity limits.
7. No new dependencies; no network; no writes outside `tmp_path`.

### Dependencies & Criticality

- **Depends on:** Steps 2, 3, 4, 5
- **Is Bottleneck:** NO (it is the acceptance gate, but failures point straight at a missed file)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/testing/testing_guide.md` hermetic rules; `apps/sandbox-executor/tests/network_guard.py`.
- **Potential failure modes for this step:** false positives on legitimate text (for example `/tmp` mentions are fine;
  the prefix regex must not match `httpx` or `subagents`); the conftest hermetic guard forbidding `git` subprocesses
  (check `hermetic_fixtures.py` and follow how other tests call git).
- **Guardrails and early‑abort checks:** run `uv run pytest apps/sandbox-executor/tests/test_repository_hygiene.py` and
  temporarily inject a violation in a scratch copy via the synthetic tests rather than editing tracked files.

### Success & Discard Criteria

- **Success:** the module passes on the cleaned tree and the synthetic positive cases fail the detector as expected.
- **Discard:** if `git ls-files` is unavailable under the hermetic guard, fall back to reading the index via
  `git -C REPO_ROOT ls-files` in the same way existing tests already do; do not walk the filesystem (it would see
  untracked files).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.5   |
| impact_pred         | 15.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 12.3  |

### Step Metrics Rationale

`EV = 0.95 * 15.0 + 0.5 * 1.0 - 0.3 * 1.5 - 2.0 = 14.25 + 0.5 - 0.45 - 2.0 = 12.3`. High impact because it is the
regression guard named in the rationale.

---

## Step 7: Verify with targeted tests, the full suite once, and both lint gates

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard verification; no timing measurement is required, so one full-suite run.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove every acceptance criterion.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Targeted: `uv run pytest` on `test_repository_hygiene.py`, `test_token_reduction.py`, `test_review_ensemble.py`,
   `test_host_worktree_sandbox.py`, `test_review_report.py`, `test_prompt_templates.py`, `test_calibration.py`,
   `test_ledger_append_only.py` and every module touched in Step 5.
2. Full suite once: `uv run task test`.
3. `uv run task lint` (ruff check, format check, lock check, flow module budgets) and `uv run task lint-docs` (prettier
   on Markdown); fix formatting with prettier only on touched Markdown files.
4. Acceptance greps (with historical excludes) for the two forbidden prefixes, the Bean pattern and the citation
   patterns; expect only the allowlisted `.gitignore` line.
5. `git diff --stat main -- holon-knowledge/ledger plans executions` shows no change other than this plan's own file;
   `ls holon-knowledge/ledger` still lists exactly three ledgers.
6. Commit with a message describing the hygiene change; open the PR for the human maintainer; do not merge.

### Dependencies & Criticality

- **Depends on:** Steps 2 to 6
- **Is Bottleneck:** YES (p above 0.6, so no sub-intent)

### Safety & Constraint Considerations

- **Relevant rules:** Test-run discipline (targeted first, full suite once); merge stays with the human maintainer.
- **Potential failure modes for this step:** unrelated flaky test; prettier reflowing edited Markdown tables.
- **Guardrails and early‑abort checks:** a failure in an untouched module that also failed in Step 1's baseline is
  reported, not fixed.

### Success & Discard Criteria

- **Success:** all four acceptance criteria hold.
- **Discard:** stop if fixing failures needs changes outside the touch set or cost exceeds `1.5 * 14.0 = 21.0`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.0   |
| impact_pred         | 10.0  |
| cost_pred           | 0.5   |
| learning_value_pred | 0.5   |
| ev_pred             | 8.65  |

### Step Metrics Rationale

`EV = 0.92 * 10.0 + 0.5 * 0.5 - 0.3 * 1.0 - 0.5 = 9.2 + 0.25 - 0.3 - 0.5 = 8.65`. Cost is mostly wall-clock of one suite
run; p reflects the chance of an unrelated flaky test or a prettier reflow needing a second pass.
