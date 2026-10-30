# GitHub Actions Workflows

This directory contains the CI/CD workflows for the `holon-agentic-coder` project.

## Workflows

- `make.yml`: Runs on pushes to `main` and on pull requests across both `ubuntu-latest` and `macos-latest`. It verifies
  `uname -m`, `make help`, and default `make`.
- `test-hygiene.yml`: Runs on pushes to `main` and on pull requests on `ubuntu-latest`. It executes fast repository
  hygiene in an isolated job:
  - Verifying lockfile currency with `uv lock --check` and `git diff --exit-code uv.lock`.
  - Running static linting with `uv run ruff check .`.
  - Checking formatting with `uv run ruff format --check .`.
  - Checking markdown formatting with Prettier (`npx prettier --check "**/*.md"`).
  - Validating repository cleanup via `uv run task clean`.
- `test-unit.yml`: Runs on pushes to `main` and on pull requests across both `ubuntu-latest` and `macos-latest`. It
  executes unit tests (`uv run pytest -m "not integration_test and not stress" --junitxml=unit-report.xml`) using cached
  `uv` dependencies.
- `test-stress.yml`: Runs on pushes to `main` and on pull requests on `ubuntu-latest`. It runs the stress suite
  (`uv run pytest -m stress`) -- the workspace-survival test and the hermetic canary guard, both of which re-run the
  unit suite in a child process -- measuring the execution time explicitly in its own workflow.
- `test-integration.yml`: Runs on pushes to `main` and on pull requests on `ubuntu-latest`. It builds all sandbox Docker
  images via `./apps/sandbox-executor/build_all_images.sh --output-log` and executes integration tests that require
  Docker services. For why containerized tests run exclusively on Linux, see [macos-docker.md](../macos-docker.md).
- `docker-build.yml`: Builds Docker images with multi-architecture Buildx caching, incorporating timing gate checks.
- `build-images.yml`: Manual dispatch workflow to build and verify Docker container images with multi-architecture
  Buildx caching.

Every job of every workflow ends with the CI timing regression gate described below. The five pull-request workflows
also trigger on the `labeled` and `unlabeled` activity types, so adding or removing the override label re-runs them, and
grant their job `actions: read` so it can read the baseline artefacts.

## CI Timing Regression Gate

`scripts/ci_timing_gate.py` stops a change that makes CI noticeably slower from reaching `main` unnoticed. It uses the
standard library only and runs on the runner's own `python3`.

### What is measured and where it is kept

- Each job's first step, "Record job start time" or "CI Timing Gate (Start)", writes `CI_TIMING_START` or
  `CI_JOB_START_EPOCH` to `$GITHUB_ENV`. The "Timing gate" or "CI Timing Gate (Check <= 5 min)" step near the end of the
  job measures from that timestamp, so the duration covers the first step to the gate step.
- On a push to `main` (and on a dispatch of `build-images.yml`) the gate runs in `record` mode: it writes the duration,
  job name, runner OS, commit and run id to `ci-timing/record.json`. Test jobs also record pytest's per-test durations
  from a JUnit report (`unit-report.xml`, `integration-report.xml`, `stress-report.xml`). The "Upload timing record"
  step then uploads the record as a workflow artefact named `ci-timing-<workflow>-<job>-<os>`, for example
  `ci-timing-test-unit-unit-macos-latest`, kept for 30 days. Records are never committed and are never a ledger.
- On a pull request the gate runs in `compare` mode: it reads the last 5 records for the same artefact name from
  successful `main` runs with the job's `GITHUB_TOKEN` (`actions: read`) and compares against their median.

### Defaults and why

The thresholds are flags spelt out in every workflow command, so changing one is a committed edit to the workflow file:
`--runs 5 --tolerance 0.5 --min-delta 120 --ceiling 600`.

- A job fails the relative check only when its duration exceeds the baseline median by more than 50 % AND by more than
  120 s, that is `duration > median * 1.5` and `duration - median > 120`.
- Every job also fails above an absolute ceiling of 600 s, whatever the baseline.
- While fewer than 5 records exist (cold start, a new job, or records older than 30 days) the relative check is skipped
  and only the ceiling applies.

These values were calibrated on the 13 successful `main` pushes of 2026-10-24 to 2026-10-29. Replaying a rolling median
of the previous 5 runs, they flag exactly the real regressions: the unit slow-down at `e0dfd24` (`432 s` against a
median of `256 s` on Ubuntu, `609 s` against `218 s` on macOS), the stress spike at `fbd3b35` (`430 s` against `137 s`)
and the lasting stress step at `acb5dc0` (`278 s` against `156 s`), while the four most recent `main` runs pass. A
tighter `25 %` / `30 s` setting flagged six runs, including healthy noise: hosted runners are noisy (the hygiene job
alone ranged from 21 s to 46 s), which is why a minimum absolute delta is needed. The 600 s ceiling clears every job on
today's `main` (the largest of the last six runs of any job is 278 s) and would have failed the 682 s unit run on macOS.

### Reading the gate output

- `::notice::Cold start ...`: not enough baseline records; only the ceiling was checked.
- `Timing gate passed: ...`: the duration, the baseline median and the thresholds, all within limits.
- `::error::Timing regression ...` or `::error::Job took ... above the 600 s ceiling`: the job failed the gate.
- `::warning::Timing regression accepted by the 'timing-regression-accepted' label ...`: a relative failure was
  overridden (see below).
- `::warning::Could not fetch the timing baseline ...`: the GitHub API or an artefact could not be read; the gate treats
  this as a cold start, so an API outage never blocks a pull request.

Test jobs also print a per-test report on every pull-request run, and the gate writes it to the job summary. It lists up
to 10 tests (`classname::name`) whose time grew by more than 50 % and more than 5 s against their baseline median, with
the baseline median, the current time and the growth in seconds. The report is appended to a failure message but never
fails a job on its own.

A relative failure that the per-test report does not explain (no test listed) is most likely hosted-runner noise: re-run
the failed job first, and keep the label below for a deliberate slow-down.

### Accepting a deliberate slow-down

Add the `timing-regression-accepted` label to the pull request. The label event re-runs the pull-request workflows, and
the relative-check failure becomes a printed warning that names the label, so every override stays visible on the pull
request. The ceiling cannot be overridden by label: it changes only through a committed edit to the `--ceiling` flag in
the workflow file. That is also how to raise the ceiling for `build-images.yml` if its multi-architecture build ever
exceeds 600 s.

## Branch Protection Status Checks

Repository maintainers configuring branch protection rulesets on `main` should require the following status checks:

- `Test - Hygiene / hygiene (ubuntu-latest)`
- `Test - Unit / unit (ubuntu-latest)`
- `Test - Unit / unit (macos-latest)`
- `Test - Integration / integration (ubuntu-latest)`
- `Run Make / build (ubuntu-latest)`
- `Run Make / build (macos-latest)`

## Composite Actions

- `.github/actions/ci-timing-gate/action.yml`: Encapsulates job timing gate initialization (`phase: start`) and runtime
  budget enforcement (`phase: check`). It provides standardized execution timing across workflows:
  - **Inputs:**
    - `phase`: (required) Specifies execution phase, accepting strictly `start` or `check`.
    - `max-seconds`: (optional, default: `300`) Maximum allowable runtime budget in seconds.
  - **Prerequisites & Requirements:**
    - Calling workflows must execute `actions/checkout` before invoking this local composite action.
    - Check steps must specify `if: always()` to ensure budget enforcement and step summary recording occur even upon
      prior step failure.

## Standards Alignment

All workflows in this repository conform to the standard Holon modular CI/CD architecture derived from
[`agentic-knowledge-base/.github`](https://github.com/thomashan/agentic-knowledge-base/tree/main/.github).
