# Execution Record: E-1793319612-claude-agent-claude-opus-5-5

## Summary

Implements plan `P-1793319426-claude-agent-claude-opus-5-5`: the Stage 5 calibration report is formatted with exactly
the `lint-docs` Prettier invocation (unpinned `npx prettier`) after its final write, and a fail-closed check runs before
any ledger append, `git add`, commit or push.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: Re-verify the version-skew root cause (done)

- `npx --yes prettier --version` resolves `3.9.10` (the `lint-docs` invocation).
- `npx --yes prettier@3.8.4 --check plans/P-1793275710-claude-agent-claude-opus-5-5_calibration.md` exits 1, while
  `npx prettier --check` on the same file exits 0.
- A 3.8.4 `--write` on a temporary copy (with `.prettierrc.json`) breaks the line after `$p\_success\_error = 0.20$,`,
  splitting `$cost\_error = 1.88$` across lines. Root cause confirmed: Prettier version skew, not config or write order.

### Step 2: Format and guard the report in `calibration.py` (done)

- Added `LINT_DOCS_PRETTIER = ("npx", "--yes", "prettier")` (unpinned, mirrors `lint-docs` and `lint-md`),
  `CalibrationFormatError(CalibrationCommitError)`, the `_run_prettier` subprocess seam, `_prettier_failure` (collects
  the reason in one place) and the public `format_calibration_report`.
- `run_calibrate` now calls `format_calibration_report` right after the final report write and before
  `record_calibration_action`, `git add` and `git commit`; with `skip_commit=True` a failure is only logged.
- Removed the `converge_prettier` import from `calibration.py`; `formatting.py` is untouched.
- `uv` was not on the sandbox `PATH`; installed it user-locally with `pip install --user uv` (tooling only, no
  project dependency change). `uv run task lint` passes.

### Step 3: Fail Stage 5 instead of pushing when the guard trips (done)

- `run_calibrate_stage` catches `CalibrationFormatError` (after `LedgerRewriteError`, before the generic `Exception`)
  and returns a FAILED Stage 5 via `_stage_failure`, skipping the fallback report, reconciliation commit and push block.
  This mirrors what the existing `CalibrationCommitError` path leaves behind (uncommitted report on the `/calibrated`
  branch) but now reports FAILED instead of SUCCESS.
- The fallback path uses `format_calibration_report` (warning only, never committed or pushed); the `converge_prettier`
  import was removed from `flow.py`.

### Step 4: Hermetic pytest coverage (done)

- `test_calibration.py`: new `TestFormatCalibrationReport` (11 tests) with a fixture paragraph in the 3.8.4 wrap from
  Step 1 that the fake `_run_prettier` rewrites to the repository style; argv and `cwd` assertions; a drift test that
  reads `lint-docs` from the root `pyproject.toml` with `tomllib`; a parametrised fail-closed guard (check exits 1,
  `TimeoutExpired`, `FileNotFoundError`, `npx` missing); credential redaction; `run_calibrate` raising before
  `record_calibration_action`, `git add` or `git commit` with the ledger and HEAD unchanged; dry run only warns; a
  passing report is committed. A `no_real_npx` fixture fails any test that reaches the real `subprocess.run` with `npx`.
- `test_flow.py`: new Stage 5 tests: `CalibrationFormatError` gives a FAILED stage with no `add`, `commit`, `merge` or
  `push`, and the happy path still pushes and fast-forwards.
- Existing patches of `sandbox_executor.calibration.converge_prettier` and `sandbox_executor.flow.converge_prettier`
  now target `format_calibration_report` (the old names no longer exist in those modules); assertions kept.

### Step 5: Probe npx resolution of unversioned prettier (done, exploratory)

- `npx --yes prettier --version` gives `3.9.10`; `npm view prettier version` gives `3.9.10`: the host resolves registry
  latest, the same as fresh CI.
- In a system temporary directory with `.prettierrc.json` and a 3.8.4-wrapped copy of a committed report,
  `format_calibration_report` (via `uv run python -I -c`) succeeded and `npx prettier --check` then exited 0.
- No repository change. Residual risk: a host with a stale npx cache could resolve an older Prettier; a follow-up intent
  could pin Prettier in `pyproject.toml` (`lint-docs`, `format-docs`) and the `lint-md` Makefile target together. The
  drift test would then flag `LINT_DOCS_PRETTIER` until it is pinned the same way.
