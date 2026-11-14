# Execution Record: E-1794663560-da6ecf9f-claude-agent-claude-opus-5-5

## Summary

Implements plan `P-1794663302-17aba811-claude-agent-claude-opus-5-5` for intent `I-1794663286-hygiene`: remove the
local-notes directory and the hidden agent-state directory from the tracked tree, replace cited external task and
review-finding identifiers with plain descriptions, and add a hermetic repository hygiene test.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: Re-inventory and baseline

- Inventory matched the plan, with two additions: `scripts/pre_commit_check.py` carried a spelling exemption for the
  local-notes directory, and review-finding citations also use an `-I<n>` form (for example `C23-I1`, `R1-I1`, about 20
  occurrences in `test_pipeline.py`, `test_artefacts.py`, `test_budgets.py`, `test_calibration.py`, `test_cli.py`,
  `test_executor.py`, `test_local_llm.py`, `test_planner.py`, `test_review_loop.py`, `test_turn_supervisor.py`). Both
  are folded into Steps 2 and 5, and the hygiene test pattern covers `[CR]<n>[A-Z]?-[NI]<n>`.
- Baseline on the five most affected modules: 455 passed.

### Step 2: Move the A/B runner to `scripts/`

- `git mv` to `scripts/ab_measure_all_methods.py`; deleted the superseded single failed run results (HTTP 404, carried
  an upstream account id).
- Defaults moved to `tmp/` (`tmp/mitm_wire_logs`, `tmp/cache`, `tmp/scorecard_report.md`); the workspace-reset
  `git clean` keeps `tmp/`, `.venv` and `docs/`.
- The script lost its spelling exemption, so `optimized` became `optimised` throughout it (identifiers and scorecard
  labels; all call sites are internal or positional) to pass `scripts/pre_commit_check.py`.
- `test_token_reduction.py`: one `_load_ab_runner()` helper replaces seven inline loaders.
- Docs: `token_reduction_ab_testing.md` and `mitm_telemetry_metrics_plan.md` now point at the real runner and state that
  no standalone real-API benchmark script is shipped; `token_reduction_measurement_plan.md` names `scripts/` and `tmp/`.

### Step 3: Re-point the remaining local-notes path defaults

- `mitm_addon.py` default `WIRE_LOG_DIR` is `tmp/mitm_wire_logs`; `.gitignore` drops the two local-notes lines
  (`git check-ignore -v` confirms `tmp/` covers `tmp/mitm_wire_logs/x` and `tmp/cache/x`).
- `.agents/AGENTS.md` scratch boundary names `/tmp` or the git-ignored `tmp/` directory; `test_calibration.py` probes
  `tmp/scratch.py` (still not a metadata prefix).
- `scripts/pre_commit_check.py` drops the spelling exemption for the retired directory.
- Prettier `3.8.4` already flags `docs/optimisation/token_reduction_measurement_plan.md` on `main` (math reflow); left
  unformatted to keep the change focused. The baseline `lint-docs` run flags 40 files, mostly under `plans/`.

### Step 4: Move the review scratch root to `tmp/review`

- Removed the tracked placeholder under the hidden agent-state directory and its `.prettierignore` line; `.gitignore`
  keeps one line so outside tooling state stays untracked (the single allowlisted occurrence).
- `review_ensemble.py`: `REVIEW_ROOT_REL = "tmp/review"`; the containment error names the parent via
  `_REVIEW_PARENT_REL = posixpath.dirname(REVIEW_ROOT_REL)` so the text cannot drift; resolve-and-refuse logic
  unchanged. Docstrings and the reviewer prohibition name `tmp/review/`.
- `scaffold.py` and `review_loop.template.md` edited identically (parity test green); README, `docs/architecture.md`
  and `docs/git_flow.md` name `tmp/review/`.
- Tests re-pointed, including the symlinked-root refusal cases `("tmp", False)`, `("tmp/review", False)` and
  `("tmp/review", True)`, which still fail closed. Four modules: 233 passed.
