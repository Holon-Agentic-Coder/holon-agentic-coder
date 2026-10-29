# Plan for I-1793246551-ci-timing-regression-gate

- **Plan ID:** P-1793246567-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-29T04:02:47.589Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A. Two clarifications that stay inside the stated goal are recorded as plan
  decisions: (a) `build-images.yml` is manual-dispatch only, so its job records on a successful dispatch from `main`
  rather than on a push (adding a push trigger would multiply the most expensive build for no benefit); (b) the override
  label is read from the pull-request event payload, so the pull-request triggers gain the `labeled` and `unlabeled`
  activity types; without them, adding the label could never reach a run, because re-runs replay the original payload.
- **Exploration stance:** balanced. The pure gate logic is well understood exploit work, but three facts are uncertain
  and only cheap to check up front: the artefact REST API shape and redirect behaviour, the `python3` version available
  on the `make` and `build-images` runners (no `uv` there), and the label-trigger semantics. Step 1 checks them before
  any code is written.
- **Safety priority level:** elevated
- **Priority Justification:** The change edits every CI workflow (config files with a large blast radius: a broken
  workflow blocks every pull request), grants `actions: read` to the job token, and pushing it needs the `workflow`
  token scope (goal 10). `holon-config/world/constraints.md` section 1 (commit boundaries) and section 3 (ledger
  immutability), and `docs/safety.md` (git as the safety boundary, sandbox network isolation for tests) all apply. There
  are no secrets and no irreversible operations, so the level is elevated rather than critical.

## Exploration

- **Proportion of steps that are exploratory:** 0.14 (Step 1 of 7 is BALANCED information gathering; the rest are
  EXPLOIT)
- **Justification:** The intent already carries calibrated thresholds and a replay, so most uncertainty is removed. The
  remaining unknowns are about the runtime environment of GitHub Actions; a wrong guess there produces a gate that fails
  every pull request on arrival, so one short checking step pays for itself.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.72  |
| entropy_pred        | 4.0   |
| impact_pred         | 80.0  |
| cost_pred           | 18.0  |
| learning_value_pred | 6.0   |
| ev_pred             | 41.4  |

`EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`

`EV = 0.72 * 80.0 + 0.5 * 6.0 - 0.3 * 4.0 - 18.0 = 57.6 + 3.0 - 1.2 - 18.0 = 41.4`

### Strategy Rationale

One plan, one execution branch, no sub-intents. The intent's own Q&A states that recording, comparison, explanation,
ceiling and override form one gate in one script, and the work fits well inside the entropy budget (`4.0` predicted
against `12.0`). Splitting would create two branches that both edit the same six workflow files, raising conflict
likelihood without lowering risk.

The order is chosen so that the riskiest unknowns are resolved first (Step 1), the pure logic and its tests are built
and green before any workflow is touched (Steps 2 and 3), and the workflow edits (Step 4, the bottleneck) are made
against an already-tested command-line interface. Docs (Step 5) describe the final flags, validation (Step 6) runs every
required check, and Step 7 commits and pushes, reporting a missing `workflow` scope as a blocker per goal 10.

Aggregation of the overall metrics:

- **p_success_pred (0.72):** qualitative, not the product of step probabilities, because Steps 2, 3, 5 and 6 are locally
  retryable inside the execution. The bottleneck is Step 4 (`0.80`, workflow syntax, permissions, Prettier YAML
  formatting and label-trigger behaviour cannot be exercised locally) together with Step 7 (`0.80`, the push may be
  refused for lack of the `workflow` scope, which goal 10 turns into a reported blocker rather than a silent drop).
  `0.80 * 0.90` for the two hard-to-retry steps gives about `0.72`.
- **entropy_pred (4.0):** sum of step entropies `0.2 + 1.2 + 0.6 + 1.3 + 0.3 + 0.2 + 0.2 = 4.0`. Using the bootstrap
  weights on a 0-10 scale: `SSA = 5` (nine files, six of them CI configuration, about 700-900 inserted lines), `IRR = 1`
  (pure additions; reverting is a revert commit), `CL = 3` (workflow files are shared by concurrent intents), `SER = 2`
  (network code exists but tests use fakes), `NOV = 4` (first CI-timing gate in this repository), so
  `0.30 * 5 + 0.25 * 1 + 0.20 * 3 + 0.15 * 2 + 0.10 * 4 = 1.5 + 0.25 + 0.6 + 0.3 + 0.4 = 3.05`, rounded up to `4.0` for
  the CI blast radius. Recent ledger rows show this agent over-predicts entropy (4.2 predicted, 0.79 actual), so `4.0`
  is deliberately conservative.
- **impact_pred (80.0):** qualitative. The gate closes a demonstrated hole (a 682 s unit run reached `main` with nothing
  failing) for every CI job, and the per-test report turns future slow-downs into actionable test names. Step impacts
  sum to 85; the overall value is held at 80 because the gate is cold for its first five main pushes (ceiling only).
- **cost_pred (18.0):** sum of step costs `1.5 + 6.0 + 4.0 + 3.5 + 1.0 + 1.5 + 0.5 = 18.0`.
- **learning_value_pred (6.0):** the first artefact-backed baseline in this repository, real per-test timing data, and a
  test of whether the `workflow`-scope push path works for the executor.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` section 1: work only on the execution branch under this plan's prefix; never
    commit to `main`.
  - `holon-config/world/constraints.md` section 3 and `docs/safety.md` invariant 6: the ledgers under
    `holon-knowledge/ledger/` stay append-only; the timing records are workflow artefacts, never a file or a ledger.
  - `holon-config/world/ruleset.md` section 2: PEP 8, typed public functions, docstrings on every module, class and
    public function.
  - `holon-config/world/ruleset.md` section 3: pytest only, tests under `apps/sandbox-executor/tests/`; the new test
    file is declared here explicitly.
  - `docs/safety.md` sandboxing model: no network in tests; the gate's network access sits behind injectable functions
    and the tests use fakes only.
  - Intent goal 11: Australian English, no new dependencies, `uv run task test`, `uv run task lint`,
    `uv run task lint-docs`, target-native paths only, and the touch set.
- **Potential violations or edge cases:**
  - Touching a file outside the touch set (for example adding `--junitxml` to `pyproject.toml` or the Makefile, or a
    helper module under `apps/`).
  - A workflow edit that breaks every pull request (YAML error, wrong expression, missing `contents: read` once
    job-level permissions are set, script crash on a runner whose `python3` is older than expected).
  - The gate failing on its own arrival (it must not: no artefacts exist yet, so every job is in cold start and only the
    600 s ceiling applies, which today's main passes with a maximum of 278 s).
  - Shell injection through pull-request label names, if labels were interpolated into a `run:` command.
  - Leaking the token to the artefact storage host on the download redirect.
  - Referencing harness paths (`.beans/`, `todo/`, `.agents/`, bean or review ids) in a written file.
- **Mitigations built into the plan:**
  - The touch set is checked with `git diff --name-only` before the commit in Step 6; any file outside it aborts the
    commit.
  - Only a boolean computed by a GitHub expression (`contains(github.event.pull_request.labels.*.name, ...)`) reaches
    the script, through an environment variable, never a label string.
  - The token is passed through the `GITHUB_TOKEN` environment variable, never on the command line, and is attached with
    `add_unredirected_header`, so it is not forwarded on the redirect to the artefact archive host.
  - Fetch failures degrade to the cold-start path with a visible warning; the job then still enforces the ceiling.
  - The script is written to run on any `python3` from 3.9 upwards (`from __future__ import annotations`, no `match`, no
    runtime `X | Y` unions), so the `make` and `build-images` jobs need no extra set-up step.
  - YAML formatting is checked with the same Prettier pass that `make lint` runs in the hygiene job.
- **Residual risk accepted (and why):** The workflows can only be exercised for real on GitHub; local validation covers
  the script, the tests, lint and YAML formatting, but not the Actions runtime. The first pull-request run of this
  change is the real integration test, and it runs in cold start (ceiling only), so a wiring error shows as a visible
  notice or an obvious step failure rather than a silent pass. The `build-images` job builds multi-architecture images
  with QEMU and has never run, so it might exceed the 600 s ceiling on its first dispatch; it is manual-dispatch only,
  so it blocks no pull request, and the README tells the maintainer how to raise its ceiling flag through a committed
  edit.
- **Allocated Entropy Budget:** 12.0
- **Predicted Plan Entropy:** 4.0
- **Budget Compliance:** The strategy fits within budget (`4.0 <= 12.0`), so the plan runs as one execution with no
  sub-intent list.

## Plan Description & Strategy

Build one standard-library script, `scripts/ci_timing_gate.py`, with two modes, wire it into every job of every
workflow, test it with fakes, and document it.

**Measurement.** Each job's first step writes `CI_TIMING_START=$(date +%s)` to `$GITHUB_ENV`. The gate step near the end
of the job reads that value and the current time, so the duration covers the first step to the gate step, as goal 1
requires. Main records and pull-request measurements are taken in exactly the same way, so they are comparable; they are
slightly shorter than the API wall clock used for calibration (runner set-up and the upload step are excluded), which
only makes the defaults a little more lenient.

**Modes.** The workflow selects the mode with an expression: `compare` on `pull_request`, `record` otherwise.

- `record` builds a JSON record (job, OS, workflow, duration in seconds, commit, run id, and, when `--junit` is given, a
  map from test id to seconds), writes it to `--output`, prints a one-line summary and enforces the ceiling. An
  `actions/upload-artifact` step then uploads the record, only when the run is a push to `main` (or, for `build-images`,
  a dispatch on `main`). Because upload is the step after a successful gate, an artefact exists only for a successful
  main run.
- `compare` measures in the same way, fetches up to N baseline records for the same artefact name through the injected
  fetcher, applies the decision rules, prints the per-test report on every run, writes the same report to
  `$GITHUB_STEP_SUMMARY` when that variable is set, and exits 1 only on failure.

**Decision rules (pure function).**

1. Over the ceiling: fail, whatever the label (the message says the ceiling cannot be overridden by label and names the
   flag in the workflow file).
2. Fewer than N baseline records: print a `::notice::` saying the relative check is skipped (cold start) and pass.
3. Relative regression when `duration > median * (1 + tolerance)` AND `duration - median > min_delta`; otherwise pass.
4. A relative regression with the override label present: print a `::warning::` that names `timing-regression-accepted`
   and pass; without the label: fail with the per-test report appended.

**Per-test report.** Test ids are `classname::name` from the JUnit XML. For each test in the current run that also
appears in at least one baseline record, compare with the median of its baseline times; keep tests whose time grew by
more than 50 % and more than 5 s; sort by absolute growth and list the top 10. The report never fails the job on its
own.

**Artefact naming.** `ci-timing-<workflow-file-stem>-<job-id>-<os>`, for example `ci-timing-test-unit-unit-macos-latest`
and `ci-timing-make-build-ubuntu-latest`. The workflow stem is needed because `make.yml` and `build-images.yml` both
have a job called `build`. Artefacts keep a 30-day retention: main receives several pushes a day, so five records are
always far inside that window, and an idle month falls back to the safe cold-start path.

**Baseline fetch (injectable).** List `GET /repos/{repo}/actions/artifacts?name=<name>&per_page=100`, keep entries that
are not expired and whose `workflow_run.head_branch` is `main`, newest first, take N, download each
`archive_download_url` zip, and read `record.json` from it in memory with `zipfile`. The token is read from the
`GITHUB_TOKEN` environment variable; every request has a 30 s timeout. Any `URLError`, `HTTPError`, bad zip or bad JSON
is reported as a warning and treated as "no baseline" (cold start), so a GitHub API outage cannot block a pull request
and the ceiling still applies.

**Configuration.** `--runs` (default 5), `--tolerance` (default 0.5), `--min-delta` (default 120), `--ceiling` (default
600), `--job`, `--os`, `--workflow`, `--start`, `--junit`, `--output`, `--repo`, `--override-label-present`. The
workflow commands spell out all four thresholds explicitly, so a change to any of them is a visible committed edit to
the workflow file, as goal 7 requires; `pyproject.toml` and the Makefile are not touched.

---

## Step 1: Confirm the Actions runtime facts the gate depends on

- **Sub‑intent recommendation:** NO
- **Reasoning:** A short, read-only check that removes the three facts most likely to make the gate fail on arrival; it
  produces no artefact other than notes in the execution record.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** (a) the artefact list endpoint filtered by `name` returns `workflow_run.head_branch` and
  `expired`, and `archive_download_url` answers with a redirect to a storage host that rejects or must not receive the
  `Authorization` header; (b) `python3` on `ubuntu-latest` and `macos-latest` is at least 3.9 without any set-up step;
  (c) a pull-request re-run replays the original event payload, so labels added later are visible only with the
  `labeled` and `unlabeled` activity types.
- **Learning target:** The exact API fields and redirect behaviour, the minimum Python syntax level the script may use,
  and whether label-type triggers are needed.
- **Maximum acceptable cost for this learning:** 1.5 cost units (about 10 % of the plan); if a fact cannot be confirmed
  from the GitHub documentation available in the sandbox, adopt the conservative assumption listed below and continue.

### Intent & Git Integration

- **Step Intent:** Fix the design assumptions before writing code, without touching any file.
- **Git branch:**
  `I-1793246551-ci-timing-regression-gate/P-1793246567-claude-agent-claude-opus-5-5/E-{timestamp}-ci-timing-gate/_` (the
  single execution branch for all steps)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Read the six workflow files and `.github/workflows/README.md` again on the execution branch after rebasing from the
   plan branch, in case a concurrent change has altered them since planning.
2. Confirm that no test parses the workflow files' pytest commands (at planning time none does; the shared
   `hermetic_fixtures.py` builds its own command), so adding `--junitxml` to `test-unit.yml` cannot break a test.
3. Check the GitHub REST documentation (if reachable) for the artefact list and download endpoints; otherwise adopt the
   conservative assumptions: filter by name, verify `head_branch` client-side, attach the token with
   `add_unredirected_header`, and treat any HTTP error as cold start.
4. Adopt the conservative Python level (3.9-compatible syntax) unless runner image documentation confirms a newer
   `python3` on both runner images.
5. Adopt the `labeled` and `unlabeled` activity types for the pull-request triggers unless the documentation shows that
   a re-run refreshes labels.
6. Record the conclusions as notes for the execution record; do not write any file in this step.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` sandboxing model (no external network unless allowed); intent goal 8 (never real
  GitHub API calls in tests).
- **Potential failure modes for this step:** documentation unreachable from the sandbox; stale assumptions.
- **Guardrails and early‑abort checks:** never call the GitHub API with a token from the sandbox; the conservative
  assumptions are safe defaults, so an unreachable source never blocks the plan.

### Success & Discard Criteria

- **Success:** Each of the three facts is either confirmed or replaced by its conservative assumption, and the choice is
  noted.
- **Discard:** Stop the step once 1.5 cost units are spent and proceed with the conservative assumptions.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.2   |
| impact_pred         | 5.0   |
| cost_pred           | 1.5   |
| learning_value_pred | 6.0   |
| ev_pred             | 6.19  |

### Step Metrics Rationale

`EV = 0.95 * 5.0 + 0.5 * 6.0 - 0.3 * 0.2 - 1.5 = 4.75 + 3.0 - 0.06 - 1.5 = 6.19`. Read-only, so entropy is minimal; its
value is in the learning (it decides syntax level, trigger types and download headers) rather than in direct impact.

---

## Step 2: Implement scripts/ci_timing_gate.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** One new standard-library module of roughly 300 lines with mostly pure functions; it is the core of the
  gate, but small and fully testable with fakes, so a separate branch adds overhead without reducing risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Provide the record, compare, explain, ceiling and override logic behind a small command-line
  interface, with network and artefact access behind injectable functions.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Module docstring in Australian English explaining the gate, both modes and the defaults;
   `from __future__ import annotations`; imports from the standard library only (`argparse`, `io`, `json`, `os`,
   `statistics`, `sys`, `time`, `urllib.request`, `urllib.error`, `xml.etree.ElementTree`, `zipfile`, `dataclasses`,
   `typing`).
2. Small frozen dataclasses: `TimingRecord` (job, os, workflow, duration in seconds, commit, run id, tests map),
   `Thresholds` (runs, tolerance, minimum delta, ceiling) and `Verdict` (passed flag, list of message lines).
3. Pure helpers, each short and low in branching:
   - `artefact_name(workflow, job, os)` returns the `ci-timing-...` name.
   - `parse_junit(path)` walks every `testcase` element under any wrapper and returns `classname::name` mapped to its
     `time` attribute as a float (missing or empty time counts as 0).
   - `rolling_median(values)` wraps `statistics.median` and returns `None` for an empty list.
   - `exceeds_relative(duration, median, thresholds)` returns true only when both the tolerance and the minimum delta
     are exceeded.
   - `top_offenders(current, baselines, limit=10, growth=0.5, min_seconds=5.0)` returns sorted rows of test id, baseline
     median, current time and growth.
   - `format_report(rows)` renders the per-test table, or a line saying no test grew beyond the thresholds.
   - `evaluate(duration, baselines, thresholds, override)` applies the four decision rules in order and returns a
     `Verdict`, using early returns rather than nested branches.
4. Injectable I/O, each a plain function passed in by `main`:
   - `fetch_baseline(repo, name, runs, token, opener)` lists artefacts, filters and downloads them as described in the
     plan description, and returns a list of `TimingRecord`; `opener` defaults to a function built on `urllib.request`
     with a 30 s timeout and the token attached through `add_unredirected_header`.
   - `safe_fetch(...)` wraps the fetcher and converts `URLError`, `HTTPError`, `zipfile.BadZipFile`, `KeyError` and
     `ValueError` into an empty list plus a `::warning::` line.
   - `clock()` returns `time.time()` and is injectable for tests.
5. `run_record(args, clock)` builds the record, writes it to `--output` (creating the parent directory), prints a
   summary and returns the ceiling verdict.
6. `run_compare(args, clock, fetcher)` builds the current measurement, fetches baselines, evaluates, prints the per-test
   report on every run, appends the report to the failure message, writes the report to `$GITHUB_STEP_SUMMARY` when it
   is set, and returns the verdict.
7. `main(argv, clock, fetcher)` parses arguments with `argparse` (all four thresholds as flags with the documented
   defaults; `--override-label-present` accepts `true` or `false`), dispatches to the mode, prints the verdict lines and
   returns exit code 0 on pass, 1 on gate failure and 2 on bad input (for example a missing or non-numeric `--start`).
   The script ends with the usual `if __name__ == "__main__"` guard that raises `SystemExit(main(...))`.
8. Messages: cold start uses `::notice::`, override uses `::warning::` naming `timing-regression-accepted`, failures use
   `::error::` and state the duration, the median, the thresholds and, for the ceiling, that only a committed edit to
   the `--ceiling` flag in the workflow file can change it.
9. Keep every function short, typed and documented; no harness paths, bean ids or review ids anywhere in the file;
   Australian spelling in all comments, docstrings and log strings (for example "artefact", "behaviour", "summarise").

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (every workflow calls this script)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` section 2 (typing, docstrings, PEP 8); intent goals 7, 8 and 11.
- **Potential failure modes for this step:** syntax newer than the runner's `python3`; ruff rules (for example B905 on
  `zip` without `strict`) failing lint; token forwarded on redirect; a fetch error crashing the job instead of
  degrading.
- **Guardrails and early‑abort checks:** run `uv run ruff check scripts/ci_timing_gate.py` and
  `uv run ruff format --check scripts/ci_timing_gate.py` as soon as the file exists; run `python3 -I` on the module with
  `--help` to confirm it imports with no third-party packages.

### Success & Discard Criteria

- **Success:** The module imports with the standard library only, passes ruff, and `--help` lists all flags with the
  documented defaults.
- **Discard:** If the module grows past about 450 lines or needs a dependency, stop and simplify (drop the step summary
  output first) rather than expanding scope.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.2   |
| impact_pred         | 30.0  |
| cost_pred           | 6.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 21.14 |

### Step Metrics Rationale

`EV = 0.85 * 30.0 + 0.5 * 4.0 - 0.3 * 1.2 - 6.0 = 25.5 + 2.0 - 0.36 - 6.0 = 21.14`. The logic is well specified by the
intent; the risks are syntax level and download details, both bounded by Step 1. Entropy is moderate because it is a new
file with network code.

---

## Step 3: Add apps/sandbox-executor/tests/test_ci_timing_gate.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** The tests are the acceptance evidence for goals 2 to 8 and change only one new file; they must land
  with the script.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove every behaviour listed in goal 8 with pytest and fake fetchers, never touching the network,
  `gh` or the GitHub API.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Import from `scripts.ci_timing_gate`, matching the existing `test_pre_commit_check.py` pattern (`pythonpath` in
   `pyproject.toml` already contains the repository root, so no `PYTHONPATH` is needed).
2. Helper factories: a record builder, a fake fetcher returning a fixed list of records, a failing fetcher that raises
   `URLError`, a fake opener that serves an artefact listing and an in-memory zip (built with `zipfile` into `BytesIO`)
   so the real `fetch_baseline` is exercised without a network, and a fixed clock.
3. Test cases (each short and named for its behaviour):
   - rolling median for odd, even and empty lists;
   - pass inside the tolerance;
   - pass when the tolerance is exceeded but the minimum delta is not, and when the delta is exceeded but the tolerance
     is not; fail only when both are exceeded;
   - ceiling failure with a healthy baseline, with no baseline, and with the override label present;
   - cold start below N prints the notice and applies only the ceiling, including the zero-record case;
   - override turns a relative failure into a warning that names `timing-regression-accepted`;
   - top-offender report: growth thresholds (50 % and 5 s, both required), sorting, the limit of 10, tests missing from
     the baseline ignored, and the report appended to the failure message but not failing on its own;
   - JUnit parsing of a report with nested `testsuites`, missing `time` attributes and duplicate names in different
     classes, written to `tmp_path`;
   - `fetch_baseline` filters out expired and non-`main` artefacts, takes at most N, and attaches the token as an
     unredirected header (asserted on the request object passed to the fake opener);
   - a fetch error degrades to cold start with a warning rather than an exception;
   - `main` in record mode writes a record to `tmp_path` and exits 0; a missing `--start` exits 2;
   - replay of the real durations cited in the intent: the unit slow-down at e0dfd24 (432 s against a median of 256 s on
     ubuntu, 609 s against 218 s on macOS), the stress spike at fbd3b35 (430 s against 137 s) and the stress step at
     acb5dc0 (278 s against 156 s) all fail with the defaults; the cc4bf1b durations (unit 132 s and 131 s, integration
     153 s, stress 263 s, hygiene 28 s, make 5 s and 10 s) pass both against baselines built from values inside the
     cited ranges and in cold start; 682 s fails the ceiling; every cited main duration from the last six runs passes
     the ceiling. Baselines that the intent states only as a median are built as five values with that median and are
     labelled in the test as reconstructions, not as the raw series.
4. No test sleeps, spawns a process or reads real environment tokens; use `monkeypatch` to clear `GITHUB_STEP_SUMMARY`
   and set it to a `tmp_path` file where the summary is asserted.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO (failures here are fixed inside the execution)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` section 3 (pytest, location under `tests/`, declared test change);
  intent goal 8; the repository's autouse credential and `gh` write guards in `conftest.py`.
- **Potential failure modes for this step:** a test reaching the network through the default opener; a slow test
  lengthening the stress suite, which re-runs the unit selection; Australian-spelling check flags an American spelling.
- **Guardrails and early‑abort checks:** every test injects a fake opener or fetcher; run the file alone with
  `uv run pytest apps/sandbox-executor/tests/test_ci_timing_gate.py` and confirm it finishes in well under a second.

### Success & Discard Criteria

- **Success:** All new tests pass under `uv run pytest apps/sandbox-executor/tests/test_ci_timing_gate.py` and under
  `uv run task test`.
- **Discard:** If a behaviour cannot be tested without real network access, change the injection seam in Step 2 rather
  than weakening the test.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 0.6   |
| impact_pred         | 15.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 10.07 |

### Step Metrics Rationale

`EV = 0.85 * 15.0 + 0.5 * 3.0 - 0.3 * 0.6 - 4.0 = 12.75 + 1.5 - 0.18 - 4.0 = 10.07`. A new, isolated test file has low
entropy; the cost reflects the breadth of required cases, including the replay.

---

## Step 4: Wire the gate into every job of every workflow

- **Sub‑intent recommendation:** NO
- **Reasoning:** Highest-risk step (CI configuration with a large blast radius that cannot be run locally), but it is
  mechanical once the script interface is fixed, and splitting it from the script would leave either half unusable.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make every job record on successful main runs and compare on pull requests, with per-test data for
  the three test jobs and least-privilege permissions.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In each of the seven jobs (`make.yml` build on two OSes, `test-hygiene.yml` hygiene, `test-unit.yml` unit on two
   OSes, `test-integration.yml` integration, `test-stress.yml` workspace-survival, `build-images.yml` build), add a
   first step "Record job start time" that appends `CI_TIMING_START=$(date +%s)` to `$GITHUB_ENV`, before checkout.
2. Add a "Timing gate" step after the job's last existing step. It runs `python3 scripts/ci_timing_gate.py` with the
   mode chosen by the expression `github.event_name == 'pull_request' && 'compare' || 'record'`, and the flags
   `--workflow <file stem>`, `--job <job id>`, `--os <matrix.os or the literal runner label>`,
   `--start "$CI_TIMING_START"`, `--runs 5 --tolerance 0.5 --min-delta 120 --ceiling 600`,
   `--repo "$GITHUB_REPOSITORY"`, `--output ci-timing/record.json`, and, for the test jobs, `--junit <report path>`. The
   step's `env` sets `GITHUB_TOKEN: ${{ github.token }}` and `TIMING_OVERRIDE` to the boolean expression
   `contains(github.event.pull_request.labels.*.name, 'timing-regression-accepted')`, passed to
   `--override-label-present "$TIMING_OVERRIDE"`. No label text reaches the shell.
3. Add an "Upload timing record" step using `actions/upload-artifact@v4` with name `ci-timing-<stem>-<job>-<os>`, path
   `ci-timing/record.json`, `retention-days: 30`, `if-no-files-found: error`, and the condition
   `github.event_name == 'push' && github.ref == 'refs/heads/main'`; for `build-images.yml` the condition is
   `github.ref == 'refs/heads/main'` (manual dispatch on main).
4. Test commands: append `--junitxml=unit-report.xml` to the last line of `test-unit.yml` and
   `--junitxml=integration-report.xml` to the pytest line of `test-integration.yml`; reuse the existing
   `stress-report.xml` in `test-stress.yml`, placing the gate after the "Assert the stress suite really ran" step.
5. Permissions: add job-level `permissions` with `contents: read` and `actions: read` to each job that runs on pull
   requests (job-level permissions replace the workflow-level block, so `contents: read` must be repeated); leave
   `build-images.yml` permissions as they are, since that job only records and already has `actions: write`.
6. Pull-request triggers in the five pull-request workflows gain
   `types: [opened, synchronize, reopened, labeled, unlabeled]`, so adding or removing the override label re-runs the
   gate with the new label set; the existing concurrency groups already cancel superseded pull-request runs.
7. Leave every existing step, matrix and branch filter unchanged; preserve the step names that branch protection checks
   rely on (job names are not changed, so the required status check names in the README stay valid).
8. Format the YAML with Prettier and check it with the same file selection `make lint` uses.

### Dependencies & Criticality

- **Depends on:** Steps 2 and 3
- **Is Bottleneck:** YES (a broken workflow blocks every pull request; this step cannot be run locally)

### Safety & Constraint Considerations

- **Relevant rules:** intent goals 1, 2, 6, 7, 10 and the touch set; `docs/safety.md` (least privilege, blast-radius
  containment).
- **Potential failure modes for this step:** YAML or expression error; `--os` key differing between record and compare
  (wrong baseline, permanent cold start); missing `contents: read` after adding job-level permissions (checkout fails);
  the gate placed before the step whose time it must include; Prettier reformatting the YAML.
- **Guardrails and early‑abort checks:** the standard library cannot parse YAML and no dependency may be added, so
  validate with `npx prettier --check` on the six files (the same tool `make lint` uses) and review the diff line by
  line; dry-run the exact gate command lines locally in record mode against a temporary output directory with a fake
  start time to confirm the flags parse.

### Success & Discard Criteria

- **Success:** All six workflows contain the start step, the gate step and the upload step with the conditions above;
  the three test jobs pass `--junit`; Prettier reports no change; the local dry-run of each gate command exits 0.
- **Discard:** If a workflow needs an edit outside `.github/workflows/` (for example a composite action) or a change to
  `pyproject.toml` or the Makefile, drop that part and report it in the execution notes.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 1.3   |
| impact_pred         | 25.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 5.0   |
| ev_pred             | 18.61 |

### Step Metrics Rationale

`EV = 0.80 * 25.0 + 0.5 * 5.0 - 0.3 * 1.3 - 3.5 = 20.0 + 2.5 - 0.39 - 3.5 = 18.61`. The highest entropy of the plan: six
CI configuration files on a shared hot path, with behaviour that only GitHub can exercise. The cold-start design keeps
the first real run safe, which is why `p_success_pred` stays at `0.80`.

---

## Step 5: Document the gate in .github/workflows/README.md

- **Sub‑intent recommendation:** NO
- **Reasoning:** A single documentation section in an allowed file; low risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Explain the gate, its defaults and why they were chosen, how to read the report and how to accept a
  deliberate slow-down.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add a "CI timing regression gate" section: what is measured (first step to gate step), where records live (workflow
   artefacts named `ci-timing-<workflow>-<job>-<os>`, 30-day retention, never committed), and which events record or
   compare.
2. Defaults and rationale: N = 5, 50 % tolerance, 120 s minimum delta, 600 s ceiling; cite the calibration summary from
   the intent (the four historical runs flagged, the four most recent main runs passing, the 25 % / 30 s setting that
   flagged healthy noise, hosted-runner noise such as hygiene ranging from 21 s to 46 s).
3. Reading the report: the notice for cold start, the error line for a failure, the warning for an override, and the
   per-test table (test id, baseline median, current time, growth), also shown in the job summary.
4. Accepting a deliberate slow-down: add the `timing-regression-accepted` label, which re-runs the pull-request
   workflows and turns the relative failure into a visible warning; the ceiling cannot be overridden by label and
   changes only through a committed edit to the `--ceiling` flag in the workflow file (and that is how
   `build-images.yml` would be raised if its multi-architecture build exceeds 600 s).
5. Update the existing bullet for `test-unit.yml` so the quoted command includes `--junitxml=unit-report.xml`, and
   mention the `labeled` and `unlabeled` trigger types and the `actions: read` permission.
6. Australian English throughout; run Prettier on the file.

### Dependencies & Criticality

- **Depends on:** Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goals 9 and 11 (Australian English, `uv run task lint-docs`, no harness paths).
- **Potential failure modes for this step:** Prettier reflow failing `lint-docs`; American spellings; describing flags
  that differ from the workflows.
- **Guardrails and early‑abort checks:** run `npx prettier --write` then `uv run task lint-docs`; cross-check every flag
  value against the workflow diff.

### Success & Discard Criteria

- **Success:** The section exists, matches the workflow flags exactly, and `uv run task lint-docs` passes.
- **Discard:** NONE expected; if the docs check fails repeatedly, keep the content and fix formatting only.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.3   |
| impact_pred         | 5.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 4.01  |

### Step Metrics Rationale

`EV = 0.92 * 5.0 + 0.5 * 1.0 - 0.3 * 0.3 - 1.0 = 4.6 + 0.5 - 0.09 - 1.0 = 4.01`. Low-risk documentation; impact is the
reviewer's and maintainer's ability to use the override correctly.

---

## Step 6: Validate the whole change locally

- **Sub‑intent recommendation:** NO
- **Reasoning:** Required gate before committing; cheap and deterministic.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove that tests, lint, docs formatting, YAML formatting, spelling and the touch set are all clean.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. From the repository root run `uv run task test` (whole unit selection, including the new file).
2. Run `uv run task lint` and `uv run task lint-docs`.
3. Run Prettier in check mode on the six workflow files, mirroring the `lint-yaml` target that the hygiene job's
   `make lint` runs.
4. Run the repository's pre-commit check script on the changed files to catch American spellings, credentials and
   scratch files.
5. Run `git diff --name-only` against the plan branch and confirm every path is one of `.github/workflows/*.yml`,
   `.github/workflows/README.md`, `scripts/ci_timing_gate.py` and `apps/sandbox-executor/tests/test_ci_timing_gate.py`
   (plus the flow's own plan or execution records).
6. Search the changed files for `.beans`, `todo/`, `.agents`, bean ids and review ids; there must be no match.
7. Confirm the arrival property: with no artefacts available, a compare run for each cited cc4bf1b duration passes
   (covered by the replay tests; re-check the test output).

### Dependencies & Criticality

- **Depends on:** Steps 2 to 5
- **Is Bottleneck:** NO (failures loop back to the step that caused them)

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 11; `holon-config/world/ruleset.md` section 3.
- **Potential failure modes for this step:** an unrelated pre-existing failure in the suite; a concurrent change
  breaking lint on the plan branch.
- **Guardrails and early‑abort checks:** if a failure is unrelated to the touch set, confirm it also fails on the plan
  branch without this change and report it rather than editing files outside the touch set.

### Success & Discard Criteria

- **Success:** Every command exits 0 and the touch-set check shows no extra file.
- **Discard:** If fixing a failure would require an off-limits file, stop and record it as a blocker in the execution
  notes.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 0.2   |
| impact_pred         | 5.0   |
| cost_pred           | 1.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 4.19  |

### Step Metrics Rationale

`EV = 0.85 * 5.0 + 0.5 * 3.0 - 0.3 * 0.2 - 1.5 = 4.25 + 1.5 - 0.06 - 1.5 = 4.19`. Validation produces no new state; its
learning value is the confirmation that the hygiene job will pass.

---

## Step 7: Commit, push and report the workflow-scope outcome

- **Sub‑intent recommendation:** NO
- **Reasoning:** A single commit and push; the only risk is the `workflow` token scope, which goal 10 says to report
  rather than work around.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Land the change on the execution branch, keeping the workflow edits, or report a precise blocker.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Rebase the execution branch from the plan branch before committing (world constraints section 1).
2. Commit the nine files in one commit with a message describing the gate and its defaults.
3. Push the execution branch. If the push is refused because the token lacks the `workflow` scope, do not drop or split
   out the workflow edits: record the exact refusal message in the execution notes as a blocker naming the missing
   scope, and leave the commit intact on the local branch.
4. Append only the flow's own ledger rows; never edit existing ledger lines.

### Dependencies & Criticality

- **Depends on:** Step 6
- **Is Bottleneck:** YES (an unpushed branch cannot be reviewed)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` sections 1 and 3; intent goal 10.
- **Potential failure modes for this step:** push refused for the `workflow` scope; a rebase conflict in a workflow file
  touched by a concurrent change.
- **Guardrails and early‑abort checks:** never force-push and never push to `main`; on a rebase conflict, resolve by
  keeping both changes in the workflow file and rerun Step 6.

### Success & Discard Criteria

- **Success:** The branch is pushed with all nine files, or the scope blocker is reported verbatim with the commit kept.
- **Discard:** NONE; a refusal is an outcome to report, not a reason to change the content.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 0.2   |
| impact_pred         | 0.0   |
| cost_pred           | 0.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 0.44  |

### Step Metrics Rationale

`EV = 0.80 * 0.0 + 0.5 * 2.0 - 0.3 * 0.2 - 0.5 = 0.0 + 1.0 - 0.06 - 0.5 = 0.44`. Its impact is carried by the earlier
steps; its own value is learning whether the executor's token can push workflow edits.

---

## Sub-intent list

None
