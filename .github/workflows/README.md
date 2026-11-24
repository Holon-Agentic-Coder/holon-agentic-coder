# GitHub Actions Workflows

This directory contains the CI/CD workflows for the `holon-agentic-coder` project.

## Workflows

- `make.yml`: Runs on pushes to `main` and on pull requests into any base branch across both `ubuntu-latest` and
  `macos-latest`. It verifies `uname -m`, `make help`, and default `make`.
- `test-hygiene.yml`: Runs on pushes to `main` and on pull requests into any base branch on `ubuntu-latest`. It executes
  fast repository hygiene in an isolated job:
  - Verifying lockfile currency with `uv lock --check` and `git diff --exit-code uv.lock`.
  - Running static linting with `uv run ruff check .`.
  - Checking formatting with `uv run ruff format --check .`.
  - Checking markdown formatting with Prettier (`npx prettier --check "**/*.md"`).
  - Validating repository cleanup via `uv run task clean`.
- `test-unit.yml`: Runs on pushes to `main` and on pull requests into any base branch across both `ubuntu-latest` and
  `macos-latest`. It executes unit tests
  (`uv run pytest -m "not integration_test and not stress" --junitxml=unit-report.xml`) using cached `uv` dependencies.
- `test-stress.yml`: Runs on pushes to `main` and on pull requests into any base branch on `ubuntu-latest`. It runs the
  stress suite (`uv run pytest -m stress`) -- the workspace-survival test and the hermetic canary guard, both of which
  re-run the unit suite in a child process -- measuring the execution time explicitly in its own workflow.
- `test-integration.yml`: Runs on pushes to `main` and on pull requests into any base branch on `ubuntu-latest`. It
  builds all sandbox Docker images via `./apps/sandbox-executor/build_all_images.sh --output-log` and executes
  integration tests that require Docker services. For why containerized tests run exclusively on Linux, see
  [macos-docker.md](../macos-docker.md).
- `docker-build.yml`: Runs on pushes to `main` and on pull requests into any base branch. It builds Docker images with
  multi-architecture Buildx caching, incorporating timing gate checks.
- `build-images.yml`: Manual dispatch workflow to build and verify Docker container images with multi-architecture
  Buildx caching.
- `ci-durations.yml`: Runs daily at 04:23 UTC and on manual dispatch, with read-only permissions (`actions: read`,
  `contents: read`). It builds the CI duration report described below and uploads it as the `ci-durations` artefact.

### Pull requests into any branch

None of the six pull-request workflows filters the `pull_request` trigger by base branch. A decomposed plan runs child
intents whose pull requests are stacked on the plan branch that listed them, not `main`; with a `main`-only filter they
received no checks, and the Stage 4 review loop halted with `test results not measured` even when every reviewer
approved. The `push` trigger stays on `main`, so a commit on an intent branch is checked once, by its pull request, and
a push to `main` once, by `push`. The timing gate on a stacked pull request still compares against the `main` baselines.
`apps/sandbox-executor/tests/test_workflow_triggers.py` fails if a base-branch filter returns to any `pull_request`
trigger or if `push` widens beyond `main`.

Every job of every workflow ends with the CI timing regression gate described below. The six pull-request workflows also
trigger on the `labeled` and `unlabeled` activity types, so every label event starts them. The bookkeeping-only push
gate described below then decides what the jobs do: adding or removing a label that a workflow reads (the override label
`timing-regression-accepted` or `module-budget-change`) runs every job in full, and any other label skips the expensive
steps when the same job already passed on the same commit. All six grant their job `actions: read`: the timing gate
reads the baseline artefacts with it, and the bookkeeping gate reads the earlier runs and their jobs.

## Bookkeeping-Only Push Gate

The flow pushes plan files, execution reports and ledger rows to a pull request several times. Such a push cannot change
a test result, yet every push used to run every job in full. `scripts/ci_bookkeeping_gate.py`, called through the
composite action `.github/actions/bookkeeping-gate`, lets a job skip its expensive steps after such a push. Every job
still starts, reports under its usual name and ends green, so required status checks are satisfied. The script uses the
standard library only and runs on the runner's own `python3`, with two sibling modules: `ci_bookkeeping_api.py` (the
event of the run and the REST reads) and `ci_bookkeeping_chain.py` (the walk through earlier runs). It also imports
`scripts/ci_timing_gate.py`, the source of the request builder, the opener, the override label name and the step-summary
writer.

### Bookkeeping paths

A path is bookkeeping when it lies under one of three roots, `plans/`, `executions/` or `holon-knowledge/`, and names a
markdown, JSON or JSONL file: its name ends in `.md`, `.json` or `.jsonl`, in lower case. Paths are matched on whole
components: the first component must be exactly one of the roots and a file name must follow it. So `plans_helper.py`,
`docs/plans/x.md` and a file named `plans` are not bookkeeping.

The three file types are the ones that the hygiene job still checks on a skipped push (see "What still runs on a skip").
A file of any other type under a root, such as `plans/tool.py` or `executions/run.sh`, is not bookkeeping, because a
skipped job would not check it at all.

A rename counts with both of its names, so a file that moves into or out of a root, or to another file type, is not
bookkeeping.

### When a job skips

"Bookkeeping gate" is the first step after the checkout in every job of the six pull-request workflows. It says "skip"
only on positive evidence, and every doubt or error runs the full job. All of the following must hold:

- The run is the first attempt of a `pull_request` run, and its action is `synchronize` (a push), `labeled` or
  `unlabeled`.
- For a push: the new head is strictly ahead of the previous head, the comparison lists fewer than 300 files, and every
  changed path is bookkeeping.
- For a label event: the label is not one that a workflow reads.
- The same job really succeeded in the newest earlier run of the same workflow on the same branch, and that run was on
  the previous head (for a label event: on the current head). When that run was itself skipped, the gate walks back
  through the skipped runs to the last real success, at most 20 runs, and then requires that every path changed between
  that commit and the new head is bookkeeping.
- No run on the way names more than one pull request.

### The marker step

A job that the gate skipped also concludes `success`, so the conclusion of an earlier job proves nothing. Every job
therefore ends with the step `Full run completed`, which runs only when the gate said "run" and every earlier step
passed. An earlier job counts as a real success only when this step succeeded. Runs from before the gate existed have no
marker step, so the first bookkeeping-only push after the gate reaches a pull request runs in full once
(`previous-run-unverified`).

### Reading the gate output

The gate prints one line in the "Bookkeeping gate" step log and adds the same line to the step summary, for example
`bookkeeping-gate: decision=RUN rule=non-bookkeeping-change detail=scripts/x.py`. The rule is the reason for the
decision.

Rules that skip:

- `bookkeeping-only`: a push that changed only bookkeeping paths since the last real success of this job.
- `label-not-result-affecting`: a label event with a label that no workflow reads, on a commit this job already passed.

Rules that run the full job:

- `event-not-pull-request`: the run is a push to `main`, a manual run or any other event.
- `rerun-attempt`: the run is a second or later attempt.
- `action-not-eligible`: the pull request was opened or reopened, or the action is unknown.
- `result-affecting-label`: the label that was added or removed is one that a workflow reads.
- `missing-previous-head`: the event of a push names no previous head.
- `incomplete-event`: the event or the environment lacks the head commit, the branch, the workflow file, the job name,
  the run number, the run attempt or the label name.
- `force-push-or-diverged`: the new head is not strictly ahead of the commit it is compared with (a force push or a
  rebase).
- `diff-too-large`: the comparison lists 300 files or more, which is the most the API returns, so the list may be cut
  short.
- `non-bookkeeping-change`: a changed path lies outside the three roots or is not a markdown, JSON or JSONL file; the
  detail names the first such path.
- `previous-run-missing`: there is no earlier run of this workflow on the branch, the newest one is on another commit,
  it holds no single job with this job's name, or every earlier run was skipped.
- `shared-head-branch`: an earlier run on the way names more than one pull request, so several open pull requests share
  the head branch and the run may belong to another one; the detail names the run and the count.
- `previous-run-not-finished`: an earlier run on the way is still queued or in progress.
- `previous-run-not-successful`: the job failed or was cancelled in an earlier run; the detail names the conclusion.
- `previous-run-unverified`: the job succeeded in an earlier run that has no marker step.
- `chain-too-long`: more than 20 skipped runs in a row lie between this run and the last real success.
- `api-error`: a GitHub API call failed or answered in an unexpected shape, or the gate itself failed; the detail names
  the error class and never its text.

### What still runs on a skip

The steps that only record a time ("Record job start time", "CI Timing Gate (Start)") still run. The hygiene job also
keeps "Set up Node.js" and runs `make lint-json` (jq over every JSON and JSONL file) and `make lint-md` (Prettier over
every markdown file), so the pushed plans, reports and ledger rows are still checked. The timing checks are skipped: a
job of a few seconds says nothing about a slow-down.

### Forcing a full run

- Re-run the job or the workflow: a second attempt always runs in full.
- Close and reopen the pull request.
- Start the workflow by hand (`workflow_dispatch`).
- Push any change that is not a bookkeeping path, for example a file outside the three roots.

An empty commit does not force a full run: it changes no path, so it counts as bookkeeping-only.

### Limits

- A skipped run does not test a base branch that moved since the last real success. The next push with another change, a
  re-run and the push to `main` after the merge all run in full.
- Tests that read the real plans and ledgers do not run on a skipped push. They run on the next full run and on `main`.
- The gate finds the earlier runs by workflow and head branch, not by pull request, so it assumes one open pull request
  per head branch. It runs in full when an earlier run names several pull requests (`shared-head-branch`), but the API
  lists only the open ones: after a second pull request from the same branch is closed, the gate may count a run of that
  pull request as an earlier run of this one.
- The gate does not notice a retargeted base. After the base branch of a pull request is changed, a bookkeeping-only
  push can still skip on the strength of a real success against the old base. Close and reopen the pull request to test
  against the new one.
- Skipped jobs appear as very short points in the CI duration report.
- The marker step proves a real success only while no step of the job uses `continue-on-error`. The wiring test rejects
  that key in the six workflows.

### Checklist for later changes

- A new job in one of these workflows, or a new pull-request workflow, needs three things: the "Bookkeeping gate" step
  (`id: bookkeeping`) directly after the checkout, the guard `if: steps.bookkeeping.outputs.skip != 'true'` on every
  later step that is not cheap, and `Full run completed` as its last step with exactly that guard. The job also needs
  `actions: read`. `apps/sandbox-executor/tests/test_ci_bookkeeping_wiring.py` enforces this.
- A new label that a workflow reads must be added to `RESULT_AFFECTING_LABELS` in `scripts/ci_bookkeeping_gate.py`. The
  same test file scans the workflows for label names and fails on one that is missing.
- A new rule name must be described in the list above. The same test file compares the list with the constants of the
  scripts.

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

## CI Duration Report

`scripts/ci_durations.py` shows how long every CI job took over time, so a slowdown is visible as a trend with a link to
the commit that caused it. Where the timing gate fails one slow job, the report shows the history behind it. It uses the
standard library only and runs on the runner's own `python3` as `python3 -m scripts.ci_durations`, with focused sibling
modules: `ci_durations_api.py` (paginated REST access), `ci_durations_collect.py` (job records), `ci_durations_stats.py`
(baselines and ceilings), `ci_durations_html.py` and `ci_durations_svg.py` (rendering).

### What is collected

For every completed run created in the last N days (default 30), the script reads `actions/runs` and
`actions/runs/{id}/jobs?filter=all`, so jobs of earlier attempts are included. Runs of `ci-durations.yml` itself, runs
still in progress, skipped jobs and jobs that never started are left out. Per job it records:

- duration: `completed_at - started_at` of the job;
- queue time: `started_at - created_at` of the job, never below 0;
- conclusion: failed jobs (`failure`, `timed_out`) and cancelled jobs are kept and marked;
- run attempt: a job of attempt 2 or later is a re-run;
- head SHA, branch, event and runner OS (the job's first runner label, for example `ubuntu-latest`);
- PR number: the first pull request of the run whose base is this repository. The API lists no pull request for a run
  from a fork, so such points show the branch and no PR link;
- ref class: `main` for a push to `main`, `pr` for a pull-request head, `other` for everything else (dispatch and
  schedule runs, and pushes to branches other than `main`).

The baseline of a point is the median of the previous 5 successful `main` runs of the same workflow, job and runner OS
(the current point excluded), as the timing gate compares against `--runs 5`. It is empty while fewer than 5 exist. The
ceiling is read from the committed workflow file: the `--ceiling` flag of `scripts/ci_timing_gate.py`, or the
`max-seconds` input of a `ci-timing-gate` `phase: check` step (300 when omitted). Where neither is found, no ceiling is
shown.

### Outputs

The `ci-durations` artefact (kept for 30 days) holds:

- `ci-durations.html`: one self-contained page with no external assets and no script, so it opens offline. Each job and
  runner OS has a chart of duration over time: `main` points are circles, pull-request heads squares, other runs
  triangles, failures hollow red, cancelled jobs grey and re-runs ringed, with the rolling-median line, the dashed
  ceiling and a strip of queue time below run time. Every point links to its run and shows a tooltip; the table under
  each chart links every point to its run, commit and pull request.
- `ci-durations.json`: every point with the fields above plus `baseline_median` and `ceiling`, under
  `schema_version: 1`.

The job summary lists up to 10 jobs whose latest `main` duration is furthest above its baseline, with the median, the
delta, the ratio and the ceiling. The report never fails on a slow job: failing is the timing gate's role.

### Refreshing and reading it

- Wait for the daily run, or open the Actions tab, choose "CI Durations" and "Run workflow" (optionally with another
  number of days), or run `gh workflow run ci-durations.yml -f days=14`.
- Download the report with `gh run download <run-id> -n ci-durations` and open `ci-durations.html` in a browser.
- Run it locally from the repository root:
  `GITHUB_TOKEN=$(gh auth token) python3 -m scripts.ci_durations --repo OWNER/NAME --out-dir ci-durations`. Without a
  token it exits with an error and writes nothing: the report shows real data only.

Each run makes one request per 100 runs plus one per run for its jobs. `--max-runs` (default 500) keeps that well below
the `GITHUB_TOKEN` limit of 1000 requests an hour. When it, or the API's cap of 1000 results per listing, cuts the
window short, the report and the summary say so with a warning. Runs whose jobs cannot be read (for example once the
rate limit is hit) are merged into one warning that counts them and names the first five.

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
- `.github/actions/bookkeeping-gate/action.yml`: Asks `scripts/ci_bookkeeping_gate.py` whether the job may skip its
  expensive steps (see "Bookkeeping-Only Push Gate" above). It never fails a job: when the script cannot run, the action
  prints a warning and the full job runs.
  - **Inputs:**
    - `job-name`: (required) The job's base name as the jobs API shows it, without the matrix part, for example `unit`
      or `Workspace survival`.
    - `os`: (optional, default: empty) The runner label of a matrix job, for example `ubuntu-latest`.
  - **Outputs:**
    - `skip`: `true` when the job may skip its expensive steps, `false` in every other case.
  - **Prerequisites & Requirements:**
    - Calling workflows must execute `actions/checkout` before invoking this local composite action, and the step needs
      `id: bookkeeping`.
    - The job must grant `actions: read` and `contents: read`.
    - Guards are written as `steps.bookkeeping.outputs.skip != 'true'`, never as `== 'false'`, so a missing output runs
      the step. A step that must also run after a failure combines both:
      `if: always() && steps.bookkeeping.outputs.skip != 'true'`.

## Standards Alignment

All workflows in this repository conform to the standard Holon modular CI/CD architecture derived from
[`agentic-knowledge-base/.github`](https://github.com/thomashan/agentic-knowledge-base/tree/main/.github).
