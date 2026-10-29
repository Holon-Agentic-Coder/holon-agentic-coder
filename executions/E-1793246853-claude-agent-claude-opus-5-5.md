# Execution Record: E-1793246853-claude-agent-claude-opus-5-5

## Summary

Implementation of plan `P-1793246567-claude-agent-claude-opus-5-5` for intent `I-1793246551-ci-timing-regression-gate`.
All seven plan steps were carried out on this execution branch. A new standard-library script,
`scripts/ci_timing_gate.py`, records each job's duration (and per-test JUnit times for the three test jobs) as a
`ci-timing-<workflow>-<job>-<os>` artefact on successful `main` runs, and on pull requests compares it with the median
of the last 5 `main` records (fail only above `median * 1.5` AND `median + 120 s`), always enforces a 600 s ceiling,
skips the relative check with a notice in cold start, and accepts the `timing-regression-accepted` label as a visible
override of the relative check only. All seven jobs in the six workflows call it; `.github/workflows/README.md`
documents it. `uv run task test` (2,743 passed), `uv run task lint` and `uv run task lint-docs` pass.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.9   |
| entropy_actual        | 2.5   |
| impact_actual         | 80.0  |
| cost_actual           | 12.0  |
| learning_value_actual | 5.0   |

## Steps Executed

1. **Step 1 (runtime facts): done, conservative assumptions adopted.** No test parses the workflow files' pytest
   commands (only docstrings in the stress tests name `test-stress.yml`), so adding `--junitxml` is safe. The GitHub API
   was deliberately not called from the sandbox. Adopted: (a) list artefacts filtered by `name`, check `expired` and
   `workflow_run.head_branch == "main"` client-side, sort by `created_at`, and attach the token with
   `add_unredirected_header` so `urllib` does not forward it on the `archive_download_url` redirect to the storage host;
   any fetch or parse error degrades to cold start. (b) Python 3.9-compatible syntax, so `python3` on `ubuntu-latest`
   and the macOS system `python3` both work without a set-up step; verified by running the script under CPython 3.9.25
   with `uvx --python 3.9 python -I`. (c) Re-runs replay the original event payload, so the pull-request triggers gain
   `labeled` and `unlabeled`.
2. **Step 2 (gate script): done.** Added `scripts/ci_timing_gate.py` (418 lines, standard library only): frozen
   dataclasses `TimingRecord`, `Thresholds`, `Verdict` and `Offender`; pure helpers `artefact_name`, `parse_junit`,
   `rolling_median`, `exceeds_relative`, `top_offenders`, `format_report`, `check_ceiling` and `evaluate`; injectable
   `fetch_baseline` (opener seam), `safe_fetch` and `main(argv, clock, fetcher)`. Exit codes: 0 pass, 1 gate failure, 2
   bad input. `ruff check`, `ruff format --check` and `--help` under `python3 -I` all pass.
3. **Step 3 (tests): done.** Added `apps/sandbox-executor/tests/test_ci_timing_gate.py` (54 test cases, 0.07 s): rolling
   median, tolerance and minimum-delta combinations, ceiling with and without baseline or label, cold start (0, 1 and 4
   records), label override, top-offender thresholds, sorting and limit, report appended to failures and the job summary
   but never failing on its own, nested JUnit parsing, `fetch_baseline` through a fake opener (expired and non-`main`
   artefacts dropped, at most N taken, token only in `unredirected_hdrs`), fetch and bad-zip errors degrading to cold
   start, `main` record mode and exit code 2, and the replay of the cited durations. Baselines cited only as a median
   are built by `reconstruct()` and labelled as reconstructions.
4. **Step 4 (workflows): done.** All seven jobs in the six workflows gained a first step "Record job start time", a
   "Timing gate" step after the last existing step, and an "Upload timing record" step (`actions/upload-artifact@v4`,
   `ci-timing-<stem>-<job>-<os>`, `retention-days: 30`, `if-no-files-found: error`) conditional on a push to `main` (or,
   for `build-images.yml`, `github.ref == 'refs/heads/main'`). The five pull-request workflows gained job-level
   `permissions` (`contents: read`, `actions: read`) and the `labeled` and `unlabeled` trigger types; the override flag
   receives only the boolean `contains(...)` expression through `TIMING_OVERRIDE`. `test-unit.yml` and
   `test-integration.yml` gained `--junitxml=unit-report.xml` and `--junitxml=integration-report.xml`; the stress job
   reuses `stress-report.xml` after its assertion step. Deviation: `build-images.yml` calls the script with a literal
   `record` mode and no token, since it is dispatch-only and never compares (least privilege). Prettier check passes;
   local dry runs of every gate command exit 0.
5. **Step 5 (docs): done.** `.github/workflows/README.md` gained a "CI Timing Regression Gate" section (measurement,
   artefact naming and retention, record and compare events, defaults with the calibration summary, how to read each
   message and the per-test report, and how to accept a deliberate slow-down with `timing-regression-accepted`), the
   `test-unit.yml` bullet now quotes `--junitxml=unit-report.xml`, and a paragraph notes the `labeled` and `unlabeled`
   trigger types and the `actions: read` permission. Flag values were cross-checked against the workflow diff.
6. **Step 6 (validation): done.** Every gate passes (see below). The first full `uv run task test` run had one failure,
   `test_ledger_lock.py::test_concurrent_process_appends_do_not_interleave`, a concurrency test unrelated to the touch
   set; it passed three times in isolation and the full suite then passed (2,743 tests), so it is recorded as a flaky
   pre-existing test, not changed here. The touch set holds: only `.github/workflows/*.yml`,
   `.github/workflows/README.md`, `scripts/ci_timing_gate.py`, the new test file and this execution record changed;
   `pyproject.toml`, the Makefile, `uv.lock` and the ledgers are untouched. The only harness-style match in the changed
   files is a pre-existing comment in `test-stress.yml`, not written by this change. Environment note: `uv` was
   installed with `pip install --user uv` and every command ran with the container's `PYTHONPATH` unset, never set.
7. **Step 7 (commit and push): done.** The plan branch had no new commits, so no rebase was needed. The sandbox had no
   git identity, so the commit was made with a per-command identity (`git -c user.name=... -c user.email=...`) matching
   the planner's `holon-agentic-coder.com` convention; no global configuration was changed. The push of the execution
   branch, including the six workflow files, was accepted, so the token's `workflow` scope was not a blocker.

## Tests Run

| command                                                                       | result                                     |
| ----------------------------------------------------------------------------- | ------------------------------------------ |
| `uv run pytest apps/sandbox-executor/tests/test_ci_timing_gate.py`            | 54 passed in 0.07 s                        |
| `uv run task test`                                                            | 2,743 passed (one flaky unrelated failure) |
| `uv run task lint`                                                            | passed                                     |
| `uv run task lint-docs`                                                       | passed                                     |
| `npx prettier --check .github/workflows/*.yml`                                | passed                                     |
| `python3 scripts/pre_commit_check.py <changed files>`                         | passed (10 files)                          |
| `python3 -I scripts/ci_timing_gate.py --help`                                 | lists every flag with its default          |
| `uvx --python 3.9 python -I scripts/ci_timing_gate.py record ...`             | exit 0 on CPython 3.9.25                   |
| local dry run of each workflow's gate command (record mode, temporary output) | all six exit 0                             |
| compare dry run with no `--repo` and a missing JUnit file                     | warnings plus cold-start notice, exit 0    |
