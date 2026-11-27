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
  The ubuntu leg runs the whole suite in one job; the macOS leg runs as two parallel shards, each running the test files
  that `scripts/unit_test_shard.py` assigns to it by a stable sha256 hash of the file path, so every file runs exactly
  once. On a pull request the macOS shards skip their expensive steps when no changed file is platform-sensitive; see
  "macOS Relevance Gate" below.
- `test-stress.yml`: Runs on pushes to `main` and on pull requests into any base branch on `ubuntu-latest`. It runs the
  stress suite (`uv run pytest -m stress`) -- the workspace-survival test and the hermetic canary guard, both of which
  re-run the unit suite in a child process -- measuring the execution time explicitly in its own workflow.
- `test-integration.yml`: Runs on pushes to `main` and on pull requests into any base branch on `ubuntu-latest`. It
  builds all sandbox Docker images via `./apps/sandbox-executor/build_all_images.sh --output-log` and executes
  integration tests that require Docker services. For why containerized tests run exclusively on Linux, see
  [macos-docker.md](../macos-docker.md).
- `docker-build.yml`: Runs on pushes to `main` and on pull requests into any base branch. It builds Docker images with
  multi-architecture Buildx caching, incorporating timing gate checks.
- `test-ledger-invariants.yml`: Runs on pull requests into any base branch (`opened`, `synchronize` and `reopened`) on
  `ubuntu-latest`, with `contents: read` only. It runs `scripts/check_ledger_stores.py` (exactly three ledgers) and
  `scripts/check_ledger_rows_append_only.py` (no ledger row of the merge base changed, moved or disappeared on the
  head). It uses neither the bookkeeping gate nor the timing gate, because ledger-only pull requests are the ones it
  must check, so it is not one of the six gated pull-request workflows below.
- `build-images.yml`: Manual dispatch workflow to build and verify Docker container images with multi-architecture
  Buildx caching.
- `ci-durations.yml`: Runs daily at 04:23 UTC and on manual dispatch, with read-only permissions (`actions: read`,
  `contents: read`). It builds the CI duration report described below and uploads it as the `ci-durations` artefact.
- `promote-ledger.yml`: Runs on pushes to `main`, daily at 03:41 UTC and on manual dispatch (with a boolean `force`
  input), under the `promote-ledger` concurrency group. It runs `holon ledger promote` (see
  [Promotion](../../docs/ledger_fragments.md#promotion)) and pushes the ledger-only result with the maintainer-provided
  `HOLON_LEDGER_BOT_TOKEN` secret; without the secret it is a green no-op. See "Ledger Promotion" below. It has no
  `pull_request` trigger, so it is not one of the six pull-request workflows and has no bookkeeping gate.

### Pull requests into any branch

None of the six gated pull-request workflows, nor `test-ledger-invariants.yml`, filters the `pull_request` trigger by
base branch. A decomposed plan runs child intents whose pull requests are stacked on the plan branch that listed them,
not `main`; with a `main`-only filter they received no checks, and the Stage 4 review loop halted with
`test results not measured` even when every reviewer approved. The `push` trigger stays on `main`, so a commit on an
intent branch is checked once, by its pull request, and a push to `main` once, by `push`. The timing gate on a stacked
pull request still compares against the `main` baselines. `apps/sandbox-executor/tests/test_workflow_triggers.py` fails
if a base-branch filter returns to any `pull_request` trigger or if `push` widens beyond `main`.

Every job of every workflow except `test-ledger-invariants.yml` ends with the CI timing regression gate described below.
The six pull-request workflows also trigger on the `labeled` and `unlabeled` activity types, so every label event starts
them. The bookkeeping-only push gate described below then decides what the jobs do: adding or removing a label that a
workflow reads (the override label `timing-regression-accepted`, `module-budget-change` or `ci-macos`) runs every job in
full, and any other label skips the expensive steps when the same job already passed on the same commit. All six grant
their job `actions: read`: the timing gate reads the baseline artefacts with it, and the bookkeeping gate reads the
earlier runs and their jobs.

## Bookkeeping-Only Push Gate

The flow pushes plan files, execution reports and ledger rows to a pull request several times. Such a push cannot change
a test result, yet every push used to run every job in full. `scripts/ci_bookkeeping_gate.py`, called through the
composite action `.github/actions/bookkeeping-gate`, lets a job skip its expensive steps after such a push. Every job
still starts, reports under its usual name and ends green, so required status checks are satisfied. The script uses the
standard library only and runs on the runner's own `python3`, with four sibling modules: `ci_bookkeeping_api.py` (the
REST reads), `ci_bookkeeping_context.py` (the event of the run, re-exported by `ci_bookkeeping_api.py`),
`ci_bookkeeping_chain.py` (the walk through earlier runs) and `ci_main_push_gate.py` (the decision for a push to `main`,
see "Pushes to main"). It also imports `scripts/ci_timing_gate.py`, the source of the request builder, the opener, the
override label name and the step-summary writer.

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

### Pushes to main

A merge to `main` used to run every job again, although the merged pull request's head had already passed the same jobs
on the same tree. In `test-unit.yml` and `test-stress.yml` (the `GATED_WORKFLOWS` of `scripts/ci_main_push_gate.py`) a
push to `main` is therefore decided by that module instead of the rules above. It says "skip" through one of two rules.

Right after the event check (first attempt, complete event), `scripts/ci_main_push_superseded.py` reads the tip of
`main`. When the pushed commit is a strict ancestor of that tip (the compare from the pushed commit to the tip answers
`ahead`), it says "skip" (`main-push-superseded`): the tip's own push run tests a superset tree. The check does not
confirm that such a run exists (a `[skip ci]` tip has none); the daily `schedule` run is the backstop. The pushed commit
being the tip, any other compare status and every error give no opinion, and the rules below decide.

Otherwise it says "skip" (`main-push-tested-tree`) only when all of the following hold, in this order:

- The run is the first attempt and names the pushed commit, the workflow file, the job and its run number.
- Exactly one pull request is associated with the pushed commit; it was merged, its base is `main` and its merge commit
  is the pushed commit. For a rebase merge the merge commit is the rebased tip, so it skips only when the next rule
  holds too.
- The tree of the pushed commit equals the tree of the pull request's head commit. The head already contained `main`, so
  the merge ref that the head's run tested had the same tree too. When `main` moved after the head's run, the trees
  differ and the jobs run in full.
- The newest pull-request run of the same workflow on the head commit (of the newest five) that holds exactly one job
  with this job's name and runner label, and whose pull request list is empty or names the merged pull request with base
  `main`, shows that job `completed` with conclusion `success`, and its `Full run completed` step succeeded (a real
  success) or was skipped (the job's own gates, the bookkeeping gate or the macOS relevance gate, proved it on an
  ancestor). The detail of the decision line says which of the two it was.
- When that step was skipped and the head commit is a merge commit, the walk through the skipped runs on the head branch
  reaches a real success, and every path changed between that commit and the head is bookkeeping. A merge-push skip
  (`merge-push-clean-main-merge`) is never enough: it proves both parents, but no job ran the merged tree.

Every missing piece of evidence runs the full job under a rule of its own, and every API error or unexpected answer is
`api-error`, so the job runs in full. A re-run of a main push always runs in full. Reading the pull requests of a commit
needs `pull-requests: read`, which both jobs hold beside `contents: read` and `actions: read`; no job holds a write
permission. Both workflows also run once a day on a `schedule` trigger: the gate never skips a scheduled run
(`event-not-pull-request`), so `main` is tested in full at least once a day whatever the pushes decided. A push to
`main` in any other workflow keeps `event-not-pull-request` and runs in full.

These rules belong to `scripts/ci_main_push_gate.py` (`main-push-superseded` to `scripts/ci_main_push_superseded.py`):

| rule                                | meaning                                                                                      |
| ----------------------------------- | -------------------------------------------------------------------------------------------- |
| `main-push-tested-tree`             | skip: the pushed tree was tested by this job on the merged pull request's head               |
| `main-push-superseded`              | skip: the pushed commit is a strict ancestor of the tip of `main`, whose push run tests it   |
| `main-push-rerun-attempt`           | the run is a second or later attempt                                                         |
| `main-push-incomplete-event`        | the environment lacks the pushed commit, the workflow file, the job name or the run number   |
| `main-push-no-pull-request`         | no pull request is associated with the pushed commit (a direct push)                         |
| `main-push-several-pull-requests`   | more than one pull request is associated with the pushed commit                              |
| `main-push-pull-request-not-merged` | the pull request is not merged, its base is not `main` or its merge commit is another commit |
| `main-push-tree-mismatch`           | the pushed tree differs from the tree of the pull request's head                             |
| `main-push-head-job-missing`        | no run on the head commit holds exactly one job with this job's name and runner label        |
| `main-push-head-job-not-finished`   | the job on the head commit is still queued or in progress                                    |
| `main-push-head-job-not-successful` | the job on the head commit failed or was cancelled; the detail names the conclusion          |
| `main-push-head-job-unverified`     | the job on the head commit succeeded but has no `Full run completed` step                    |
| `main-push-head-skip-unproven`      | the job on a merge head was skipped; no real success has only bookkeeping changes since      |

### Merge pushes

A push that merges the base branch into the pull request's head ("Merge remote-tracking branch origin/main") changes,
against the previous head, every file that `main` changed since the branch point, so the rules above run it in full
under `non-bookkeeping-change` or `diff-too-large`. Such a `synchronize` push is then handed to
`scripts/ci_merge_push_gate.py`. When the new head is not a merge commit, the gate keeps its own decision, except in the
hygiene job. Otherwise the module decides, and it says "skip" (`merge-push-clean-main-merge`) only when all of the
following hold, in this order:

- The job is not `hygiene` and the workflow is not `test-hygiene.yml`.
- The new head has two parents, the first is the previous head, and the event names the base branch.
- The job really succeeded on the previous head (the walk and the diff rule of "When a job skips").
- The newest push run of the workflow on the base branch on the merged-in commit (the second parent) that holds this job
  shows a real success.
- A merge base exists, and the trees of the merge base, both parents and the head are complete.
- Every path of the head holds the file-level three-way merge: no conflict was resolved by hand.
- The two sides changed no common path and no path in a common parent directory. Bookkeeping paths are left out of this
  rule and the next two.
- Neither side changed `pyproject.toml`, `uv.lock`, `Makefile` or `.python-version` at the root, an app's
  `pyproject.toml` or a workflow file while the other side changed any path.
- When both sides changed files under `apps/` or `scripts/`, every such file is a regular Python file other than
  `conftest.py` that parses, and no file of one side imports a module of the other or is its `test_<stem>.py`.

These rules belong to `scripts/ci_merge_push_gate.py`:

| rule                                        | meaning                                                                                    |
| ------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `merge-push-clean-main-merge`               | skip: both parents really passed this job and the merge combined changes that do not touch |
| `merge-push-hygiene-never-skipped`          | the job is the hygiene job, which never skips on this route                                |
| `merge-push-not-two-parents`                | the new head is not a commit with two parents                                              |
| `merge-push-first-parent-not-previous-head` | the first parent of the new head is not the previous head                                  |
| `merge-push-incomplete-event`               | the event lacks the base branch of the pull request                                        |
| `merge-push-base-run-missing`               | no push run of the workflow on the merged-in commit holds this job                         |
| `merge-push-base-run-not-finished`          | the job on the merged-in commit is still queued or in progress                             |
| `merge-push-base-run-not-successful`        | the job on the merged-in commit failed or was cancelled; the detail names the conclusion   |
| `merge-push-base-run-unverified`            | the job on the merged-in commit succeeded but its `Full run completed` step did not        |
| `merge-push-no-merge-base`                  | the two parents have no merge base                                                         |
| `merge-push-tree-truncated`                 | the API cut a tree of the merge base, a parent or the head short                           |
| `merge-push-conflict-resolution`            | a path of the head differs from the three-way merge of its parents                         |
| `merge-push-overlapping-change`             | both sides changed the same path or paths in the same directory                            |
| `merge-push-config-change`                  | one side changed a dependency, tool or workflow file and the other side changed any path   |
| `merge-push-unanalysable-change`            | a file both sides could couple through is not a regular Python file that parses            |
| `merge-push-scan-too-large`                 | the import scan needs more blob reads than the module allows                               |
| `merge-push-related-change`                 | a file of one side imports a module of the other side or is its test                       |
| `merge-push-api-error`                      | an API call failed or answered in an unexpected shape; the detail names the class          |

No job ran the merged tree, so a merge-push skip is never evidence for a later push to `main`
(`main-push-head-skip-unproven`).

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

- `event-not-pull-request`: the run is a scheduled or manual run, a push to `main` in a workflow other than the two of
  "Pushes to main", or any other event.
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
- `chain-too-long`: the newest 20 earlier runs were all skipped and older runs exist; the gate does not look further
  back.
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
- Start the workflow by hand (`workflow_dispatch`); this also runs a skipped main push's jobs in full on `main`.
- Re-run a main push: a second attempt always runs in full.
- Push any change that is not a bookkeeping path, for example a file outside the three roots.

An empty commit does not force a full run: it changes no path, so it counts as bookkeeping-only.

### Limits

- A skipped run does not test a base branch that moved since the last real success. The next push with another change
  and a re-run run in full. The push to `main` after the merge runs in full unless its tree equals the tested head's
  tree, and the daily scheduled run of `test-unit.yml` and `test-stress.yml` always runs in full.
- Tests that read the real plans and ledgers do not run on a skipped push. They run on the next full run and on `main`.
- The gate finds the earlier runs by workflow and head branch, not by pull request, so it assumes one open pull request
  per head branch. It runs in full when an earlier run names several pull requests (`shared-head-branch`), but the API
  lists only the open ones: after a second pull request from the same branch is closed, the gate may count a run of that
  pull request as an earlier run of this one.
- The gate does not notice a retargeted base. After the base branch of a pull request is changed, a bookkeeping-only
  push can still skip on the strength of a real success against the old base. Close and reopen the pull request to test
  against the new one.
- Skipped jobs appear as very short points in the CI duration report.
- A main push that the gate skipped uploads no timing record ("Upload timing record" then ignores the missing file), so
  the timing baselines come from the main pushes that still run in full. After a quiet month a job may have fewer than
  five records; the timing gate then only warns and the 900 s ceiling still applies.
- A head run whose pull request list is empty is accepted, because the API may drop a merged pull request from it. If
  another pull request with the same head commit against another base ran the jobs and its run names no pull request, a
  main push can skip on that run's merge ref; the tree check still requires the head to hold the pushed tree.
- A main push skips on the pull-request head's judgement, including a head job that a gate skipped. A defect that only a
  full run on `main` would catch can stay on `main` until the next daily scheduled run.
- A merge push skips when the two sides share no path, no directory and no direct import or test name. A coupling the
  module does not see, such as a transitive import, a data file or a fixture, can let a merge push skip a job that the
  merged tree would fail. The next full run, the daily scheduled run and the flow's CI check on the final head catch it.
- The marker step proves a real success only while no step of the job uses `continue-on-error`. The wiring test rejects
  that key in the six workflows.

### Checklist for later changes

- A new job in one of these workflows, or a new pull-request workflow, needs three things: the "Bookkeeping gate" step
  (`id: bookkeeping`) directly after the checkout, the guard `if: steps.bookkeeping.outputs.skip != 'true'` on every
  later step that is not cheap, and `Full run completed` as its last step with exactly that guard. The job also needs
  `actions: read`. `apps/sandbox-executor/tests/test_ci_bookkeeping_wiring.py` enforces this.
- A workflow whose jobs should skip on pushes to `main` must be added to `GATED_WORKFLOWS` in
  `scripts/ci_main_push_gate.py`, hold `pull-requests: read` and a daily `schedule` trigger, and make its timing upload
  ignore a missing record on a skip; the same test file checks the two workflows in that tuple.
- A new label that a workflow reads must be added to `RESULT_AFFECTING_LABELS` in `scripts/ci_bookkeeping_gate.py`. The
  same test file scans the workflows for label names and fails on one that is missing. A label that a gate script reads
  from the event belongs there too: `ci-macos`, read by the macOS relevance gate below.
- A new rule name must be described in the list above. The same test file compares the list with the constants of the
  scripts.

## macOS Relevance Gate

The `macos-latest` leg of `test-unit.yml` is the slowest job of the pipeline (median 7.0 minutes, longest 9.5 minutes,
against 3.2 minutes for the ubuntu leg on the same heads), so it sets how long every pull request waits for CI.
`scripts/macos_relevance.py`, called through the composite action `.github/actions/macos-relevance-gate`, lets the macOS
leg of a pull-request run skip its expensive steps when no changed file can behave differently on macOS. The macOS leg
runs as two shards and the gate runs on each of them; both judge the same commits the same way, so both skip or both
run. Each shard still starts, reports as `unit (macos-latest, shard 1 of 2)` or `unit (macos-latest, shard 2 of 2)` and
ends green, so the required status checks are satisfied; the job has no job-level condition. The ubuntu leg, the
bookkeeping gate and the Workspace survival job are unchanged. The rule lives in `scripts/macos_relevance_rules.py` (its
content patterns in `scripts/macos_relevance_patterns.py`), the decision rules and log lines in
`scripts/macos_relevance_judgement.py` and the git reads in `scripts/macos_relevance_git.py`; all of them use the
standard library only and run on the runner's own `python3`.

### When the leg runs

"macOS relevance gate" is the step directly after "Bookkeeping gate" in `test-unit.yml`. It runs only on the macOS leg
and only when the bookkeeping gate said "run". Its rules are checked in order; the first match decides and only the last
one skips:

- `event-not-pull-request`: a push to `main`, a manual run or any other event; on `main` the leg runs in full unless the
  bookkeeping gate skipped the push ("Pushes to main").
- `rerun-attempt`: the run is a second or later attempt.
- `force-label`: the pull request carries the `ci-macos` label, compared without case.
- `incomplete-event`: the environment lacks `GITHUB_SHA` or the run attempt.
- `changed-files-unreadable`: git failed, the checked-out `HEAD` is not `GITHUB_SHA`, or `HEAD` is not a merge commit
  with two parents; the detail names the error class or the condition, never an error text.
- `no-changed-files`: the merge commit changes nothing against its first parent, so there is nothing to judge.
- `sensitive-path`: a changed file is platform-sensitive by its path; the detail names the first one, its side and the
  reason.
- `sensitive-content`: a changed Python file is platform-sensitive by its content, new or old.
- `internal-error`: the gate itself failed; the detail names the error class. The action also falls back to "run" when
  the script cannot start.
- `all-neutral`: every changed file is neutral, and the leg skips; the detail gives the count.

On a pull request the checkout holds the merge commit `refs/pull/N/merge`. The action first deepens it to its parents
(`git fetch --no-tags --no-recurse-submodules --depth=2 origin "$GITHUB_SHA"`; a failure only warns, and the script then
says `changed-files-unreadable`). The changed files are `git diff --name-status -z -M HEAD^1 HEAD`, exactly what the
pull request changes on top of its base; the new content is read from `HEAD` and the old content from `HEAD^1`. There is
no file cap and no API call.

### Sensitive paths

A changed file is sensitive by path, whatever its content, when its new path or, for a rename or a deletion, its old
path is one of:

- any `pyproject.toml` or `uv.lock`, at any depth;
- every path under `.github/` (workflows, actions, this README);
- the gate itself and the CI gates it runs with: `scripts/macos_relevance*.py`, `scripts/ci_bookkeeping_gate.py`,
  `scripts/ci_bookkeeping_api.py`, `scripts/ci_bookkeeping_context.py`, `scripts/ci_bookkeeping_chain.py`,
  `scripts/ci_timing_gate.py` and `apps/sandbox-executor/tests/test_macos_relevance*.py`;
- shell: `*.sh`, `*.bash`, `*.zsh`, `Makefile`, the repository launcher `holon`, and any Python or extension-less file
  whose first line is a `#!` shebang naming `sh`, `bash` or `zsh`;
- containers: `Dockerfile`, `Dockerfile.*`, `*.Dockerfile`, `docker-bake.hcl`;
- launcher and keychain code: everything under `apps/sandbox-executor/src/sandbox_executor/launcher/`,
  `apps/sandbox-executor/src/sandbox_executor/entrypoint/` and `apps/sandbox-executor/entrypoint/`, and any file whose
  name contains `keychain`, `launcher` or `credential` (any case);
- test-wide configuration: every `conftest.py`.

### Sensitive content

Every changed Python file (`.py`), tests included, is read in its new and its old version. Each line, without its `#`
comment, is matched against these regular expressions; the first match decides and is printed with its reason:

- Processes (`process`): `^\s*(import|from)\s+([\w.]+\s*,\s*)*(subprocess)\b`, `\bsubprocess\.`,
  `\bos\.(system|popen|fork|exec\w*|spawn\w*|kill|killpg|getpgid|setsid|wait\w*)\(`, `\bpexpect\b`,
  `\basyncio\.create_subprocess_`, `^\s*(import|from)\s+([\w.]+\s*,\s*)*(multiprocessing)\b`, `\bmultiprocessing\.`,
  `\bProcessPoolExecutor\b` (process start methods differ: `spawn` on macOS, `fork` on Linux)
- Platform checks (`platform-check`): `\bsys\.platform\b`, `^\s*(import|from)\s+([\w.]+\s*,\s*)*(platform)\b`,
  `\bplatform\.`, `\bos\.name\b`, `\bos\.uname\(`
- POSIX-only modules (`posix-module`):
  `^\s*(import|from)\s+([\w.]+\s*,\s*)*(signal|fcntl|termios|pty|tty|resource|pwd|grp|select)\b`, `\bsignal\.`,
  `\bos\.sched_\w+\(` (Linux-only scheduler calls), `\bAF_UNIX\b` (Unix sockets: 104-byte path limit on macOS)
- Home and temporary directories (`home-or-temp`): `^(?=.*(environ|getenv|setenv|putenv|delenv)).*\bHOME\b`,
  `\bexpanduser\(`, `\bPath\.home\(`, `\bTMPDIR\b`, `^\s*(import|from)\s+([\w.]+\s*,\s*)*(tempfile)\b`, `\bgettempdir\(`
- Filesystem operations beyond joining paths (`filesystem`):
  `\bos\.path\.(realpath|abspath|samefile|islink|ismount|expanduser|getmtime|getctime|getatime|exists|lexists|isfile|isdir|getsize)\(`,
  `\bos\.(chmod|chown|lchown|symlink|link|readlink|rename|replace|scandir|listdir|walk|stat|lstat|access|getcwd|chdir|umask|statvfs|makedirs|mkdir|remove|unlink|rmdir|removedirs)\(`,
  `^\s*from\s+os(\.path)?\s+import\b`, `^\s*(import|from)\s+([\w.]+\s*,\s*)*(shutil)\b`, `\bshutil\.`,
  `\.(resolve|absolute|samefile|is_symlink|symlink_to|hardlink_to|readlink|chmod|lchmod|stat|lstat|owner|iterdir|glob|rglob|walk|rename|expanduser|cwd|exists|is_file|is_dir|mkdir|unlink|rmdir|touch)\(`,
  `\.replace\(\s*([^,=()\s]|\([^()]*\))([^,=()]|\([^()]*\))*\)` (`Path.replace(target)` with one positional argument)
- Keychain and launcher code (`keychain-or-launcher`): `keychain` (any case), `\bfind-generic-password\b`,
  `\bsandbox_executor\.launcher\b`, `^\s*from\s+\.+launcher\b`

Content larger than 1 MiB, not UTF-8 or unreadable is sensitive (`unreadable-content`). A pattern inside a string or a
docstring still matches: a false "sensitive" only costs one macOS run.

### What stays neutral

Every other file: markdown, JSON and JSONL, YAML outside `.github/`, TOML other than `pyproject.toml`, templates, and
Python without a match. Neutral on purpose, and so part of the accepted risk below: pure joining and naming
(`os.path.join`, `basename`, `dirname`, `splitext`, `PurePath`, the `/` operator, `.name`, `.suffix`, `.parent`), plain
content I/O (`open`, `read_text`, `write_text`, `read_bytes`, `write_bytes`), thread pools, `AF_INET` sockets,
`str.replace`, `datetime.replace(tzinfo=...)` and `re.Match.group`.

### Reading the gate output

The gate prints one decision line, then one line per changed file with its verdict, status letter and path (and, for a
rename, the old path), and for a sensitive file the side (`new` or `old`) and the reason. The same lines go to the step
summary. A character that is not printable is shown as `?`.

```text
macos-relevance-gate: skip (all-neutral): 2 changed files
  neutral   A docs/new.md
  neutral   M pkg/calc.py
The macOS unit leg is skipped: no changed file is platform-sensitive; add the 'ci-macos' label or re-run the job to force it.
```

```text
macos-relevance-gate: run (sensitive-content): pkg/plain.py (old: process `import subprocess`)
  sensitive R pkg/plain.py <- pkg/runner.py  old:process `import subprocess`
```

### Forcing the macOS leg

- Add the `ci-macos` label to the pull request. The label is in `RESULT_AFFECTING_LABELS`, so the bookkeeping gate runs
  on the label event and the relevance gate then sees the label.
- Re-run the job or the workflow: a second attempt always runs.
- Start the workflow by hand (`workflow_dispatch`).

### Interaction with the bookkeeping gate

Every expensive step ("Install uv", "Run unit tests", "CI Timing Gate (compare with main)" and `Full run completed`)
carries both guards: `steps.bookkeeping.outputs.skip != 'true' && steps.macos_relevance.outputs.skip != 'true'`. Both
are written with `!= 'true'`, because on the ubuntu leg the relevance gate never runs and its output is empty. A
relevance skip leaves `Full run completed` skipped, so the bookkeeping gate never counts such a run as a real success;
on a later bookkeeping-only push it finds no real success to rely on, says "run", and the relevance gate decides again.

### Timing

A skipped leg runs neither "CI Timing Gate (compare with main)" nor any record step, so it records no timing sample and
cannot fail the CI timing gate. Timing records are uploaded only on pushes to `main` that ran the leg in full, and the
compare baseline comes only from `main` runs.

### Replay

`python3 -m scripts.macos_relevance --replay-commit <sha>` judges one commit against its first parent as an unlabelled
pull request, and `--replay-range <range>` prints one table row per squash-merged pull request (subject ending in
`(#N)`). Both use the same functions as CI and need no `GITHUB_*` variable. Over the 62 pull requests merged into `main`
from #241 to #314 the rule would have skipped the leg on 1: flow code and its tests use `subprocess`, `Path.resolve`,
`shutil` and `tempfile` almost everywhere.

### Limits and accepted risk

- A macOS-only break in a file judged neutral (for example plain `open` on a path whose case differs, or behaviour that
  depends on a neutral operation) reaches `main` without a macOS run on the pull request. The push to `main` after the
  merge runs the leg in full unless its tree equals the tested head's (see "Pushes to main"), and the daily scheduled
  run always does and catches it; the maintainer then fixes the break and adds the missing pattern here and in
  `scripts/macos_relevance_patterns.py`.
- A change on the base branch is not part of the pull request's diff, so a base-only macOS break surfaces on `main`.
- Skipped legs appear as very short points in the CI duration report.
- A pull request without a merge commit (for example one with conflicts) has no checkout to judge; the gate then runs.

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
  `ci-timing-test-unit-unit-ubuntu-latest`, kept for 30 days. The macOS unit shards pass `--job unit-shard-1` and
  `--job unit-shard-2`, so their records and baselines are kept per shard
  (`ci-timing-test-unit-unit-shard-1-macos-latest` and `ci-timing-test-unit-unit-shard-2-macos-latest`) and start cold
  for their first 5 `main` pushes. Records are never committed and are never a ledger.
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

## Ledger Promotion

`promote-ledger.yml` runs `holon ledger promote` on `main`. The command appends the pending fragment rows under
`holon-knowledge/ledger/pending/` to the canonical ledgers, additions only, once enough root intents have fragments (see
[Promotion](../../docs/ledger_fragments.md#promotion)); a manual dispatch with `force` set promotes whatever the count.
The job then commits and pushes the result to `main` as `holon-ledger-bot`:

- **Green no-op without the secret.** The job-level environment carries `HOLON_LEDGER_BOT_TOKEN` from the repository
  secret. When it is empty, the `Explain a missing bot token` step prints a `::notice::` and writes the same reason to
  the job summary, every later step is skipped and the run stays green. The secret reaches only the job environment and
  the checkout `token` input, never a `run:` script, and no step prints it.
- **Never its own commit.** The promotion commit message is `chore(ledger): promote pending fragments [ledger-promote]`.
  A push whose head commit message contains `[ledger-promote]` skips the job, so a promotion never triggers another one.
  Runs queue in the `promote-ledger` concurrency group with `cancel-in-progress: false`, so no run is killed mid-push.
- **Ledger paths only.** Before committing, the `Fail on changes outside holon-knowledge/ledger/` step lists every
  changed, deleted and untracked path, with a rename counted under both names. It fails the run if any path lies outside
  `holon-knowledge/ledger/`, and logs "Nothing to promote" when nothing changed (below the threshold or no pending
  fragments). The commit stages `holon-knowledge/ledger` only.
- **Push race.** The push to `main` is tried at most 3 times. After a rejected push the job fetches `origin/main` and
  rebases the promotion commit onto it. A rebase conflict aborts the rebase and fails the run: nothing is resolved by
  hand. Re-run the workflow once `main` settles.
- **Timing gate.** When the secret is set, the job starts and ends with the CI timing regression gate (600 s ceiling).

`apps/sandbox-executor/tests/test_promote_ledger_workflow.py` pins these properties on a structural parse of the
workflow. A live push cannot run in CI, so the first run after a maintainer sets the secret is the real check.

### Maintainer-provided secret

- Create a fine-grained personal access token or a GitHub App installation token limited to this repository with
  `Contents: Read and write`, and store it as the repository secret `HOLON_LEDGER_BOT_TOKEN` (Settings, "Secrets and
  variables", "Actions"). The workflow's own `GITHUB_TOKEN` stays read-only (`contents: read`) and never pushes.
- If branch protection on `main` requires pull requests or status checks, allow the token's identity to push to `main`,
  or every push is rejected and the run fails after 3 attempts.
- Start the first enabled run by hand ("Run workflow" on "Promote Ledger", or `gh workflow run promote-ledger.yml`) and
  watch it. Deleting the secret disables promotion again.

## Branch Protection Status Checks

Repository maintainers configuring branch protection rulesets on `main` should require the following status checks:

- `Test - Hygiene / hygiene (ubuntu-latest)`
- `Test - Unit / unit (ubuntu-latest)`
- `Test - Unit / unit (macos-latest, shard 1 of 2)`
- `Test - Unit / unit (macos-latest, shard 2 of 2)`
- `Test - Integration / integration (ubuntu-latest)`
- `Run Make / build (ubuntu-latest)`
- `Run Make / build (macos-latest)`

The two macOS unit shards replaced the former `Test - Unit / unit (macos-latest)` check; maintainers must update the
ruleset when the sharded workflow merges, or the old check stays "Expected" and blocks merges.

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
    - `os`: (optional, default: empty) The value that tells the matrix leg apart among the values of its job name: the
      runner label, for example `ubuntu-latest`, or `shard N of M` for a unit shard.
  - **Outputs:**
    - `skip`: `true` when the job may skip its expensive steps, `false` in every other case.
  - **Prerequisites & Requirements:**
    - Calling workflows must execute `actions/checkout` before invoking this local composite action, and the step needs
      `id: bookkeeping`.
    - The job must grant `actions: read` and `contents: read`.
    - Guards are written as `steps.bookkeeping.outputs.skip != 'true'`, never as `== 'false'`, so a missing output runs
      the step. A step that must also run after a failure combines both:
      `if: always() && steps.bookkeeping.outputs.skip != 'true'`.
- `.github/actions/macos-relevance-gate/action.yml`: Asks `scripts/macos_relevance.py` whether the macOS unit leg may
  skip its expensive steps (see "macOS Relevance Gate" above). On a pull request it first fetches the parents of the
  merge commit. It never fails a job: when the script cannot run, the action prints a warning and the leg runs.
  - **Inputs:** none; the script reads the event file, `GITHUB_SHA` and `GITHUB_RUN_ATTEMPT` from the environment and
    the changed files from local git. It needs no token beyond the checkout's credentials.
  - **Outputs:**
    - `skip`: `true` when the leg may skip its expensive steps, `false` in every other case.
  - **Prerequisites & Requirements:**
    - Calling workflows must execute `actions/checkout` before invoking this local composite action, and the step needs
      `id: macos_relevance`.
    - The job must grant `contents: read`.
    - Guards are written as `steps.macos_relevance.outputs.skip != 'true'`, never as `== 'false'`, so a missing output
      runs the step.

## Standards Alignment

All workflows in this repository conform to the standard Holon modular CI/CD architecture derived from
[`agentic-knowledge-base/.github`](https://github.com/thomashan/agentic-knowledge-base/tree/main/.github).
