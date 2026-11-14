# Execution Record: E-1794663560-da6ecf9f-claude-agent-claude-opus-5-5

## Summary

Implements plan `P-1794663302-17aba811-claude-agent-claude-opus-5-5` for intent `I-1794663286-hygiene`: remove the
local-notes directory and the hidden agent-state directory from the tracked tree, replace cited external task and
review-finding identifiers with plain descriptions, and add a hermetic repository hygiene test.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 1.0   |
| entropy_actual        | 9.0   |
| impact_actual         | 70.0  |
| cost_actual           | 13.0  |
| learning_value_actual | 3.0   |

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
- `scaffold.py` and `review_loop.template.md` edited identically (parity test green); README, `docs/architecture.md` and
  `docs/git_flow.md` name `tmp/review/`.
- Tests re-pointed, including the symlinked-root refusal cases `("tmp", False)`, `("tmp/review", False)` and
  `("tmp/review", True)`, which still fail closed. Four modules: 233 passed.

### Step 5: Replace cited identifiers with plain descriptions

- 70 review-finding citations (`C<n>[A-Z]-N<n>`, `C<n>-I<n>`, `R<n>-N<n>`, `R<n>-I<n>`, PR-cycle citations and
  `review finding N<n>`) removed from comments and docstrings in `local_llm.py`, `cli/__init__.py` and 17 test modules.
  Leading ids were dropped and the sentence re-capitalised; trailing parentheticals were removed; the few where the id
  was the subject were rewritten (for example "the withheld-host guard" in `local_llm.py`).
- `.github/workflows/test-stress.yml`: the external task id became "an earlier incident".
- Repository-native finding ids used as data (`R1-F1`, `FIND-CI-001` and so on) and `noqa` codes are untouched; no
  assertion literal changed. The 17 Step 5 modules: 1777 passed, 632 subtests passed.

### Step 6: Hermetic repository hygiene test

- New `apps/sandbox-executor/tests/test_repository_hygiene.py`: lists tracked files with `git ls-files -z` (local, as
  `test_pre_commit_check.py` already does), skips the historical records, gitlinks, symlinks and non-UTF-8 files, and
  reports `path:line: reason` for forbidden directory prefixes (path-like tokens only), case-insensitive external task
  ids, and review-finding citations. Forbidden names are assembled from fragments so the module never matches itself.
- `ALLOWLIST` holds one entry: the exact hidden-directory line in `.gitignore`.
- 25 tests pass (real tree clean, positives, negatives such as `R1-F1`, `noqa: F401`, `beans 12`, `mytodo/`, historical
  and allowlist skips). A temporary intent-to-add probe file naming the local-notes directory made the real-tree test
  fail with `docs/zz_probe.md:1: local-notes directory`; the probe was removed.

### Step 7: Verification

- All four acceptance criteria hold: the acceptance greps (with historical excludes) return only the allowlisted
  `.gitignore` line; the A/B runner lives in `scripts/`; the hygiene test fails on an injected leak; tests and
  `uv run task lint` pass.
- `git diff --stat d4726c6 -- holon-knowledge/ledger plans` is empty (the ledger rows against `main` are the flow's own
  intent and plan rows); `holon-knowledge/ledger` still lists exactly three ledgers; `uv.lock` is unchanged.

## Tests Run

| command                                                                                 | result                                | pytest time |
| --------------------------------------------------------------------------------------- | ------------------------------------- | ----------- |
| baseline: `uv run pytest` on five most affected modules (Step 1)                        | 455 passed, 0 failed                  | `2.63s`     |
| `uv run pytest apps/sandbox-executor/tests/test_token_reduction.py` (Step 2)            | 222 passed, 0 failed                  | `2.14s`     |
| `uv run pytest` on `test_calibration.py` and `test_token_reduction.py` (Step 3)         | 304 passed, 8 subtests, 0 failed      | `2.44s`     |
| `uv run pytest` on the four scratch-root and template modules (Step 4)                  | 233 passed, 0 failed                  | `2.40s`     |
| `uv run pytest` on the 17 Step 5 modules                                                | 1777 passed, 632 subtests, 0 failed   | `14.82s`    |
| `uv run pytest apps/sandbox-executor/tests/test_repository_hygiene.py` (Step 6)         | 25 passed, 0 failed                   | `0.65s`     |
| `uv run pytest -n auto --dist worksteal` on all 24 touched and related modules (Step 7) | 2094 passed, 632 subtests, 0 failed   | `15.56s`    |
| `uv run task test` (full suite, once)                                                   | 4071 passed, 1 skipped, 0 failed      | `23.80s`    |
| `uv run task lint`                                                                      | pass                                  | n/a         |
| `uv run task lint-docs`                                                                 | 39 files flagged, identical to `main` | n/a         |

The full suite ran once (no timing measurement required), so the median full-suite time is the single run, `23.80s`.

`uv run task lint-docs` already fails on `main`: prettier `3.8.4` flags the same 39 Markdown files, mostly historical
`plans/` and `executions/` records plus `docs/optimisation/token_reduction_measurement_plan.md`, `.agents/holon_flow.md`
and `holon-config/metrics/README.md`. Every Markdown file this change touched was checked with prettier; none is newly
flagged. The pre-existing failures were left alone to keep the change focused.
