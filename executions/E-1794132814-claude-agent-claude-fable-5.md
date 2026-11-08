# Execution Record: E-1794132814-claude-agent-claude-fable-5

## Summary

Execution of plan `P-1794132519-claude-agent-claude-fable-5` for intent
`I-1794132504-purge-harness-bean-references-from-code-and-docs`. Removes every harness bean reference
(runtime strings, semantic identifiers, source comments and docstrings, test names and assertions,
documentation prose) from `apps/sandbox-executor/` and `docs/`, excluding the immutable history paths
(`plans/`, `executions/`, `holon-knowledge/`). In progress.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: Inventory and baseline (INFO_GATHERING)

- Case-insensitive grep for `bean` over the repository (excluding `plans/`, `executions/`,
  `holon-knowledge/`, `intents/`, caches) found **205 hits**: 93 in `apps/sandbox-executor/src/`, 53 in
  `apps/sandbox-executor/tests/`, 51 in docs (`docs/` + app README + `docs/hermetic_testing.md`), and 8 in
  three files outside the plan's survey: root `README.md` (2), `Makefile` (2) and
  `scripts/pre_commit_check.py` (4). Drift noted: the plan predicted ~197 across ~57 files; the extra 8 hits
  are in repo-level files. The intent description ("Purge all harness bean references ... from
  holon-agentic-coder code, docstrings, tests, and documentation") covers them, so they are cleaned in Steps
  4/6 and the Step 7 sweep includes them.
- Five-bucket classification matches the plan's survey (runtime strings, semantic identifiers,
  comments/docstrings, tests, documentation) with the drift above.
- Consumer grep for `bean_0034_enforced`, `"beans"` and `bean_list`: only `flow.py` and
  `tests/test_flow.py` / `tests/test_cli.py` consume them (history paths excluded). Rename surface closed.
- Environment note: `uv` was absent from the sandbox; installed user-level via `pip install --user uv`
  (tooling only — no project dependency, `pyproject.toml` or `uv.lock` change).
- Baseline:
  `uv run pytest apps/sandbox-executor/tests/test_flow.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_cli.py -n auto --dist worksteal`
  → **834 passed, 53 subtests passed in 5.29s** (green).

### Step 2: Runtime safety-halt strings (IMPLEMENTATION)

- Replaced `[BEAN 0034 SAFETY HALT]` with `[SAFETY HALT]` at `cli/commands/flow.py:288`,
  `entrypoint/review_loop.py:765`, `flow.py:5194` and `flow.py:5302`; the follow-on sentence
  `Under Bean 0034, autonomous merge to main ...` became `Autonomous merge to main ...`.
- Rewrote the ledger summary halt reason at `flow.py:4804` to `(human-only merge safety halt)`.
- Rewrote the rule-identity docstrings/comments at `review_loop.py:11` and `flow.py:12, 1515, 3211, 4146,
  4962` to name the human-only PR merge rule directly.
- Retargeted the single marker assertion (`tests/test_flow.py:1629`) to `"[SAFETY HALT]" in captured` with
  identical strictness (declared test change per ruleset.md §3).
- Guardrail grep: zero `BEAN 0034` hits remain in `src/`.
- Targeted tests: **834 passed, 53 subtests passed in 5.17s** (green).

### Step 3: Semantic identifier renames (IMPLEMENTATION)

- Renamed the payload key `bean_0034_enforced` → `human_merge_gate_enforced` at the six `flow.py` sites and
  retargeted the assertions at `tests/test_flow.py:515`, `:1543` and the fixture at `tests/test_cli.py:1078`
  (declared test changes per ruleset.md §3). Step 1 consumer grep confirmed no other consumer; historical
  ledger rows keep the old key untouched (append-only).
- PR-body generator (`flow.py`): section heading `## Referenced Beans` → `## References`, closing line
  `Closes Bean <list>` → `References: <list>`, locals `beans` → `references` and `bean_list` →
  `reference_list`, intent field read `beans` → `references`; the `None` fallback branch is unchanged under
  the new heading. Generator docstring updated to match.
- PR-body tests (`tests/test_flow.py:1676–1716`): fixture key `beans` → `references`, goal-text bean
  decorations replaced with plain numbered items, heading/closing assertions retargeted with identical
  strictness.
- Guardrail grep: zero `bean_0034_enforced`, `Referenced Beans`, `Closes Bean` or `bean_list` hits remain in
  `apps/`.
- Targeted tests (`test_flow.py` + `test_cli.py`): **733 passed, 53 subtests passed in 5.13s** (green).

### Step 4: Source comment/docstring sweep (REFACTOR)

- Scripted removal of self-contained `(Bean NNNN[ Goal N| parity])` parentheticals across 26 `src/` files
  (regex constrained to bean-only parentheticals; every changed line reviewed via `git diff --unified=0`).
- Seven manual rewrites where the sentence leaned on the bean for meaning: `local_llm.py` (server-connect
  rewrite provenance, twice), `ledger.py:34` (`ACT-BUDGET` parenthetical trimmed), `turns.py:8` ("A *turn*
  is a single assistant response ..."), `entrypoint/executor.py:267` ("its own intent"),
  `agent_runner.py:378` (multi-line parenthetical dropped), `budgets.py:15` ("recorded during the artefact,
  turn and model-envelope work").
- CLI help strings (`cli/commands/*`) had their `(Bean 0052)` / `(Bean 0118)` parentheticals dropped; a test
  grep confirmed no test asserts those help strings verbatim, so no test run was needed in this step.
- One `E501` long-line introduced in `local_llm.py` was wrapped; `uv run task lint` → **All checks passed!**
- Guardrail grep: **0** bean hits remain in `apps/sandbox-executor/src/`. Only comments, docstrings and help
  strings changed; no code semantics touched.

### Step 5: Test-suite sweep (TEST — declared test modifications per ruleset.md §3)

- Renames (no collisions, pre-checked): `test_stage4_review_bean_0034_human_only_merge_halt` →
  `test_stage4_review_human_only_merge_halt`; `test_review_loop_consensus_approval_halts_under_bean_0034` →
  `test_review_loop_consensus_approval_halts_without_merge`;
  `test_review_loop_main_consensus_approval_bean_0034` → `test_review_loop_main_consensus_approval_halts`;
  `test_stage5_calibrate_bean_0038[_mocked]` → `test_stage5_calibrate[_mocked]`;
  `test_summary_format_matches_the_bean_example` → `test_summary_format_matches_the_documented_example`.
- Assertion messages `VIOLATION OF BEAN 0034: ...` → `HUMAN-ONLY MERGE RULE VIOLATED: merge invoked with
  args ...` (×2, condition unchanged). Same constrained parenthetical sweep as Step 4 across 8 test files;
  manual rewrites for the `bean 0019` incident prose (→ "the workspace-deletion incident"), `Bean 0064` /
  `Bean 0138/0139` comments in `test_executor.py`, and `test_calibration.py`'s non-metadata fixture path
  `.beans/0001-task.md` → `.tasks/0001-task.md` (verified against `METADATA_PATH_PREFIXES`: still False).
- No test deleted; no assertion condition altered. Three `E501` long lines were shortened (message text
  only).
- Full app suite: `uv run pytest apps/sandbox-executor/tests/ -n auto --dist worksteal` →
  **3053 passed, 13 skipped, 651 subtests passed in 45.21s**; `uv run task lint` → **All checks passed!**
- Guardrail grep: **0** bean hits remain in `apps/sandbox-executor/tests/`.
