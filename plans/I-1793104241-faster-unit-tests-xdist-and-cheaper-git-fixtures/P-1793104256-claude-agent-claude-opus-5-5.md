# Plan for I-1793104241-faster-unit-tests-xdist-and-cheaper-git-fixtures

- **Plan ID:** P-1793104256-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-27T12:30:57.082Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A. The intent is concrete, measured and bounded to test-side files. Only the
  ordering is changed: hermetic git and the deterministic turn-supervisor fix land before xdist is switched on, so that
  any failure under parallel execution can be traced to one change.
- **Exploration stance:** balanced. Most of the work is known technique (session git config, copied template
  repositories, pytest-xdist). Two choices are measured rather than assumed: the xdist distribution mode and worker
  count per OS, and which fixtures are worth templating. A small exploratory budget goes to these.
- **Safety priority level:** elevated
- **Priority Justification:** `holon-config/world/ruleset.md` section 3 says that sandbox executions must not modify
  tests or test assertions unless the plan explicitly declares the change. This intent is entirely a test-side change,
  so every test file that will be touched is declared below and no assertion may be removed or weakened. The stress
  canary and the hermetic guard are safety tests in their own right (`docs/safety.md`, "Sandboxing is mandatory for
  execution"), so changing how they launch the child suite must not void what they prove.

## Exploration

- **Proportion of steps that are exploratory:** 0.2
- **Justification:** Steps 1 and 6 are measurement-driven. Step 6 picks the xdist distribution mode and worker count
  from real timings. Everything else is exploitation of well-understood patterns, which fits an intent that forbids test
  weakening and is reviewed line by line.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.72  |
| entropy_pred        | 8.2   |
| impact_pred         | 70.0  |
| cost_pred           | 22.5  |
| learning_value_pred | 6.0   |
| ev_pred             | 28.44 |

`EV = 0.72 * 70.0 + 0.5 * 6.0 - 0.3 * 8.2 - 22.5 = 50.40 + 3.00 - 2.46 - 22.5 = 28.44`

## Declared test-side change set (ruleset.md section 3)

The execution may modify only these files. If another file needs to change, record that in the execution notes before
touching it.

- `apps/sandbox-executor/tests/conftest.py`: session-wide hermetic git config fixture.
- `apps/sandbox-executor/tests/hermetic_fixtures.py`: `pytest_suite_command` gains the xdist worker arguments.
- `apps/sandbox-executor/tests/test_flow.py`: template-backed `flow_repos` and other seeded-origin fixtures; removal of
  identity `git config` calls that the global test config now supplies (fixture setup only, never assertions).
- `apps/sandbox-executor/tests/test_executor.py`: the same, for its seeded-origin setup only if profiling shows a gain.
- `apps/sandbox-executor/tests/test_turn_supervisor.py`: deterministic lingering-pipe tests.
- Possibly `apps/sandbox-executor/tests/test_git.py`, `test_cli.py`, `test_review_loop.py`, `test_calibration.py`,
  `test_preflight.py`, `test_build_all_images.py`: only for parallel-safety fixes found by the step 5 audit (fixture or
  path changes, no assertion changes).
- `pyproject.toml` (root dev group plus `task test`), `uv.lock`, `.github/workflows/test-unit.yml`.
- `apps/sandbox-executor/docs/hermetic_testing.md`, `docs/testing/testing_guide.md`.
- Production code under `apps/sandbox-executor/src` is out of scope. If a production change looks necessary (for example
  an injectable reader-join hook in `turns.py`), record it in the execution notes as a separate follow-up.

## Plan Description & Strategy

The baseline shows two separate costs: about 30 git processes per test (1,308 of them unwanted `git maintenance` runs),
and a serial runner on machines with between 3 and 18 cores. The plan deals with them in a safe order:

1. Measure the baseline on this branch: the serial wall time, the `--durations` profile and the GIT_TRACE process count.
2. Add a hermetic global git config for the whole session (this removes the maintenance runs and the dependence on the
   developer's config).
3. Replace the repeated seed-and-push fixture work with per-worker template repositories that each test copies.
4. Make the `READER_JOIN_TIMEOUT_SECONDS` tests deterministic, and audit other tests that race real time.
5. Audit and fix parallel-safety hazards: fixed paths, writes into the repository, cwd and order dependence.
6. Add pytest-xdist and tune the distribution mode and worker count from measurements.
7. Prove stability: 5 full xdist runs, plus 50 repeats under load of the turn-supervisor and fuzz tests.
8. Measure the result locally and in CI, and record the GIT_TRACE count after the change.
9. Update the docs, run lint and prettier, and write the PR body with the real table.

The whole plan is one execution branch with no sub-intents. The predicted entropy (8.2) is under the budget (10.0). The
changes are tightly coupled, because xdist safety depends on the hermetic config and on the per-worker template caches.
Splitting them would make the review loop look at partial states that cannot be measured on their own.

---

## Step 1: Measure the baseline on this branch (serial timing, durations, GIT_TRACE count)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only measurement that every later comparison relies on. Cheap, and it must happen before any
  change.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED
- **Hypothesis being tested:** On this sandbox, the git-heavy fixtures in test_flow.py and test_executor.py still
  dominate, and the GIT_TRACE count is close to the intent's figure of about 30 git processes per test.
- **Learning target:** The top 30 slowest setups and calls (`--durations=30 --durations-min=0.5`), which fixtures to
  template first, the local core count, and the serial wall time on this host.
- **Maximum acceptable cost for this learning:** two serial suite runs (about 10 minutes) plus one traced run of the two
  heavy files.

### Intent & Git Integration

- **Step Intent:** Get real before-numbers on this host so the after-numbers are like for like.
- **Git branch:**
  `I-1793104241-faster-unit-tests-xdist-and-cheaper-git-fixtures/P-1793104256-claude-agent-claude-opus-5-5/E-{timestamp}-faster-tests/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- From the repository root, run `uv run pytest -m "not integration_test and not stress" --durations=30` serially and
  record the wall time, the pass count and the slowest setups and calls.
- Count git processes: run
  `uv run pytest apps/sandbox-executor/tests/test_flow.py apps/sandbox-executor/tests/test_executor.py` with `GIT_TRACE`
  pointing at a file in a fresh temporary directory outside the repository. Count the `trace: built-in: git` and
  `run_command` events by subcommand (maintenance, fetch, upload-pack, unpack-objects, rev-parse, config). Delete the
  trace file afterwards and keep only the numbers in the execution notes. The trace is not a ledger and is never
  committed.
- Record the OS, core count, git version and Python version next to the numbers.
- Also record the intent's quoted CI baseline (fbd3b35) unchanged, as the CI reference row.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent constraint 5 (real data only); intent constraint 7 (no auxiliary ledgers, tests only via
  `uv run pytest` from the root).
- **Potential failure modes for this step:** the baseline fails on this host (for example because the global config sets
  core.hooksPath); the trace file grows large.
- **Guardrails and early‑abort checks:** if baseline tests fail, record the failures as pre-existing with their output
  and continue, since step 2 is meant to fix the hooksPath one. Put the trace in a temporary directory and delete it
  after counting.

### Success & Discard Criteria

- **Success:** the execution notes hold the serial wall time, pass count, durations top list and git process counts by
  subcommand.
- **Discard:** none. If tracing is impossible on this host, record that and use the intent's trace figures as the before
  value, labelled as such.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.3   |
| impact_pred         | 8.0   |
| cost_pred           | 2.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 8.01  |

### Step Metrics Rationale

`EV = 0.95 * 8.0 + 0.5 * 5.0 - 0.3 * 0.3 - 2.0 = 7.60 + 2.50 - 0.09 - 2.0 = 8.01`. The step is read-only, so its entropy
is very low. Its learning value is high relative to its size, because it decides where steps 3 and 6 spend effort.

---

## Step 2: Add a session-wide hermetic git config in conftest.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** A small, self-contained fixture with a large payoff (it removes about 1,300 maintenance runs and the
  hooksPath failure). It is the base the template and xdist steps build on.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make every git process started by any test, and by the child suites, read a test-only global config
  and never the developer's or the system's.
- **Git branch:** same execution branch as step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `apps/sandbox-executor/tests/conftest.py`, add one `scope="session", autouse=True` fixture, for example
  `hermetic_git_config`. It uses `tmp_path_factory`, which already gives each xdist worker its own base temporary
  directory.
  - Create an empty hooks directory and write a gitconfig file into a `mktemp("git-config")` directory.
  - The file sets: `gc.auto=0`, `maintenance.auto=false`, `core.hooksPath=<empty dir>`, `init.defaultBranch=main`,
    `user.name` and `user.email` (one neutral test identity), `commit.gpgsign=false`, `tag.gpgsign=false`,
    `advice.detachedHead=false`, and `fetch.writeCommitGraph=false` (and `core.fsmonitor=false` if profiling shows
    fsmonitor spawns). Leave out anything that changes the semantics a test asserts on, such as safe.directory or
    protocol policy.
  - Set `GIT_CONFIG_GLOBAL=<file>` and `GIT_CONFIG_NOSYSTEM=1` with a session-scoped `pytest.MonkeyPatch.context()`, so
    `os.environ` carries them for the whole session. Child processes that copy `os.environ` (`simulated_container_env`,
    the workspace-survival child, xdist workers) therefore inherit them. Restore them on teardown.
- Do not put these variables into `no_ambient_credentials` or `_HARNESS_NAMES`. The fixture is independent and lives in
  the existing conftest, as the intent asks.
- Add one small regression test (in an existing test module, or a short test next to the fixture). Under the fixture,
  `git config --global --get core.hooksPath` must resolve to the empty test hooks directory and
  `git config --show-origin --get gc.auto` must name the test config file. This proves the developer's config is not
  read.
- Audit the 69 `patch.dict(os.environ, ..., clear=True)` sites. Those that also run real git would drop the two
  variables and silently go back to the developer config. For each one that spawns git, re-add the two variables to the
  patched dictionary (a fixture-side change, no assertion change), or record that it does not spawn git.
- Confirm that HOME-redirecting fixtures (`flow_repos` sets HOME) still find the identity through `GIT_CONFIG_GLOBAL`,
  which git prefers over `$HOME/.gitconfig`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (steps 3 and 6 rely on it, but its own p_success is high)

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 1; `docs/safety.md` "Sandboxing is mandatory"; the hermetic guard
  (`test_sandbox_hermetic_guard.py`) must keep working.
- **Potential failure modes for this step:** a test asserts behaviour when the git identity is missing and now passes
  for the wrong reason; a test asserts on `git config --show-scope` output (for example
  `test_credential_helper_answers_from_the_environment`, which builds its own env and so stays unaffected); the canary's
  simulated HOME relied on HOME-based config.
- **Guardrails and early‑abort checks:** search for tests that assert on missing identity or on the global or system
  scope before adding identity to the global config. If any exist, leave identity out of the global file and keep their
  setup. Run the full serial suite and both stress tests (`uv run task test-stress`) after this step, before going on.

### Success & Discard Criteria

- **Success:** the serial suite and the stress suite pass. A run with a deliberately hostile developer config (a
  temporary HOME whose `.gitconfig` sets `core.hooksPath` to a failing hook, created only for the check) passes.
  GIT_TRACE shows zero `git maintenance` runs for test_flow.py and test_executor.py.
- **Discard:** if hermetic config breaks more than 3 tests in ways that need assertion changes, stop. Reduce the config
  to the four keys `gc.auto`, `maintenance.auto`, `core.hooksPath` and `init.defaultBranch`, and record why.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.2   |
| impact_pred         | 18.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 13.98 |

### Step Metrics Rationale

`EV = 0.88 * 18.0 + 0.5 * 3.0 - 0.3 * 1.2 - 3.0 = 15.84 + 1.50 - 0.36 - 3.0 = 13.98`. The fixture is small, and the main
risk is the `clear=True` environment sites. Impact covers both speed (maintenance is a large share of the git events)
and the hooksPath correctness fix.

---

## Step 3: Build seeded repositories once per worker and copy them per test

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is the largest single source of git processes (init, commit, push and clone per test) and the
  riskiest step for isolation. It stays in this plan because it only touches fixture code and gets an explicit isolation
  guard. As a separate intent it would be unmeasurable without step 2.
- **Step Type:** REFACTOR
- **Exploration level:** BALANCED
- **Hypothesis being tested:** copying a pre-built `origin.git` and `host` pair with `shutil.copytree`, then rewriting
  the remote URL with one git call, is several times cheaper than init, seed commit, push and clone (about 8 to 10
  processes), and gives a byte-identical starting state.
- **Learning target:** setup time per test before and after for `flow_repos`, and how many other fixtures are worth
  converting.
- **Maximum acceptable cost for this learning:** about 5 cost units. Stop converting fixtures once the remaining ones
  each save under 1 s in total over the suite.

### Intent & Git Integration

- **Step Intent:** Cut fixture setup cost while guaranteeing that no test sees another test's commits.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `test_flow.py`, add a `scope="session"` fixture, for example `_flow_repos_template`, that runs the current body of
  `flow_repos` once into `tmp_path_factory.mktemp("flow-template")`. That body is: a bare origin, a seed repository with
  the three empty ledgers and `src/app.py`, the seed commit, the push, and a host clone. Because `tmp_path_factory` is
  per worker under xdist, the cache is per worker automatically.
- Rewrite `flow_repos` to copytree the template's `origin.git` and `host` into the test's `tmp_path` (`symlinks=True`).
  Point the host at its private origin with `git -C host remote set-url origin <tmp_path/origin.git>`. Keep the HOME
  monkeypatch and the return value exactly as they are, so no test using the fixture changes.
- Isolation guard: after building it, make the template tree read-only (strip write bits). Any accidental push to or
  commit in the template then fails loudly instead of leaking state. The per-test copies get their write bits back, or
  `copytree` uses a copy function that restores the user write bit. Tests stay order-independent because every test gets
  a fresh copy.
- Keep HOME-dependent state (`tmp_path / "home"`) per test and outside the template.
- Remove the now-redundant `_configure_identity` calls in the converted fixtures only, because the step 2 global config
  supplies the identity. Keep the helper wherever a test itself exercises local identity.
- Using the step 1 durations profile, apply the same pattern to the next most expensive seeded-origin setups. Likely
  candidates are the inline seeds in `test_flow.py` (near the `TestFlowStages` git setup, `TestStage4LedgerProvenance`,
  the mergeability fixtures and `TestFlowDispatchMergeInteractions`) and the seeded origin in `test_executor.py` near
  the executor git test. Convert one only when its total measured setup exceeds about 1 s over the suite. Leave unittest
  `tempfile.TemporaryDirectory` cases that mock git alone.
- Never template a repository whose test asserts on the creation steps themselves, such as init or first-push behaviour.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO (the plan still delivers xdist speed-up and hermeticity if this step is reverted)

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 2 (isolation); `ruleset.md` section 3 (declared test changes, no assertion changes).
- **Potential failure modes for this step:** the copied host still points at the template origin, so tests push into
  shared state; absolute paths are embedded in `.git` files (alternates, worktrees); differing mtimes or inode
  assumptions; hooks copied along.
- **Guardrails and early‑abort checks:** the read-only template turns cross-test leakage into a hard failure. Check
  `git -C host remote get-url origin` against the per-test path inside the fixture. Run test_flow.py serially and under
  `-n auto --dist worksteal` twice, in different orders (for example `-p no:cacheprovider` with a reversed file list),
  and compare pass counts.

### Success & Discard Criteria

- **Success:** test_flow.py setup time falls by at least 30 per cent compared with step 2, the git process count falls
  further, and every test passes serially and in parallel.
- **Discard:** if a converted fixture causes any order-dependent failure that cannot be fixed within the fixture, revert
  that fixture to its original form and record it. If `flow_repos` itself cannot be templated safely, drop the step and
  keep steps 2 and 6.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.75  |
| entropy_pred        | 2.0   |
| impact_pred         | 20.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 11.40 |

### Step Metrics Rationale

`EV = 0.75 * 20.0 + 0.5 * 4.0 - 0.3 * 2.0 - 5.0 = 15.00 + 2.00 - 0.60 - 5.0 = 11.40`. This step has the highest entropy,
because it rewrites shared fixtures in the largest test module. p_success is moderated by the absolute-path and
isolation hazards, but the read-only template guard makes failures visible rather than silent.

---

## Step 4: Make the lingering-pipe turn-supervisor tests deterministic and audit real-time races

- **Sub‑intent recommendation:** NO
- **Reasoning:** A small, test-only change that removes a known macOS CI flake. It must land before xdist raises CPU
  contention.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Remove the dependence on scheduling from
  `TestRunTurn::test_lingering_pipe_holder_group_is_terminated_after_exit` and its siblings, without changing
  `turns.py`.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Root cause: after the fake `killpg` closes the `TickStream`, the reader thread's `read1` polls the queue every 0.01 s
  and only then returns EOF. `run_turn` joins it with `READER_JOIN_TIMEOUT_SECONDS` patched to 0.05 s. Under load the
  reader does not finish in 0.05 s, so `run_turn` also sends SIGKILL and the signals assertion fails.
- Fix 1 (signal the reader): give `TickStream` a `close()` that sets `closed` and wakes `read1` immediately, for example
  by putting an EOF sentinel on the queue. Then `read1` returns `b""` on the next get, not after a poll interval.
- Fix 2 (inject the join): in the test's `killpg`, after closing the stream, join the turn's reader threads before
  returning. Find them as the live `threading.Thread` instances of `turns._StreamReader`, or wrap `turns._StreamReader`
  with a test subclass recorded through `mock.patch.object`. Join each with a generous bound (for example 5 s, which is
  only a safety net). By the time `run_turn` makes its own 0.05 s join, the reader is already dead, so the outcome no
  longer depends on scheduling. Apply the same pattern to `test_lingering_pipe_holder_ignoring_sigterm_gets_sigkill` (at
  the SIGKILL signal).
- The two tests whose stream never closes (`..._survives_sigkill_does_not_hang` and
  `..._never_signals_its_own_process_group`) are deterministic in outcome, because the reader can never finish. Keep the
  0.05 s patch there for speed. Their assertions stay unchanged.
- Audit: search the tests for `mock.patch.object(... TIMEOUT|_SECONDS|INTERVAL ...)`, `monkeypatch.setattr` of timeouts,
  `time.sleep`, `timeout=0.0x`, `Event.wait(` and `join(timeout=` (17 `time.sleep` and timeout-patch hits at planning
  time). For each that races a real thread or process against a tiny real timeout, apply the same signal-or-join
  approach, or replace real time with the existing fake clock or sleeper injection already used by `Harness`. Record the
  audit outcome per site in the execution notes.
- If a site cannot be made deterministic without a production hook (for example an injectable join in `turns.py`), leave
  it unchanged, record it as a production follow-up, and make sure the 50x load check in step 7 covers it.

### Dependencies & Criticality

- **Depends on:** NONE (can run in parallel with steps 2 and 3; ordered here for review clarity)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 4; intent answer "no test may be weakened".
- **Potential failure modes for this step:** the fix hides a real production bug by joining too eagerly; the thread
  discovery picks up readers from another test (impossible under xdist processes, but possible serially if a previous
  test leaked a thread).
- **Guardrails and early‑abort checks:** restrict the joined readers to those created during this `run_turn` call (wrap
  the class, not `threading.enumerate`, if leaked threads are seen). Assertions are unchanged, and the signal list must
  still equal exactly `[(CHILD_PID, SIGTERM)]`.

### Success & Discard Criteria

- **Success:** 50 consecutive runs of `test_turn_supervisor.py` under artificial CPU load (step 7) with zero failures.
- **Discard:** if determinism needs a production change, document it and keep the test as is (do not loosen its
  assertion).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.6   |
| impact_pred         | 8.0   |
| cost_pred           | 2.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 6.52  |

### Step Metrics Rationale

`EV = 0.90 * 8.0 + 0.5 * 3.0 - 0.3 * 0.6 - 2.0 = 7.20 + 1.50 - 0.18 - 2.0 = 6.52`. The root cause is understood from the
code (`turns.py` joins with the patched timeout; `TickStream.read1` polls every 0.01 s), so success is likely and the
change is local.

---

## Step 5: Audit and fix parallel-safety hazards

- **Sub‑intent recommendation:** NO
- **Reasoning:** A targeted audit whose fixes are small fixture or path edits. It is the precondition for turning on
  xdist by default.
- **Step Type:** TEST
- **Exploration level:** BALANCED
- **Hypothesis being tested:** apart from git config and the turn-supervisor timing, the suite has few or no shared
  filesystem or state hazards. The `/tmp/...` literals found at planning time are mock strings that are never written.
- **Learning target:** a list of hazards that are real, as opposed to string-only.
- **Maximum acceptable cost for this learning:** about 3 cost units.

### Intent & Git Integration

- **Step Intent:** Make sure no two workers share a writable path, a ledger, the repository working tree or
  process-global state.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Fixed paths: check every `/tmp/` literal (`test_agent_runner.py`, `test_intent_creator.py`, `test_local_llm.py`,
  `test_token_reduction.py`, `test_executor.py`, `test_flow.py`, `test_calibration.py`, `test_pre_commit_check.py`,
  `test_planner_integration.py`, `test_intent_creator_integration.py`) and classify it as a mock string or a real
  filesystem write. Replace each real write with `tmp_path` or `tmp_path_factory`. The `/tmp/holon-fixture-seed` in
  `hermetic_fixtures.py` runs inside a throwaway container (integration only) and stays.
- Writes into the repository: search for tests that write under `REPO_ROOT` or the cwd (for example
  `build_all_images.log`, which the `clean` task removes from `apps/sandbox-executor` and the root, plus ledgers under
  `holon-knowledge/ledger`, plans, executions, `.pytest_cache`). Redirect them to per-test temporary directories, or
  confirm they are already mocked.
- Ledgers: make sure no unit test resolves the real workspace ledger (`get_workspace_dir` defaults). Where a test relies
  on a default path, patch it to a per-test directory.
- cwd and env: the only `chdir` found is `monkeypatch.chdir` in `test_pre_commit_check.py`, which is safe per process.
  Check for module-level caches (lru_cache, module globals) that a test mutates without restoring. They are safe across
  workers but can become order-dependent within a worker once xdist changes the order.
- Docker, network and ports: confirm that unit tests never bind fixed ports and never create named Docker resources
  (integration tests stay serial and outside `-n`).
- Order dependence check: run the unit selection serially once in reverse file order (by passing the test files
  explicitly in reverse) and once with `-n auto --dist worksteal`. Investigate any test that passes in one and fails in
  the other.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 4; intent constraint 7 (target-native paths, no auxiliary ledgers);
  `holon-config/world/constraints.md` section 3 (the ledger must never be written by tests).
- **Potential failure modes for this step:** a fix changes what a test asserts (forbidden); a hidden write into the real
  ledger is found that pre-dates this intent.
- **Guardrails and early‑abort checks:** fixes are limited to fixture and path redirection. Check `git status` of the
  repository after each full run: it must be clean, which proves the suite writes nothing into the working tree.

### Success & Discard Criteria

- **Success:** the audit list is recorded in the execution notes, every real hazard is fixed, and `git status` is clean
  after serial, reversed and xdist runs.
- **Discard:** if a hazard needs a production change, record it as a follow-up and mark the affected test with
  `xdist_group` so it is serialised within a worker. Do not skip it. Use this marking only when it is justified and
  recorded.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 1.0   |
| impact_pred         | 8.0   |
| cost_pred           | 3.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 5.10  |

### Step Metrics Rationale

`EV = 0.80 * 8.0 + 0.5 * 4.0 - 0.3 * 1.0 - 3.0 = 6.40 + 2.00 - 0.30 - 3.0 = 5.10`. Most of the `/tmp` hits are mock
strings, so the expected fix count is small. The learning value is high because the audit is what makes "safe under
xdist" a claim backed by evidence.

---

## Step 6: Add pytest-xdist and tune the worker count and distribution mode

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is where most of the wall-clock gain comes from. The configuration surface is small (one pinned
  dependency and three command lines), but the canary and stress interaction needs care.
- **Step Type:** CONFIG
- **Exploration level:** EXPLORATORY
- **Hypothesis being tested:** `-n auto` with `--dist worksteal` beats `load` and `loadfile`, because test_flow.py is 55
  per cent of the runtime and its tests have uneven durations. `auto` is not slower than an explicit count on 3-core
  macOS and 4-core ubuntu runners.
- **Learning target:** wall time for `-n auto` against `-n logical` or a fixed count, crossed with
  `load`/`worksteal`/`loadfile`, locally, plus one CI data point per OS.
- **Maximum acceptable cost for this learning:** 6 local suite runs and at most 2 CI iterations.

### Intent & Git Integration

- **Step Intent:** Run the unit selection in parallel everywhere it is run: the task, CI and the stress canary's child.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add an exact pin of the current pytest-xdist release to the root `[dependency-groups] dev` list, in the same style as
  `pytest==9.1.1`. Confirm it supports pytest 9.1 before pinning. Run `uv lock`, then `uv sync`, and check that the lock
  diff only adds pytest-xdist and its required transitive dependency (execnet). If any other package version changes,
  stop and rethink. Execnet is part of the single allowed dependency, not a second one, and the execution notes should
  say so explicitly.
- `pyproject.toml` `task test`: add `-n auto` and the chosen `--dist` mode to the unit command only. Do not add `-n` to
  `addopts`, so that `test-integration` and `test-stress` remain serial, as the intent requires.
- `.github/workflows/test-unit.yml`: add the same flags to the `Run unit tests` command. Keep `-v -l --tb=short`. Add
  `--durations=20` only if it is useful for the reported table.
- `apps/sandbox-executor/tests/hermetic_fixtures.py` `pytest_suite_command`: add `-n` with the chosen value and the
  `--dist` mode before `*extra_args`, so both the workspace-survival child and the hermetic guard child run in parallel.
  Then check:
  - the child's HOME is the simulated home. xdist workers are spawned by execnet from the child's own interpreter and
    environment (`UV_PROJECT_ENVIRONMENT` and `UV_PYTHON_DOWNLOADS` are already set), so they inherit everything,
    including `GIT_CONFIG_GLOBAL`;
  - the hermetic guard's negative-control child still fails for the intended reason and is not broken by xdist. Read
    `test_sandbox_hermetic_guard.py` around its output parsing and make sure it does not depend on per-test lines that
    xdist reorders;
  - `-p no:cacheprovider` and `--import-mode=importlib` work together with xdist.
- Tuning: locally run the unit selection with `-n auto` under `worksteal`, `load` and `loadfile`, then `-n 4`, `-n 8`
  and `-n logical`, and keep the fastest. Make sure it does not starve 3-core macOS CI runners. If `auto` is slower
  there, use a tuned count per OS in the workflow matrix (for example a matrix `workers` value) and keep `auto` locally.
- Debug path: `-n 0` and `-p no:xdist` must both still work. Verify them once.

### Dependencies & Criticality

- **Depends on:** Steps 2, 4 and 5 (step 3 is helpful but not required)
- **Is Bottleneck:** YES (without xdist the CI target gains are much smaller); p_success is 0.80, so no sub-intent is
  required

### Safety & Constraint Considerations

- **Relevant rules:** intent answer 2 (exactly one new dependency, pinned exactly); `ruleset.md` section 1 (dependencies
  declared in `pyproject.toml`, uv-managed); intent goal 3 (integration and stress stay serial).
- **Potential failure modes for this step:** the lock pulls in extra packages; the stress canary's child under xdist
  exceeds memory on the CI runner; the junit "stress really ran" check is affected (it is not, because only the parent
  stress run writes junit and it stays serial); `uv lock --check` fails in lint.
- **Guardrails and early‑abort checks:** review the lock diff line by line; `uv run task lint` must pass (it runs
  `uv lock --check` and `git diff --exit-code uv.lock`); the stress suite must pass locally before pushing.

### Success & Discard Criteria

- **Success:** the local unit selection is at least 3 times faster than the step 1 serial baseline on the many-core
  host; both stress tests pass with the parallel child; lint passes.
- **Discard:** if the parallel child makes the stress suite flaky, keep `pytest_suite_command` serial, record why, and
  still ship xdist for the unit task and CI.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 1.5   |
| impact_pred         | 25.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 19.05 |

### Step Metrics Rationale

`EV = 0.80 * 25.0 + 0.5 * 5.0 - 0.3 * 1.5 - 3.0 = 20.00 + 2.50 - 0.45 - 3.0 = 19.05`. Highest impact, because
parallelism multiplies everything else. The exploratory tuning carries a real learning value (which distribution suits
this suite on 3-core and 4-core runners) that is reusable for the integration jobs later.

---

## Step 7: Prove stability under xdist and CPU load

- **Sub‑intent recommendation:** NO
- **Reasoning:** A pure verification step that the intent requires. It is cheap compared with shipping a flaky suite.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Show zero failures across repeated parallel and loaded runs.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Run `uv run task test` (with the xdist flags) 5 times from the repository root. Record the pass, fail and error counts
  and the wall time of each run.
- Generate CPU load with plain shell busy loops, one per core, started in the background and killed afterwards. This
  adds no new dependency. Under that load, run
  `uv run pytest apps/sandbox-executor/tests/test_turn_supervisor.py apps/sandbox-executor/tests/test_url_message_fuzz.py`
  50 times in a shell loop and count failures. Run it once more with `-n auto` to add worker contention.
- Run `uv run task test-stress` once with the final configuration.
- Check `git status` after the runs: it must be clean.
- Record everything in the execution notes as counts. Do not keep any log or ledger files in the repository.

### Dependencies & Criticality

- **Depends on:** Steps 4 and 6
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 4 (stability proof); intent constraint 7 (no auxiliary ledgers).
- **Potential failure modes for this step:** a rare flake appears only under load; the load loops are left running.
- **Guardrails and early‑abort checks:** start the load processes from one script that installs a trap to kill them on
  exit. On any failure, go back to step 4 or 5 for that test and restart the 50-run count from zero.

### Success & Discard Criteria

- **Success:** 5 of 5 full runs are green; 50 of 50 loaded runs are green; the stress suite is green.
- **Discard:** if a test still flakes after two fix iterations, stop, record the failure output, and report the plan as
  partially successful without claiming stability.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 0.5   |
| impact_pred         | 8.0   |
| cost_pred           | 2.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 6.15  |

### Step Metrics Rationale

`EV = 0.85 * 8.0 + 0.5 * 3.0 - 0.3 * 0.5 - 2.0 = 6.80 + 1.50 - 0.15 - 2.0 = 6.15`. The run time is bounded: 50 short
runs of two modules plus 5 parallel full runs.

---

## Step 8: Measure after the change, locally and in the PR's CI, and count git processes again

- **Sub‑intent recommendation:** NO
- **Reasoning:** Reporting real numbers is required. It depends on CI runs of the PR itself.
- **Step Type:** INFO_GATHERING
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Produce the before and after table from real runs only.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Locally: run the full unit selection serially (`-n 0`) and with the final xdist flags, on the same host as step 1.
- GIT_TRACE: repeat the step 1 trace over test_flow.py and test_executor.py (serial, so the counts compare with the
  baseline) and count by subcommand.
- CI: once the PR runs, read the job and step timings with `gh run view --json jobs` and the pytest summary time from
  the logs for `unit (ubuntu-latest)`, `unit (macos-latest)` and `Workspace survival (ubuntu-latest)`. Compare them with
  the intent's fbd3b35 baseline (251/242 s, 404/382 s, 430/417 s).
- Build one table with these columns: environment, step or job, before, after, delta and speed-up. Put it in the
  execution notes and in the PR body. If CI numbers are not yet available when the notes are written, write "pending"
  rather than estimating.

### Dependencies & Criticality

- **Depends on:** Steps 6 and 7
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 5 (real data only); `world/constraints.md` section 1 (pushing only the execution
  branch, never `main`).
- **Potential failure modes for this step:** CI queueing or cancellation leaves the numbers incomplete; numbers are
  mistaken for estimates.
- **Guardrails and early‑abort checks:** quote run IDs or URLs only in the PR body and the execution notes, not in
  repository files. Never write synthetic numbers.

### Success & Discard Criteria

- **Success:** the table is complete with real local, CI and git-count figures. Targets (not gates): unit (ubuntu)
  pytest at most 120 s, unit (macos) at most 200 s, Workspace survival at most 250 s, and git processes per test reduced
  by at least 40 per cent.
- **Discard:** if CI shows a regression on any OS compared with the baseline, return to step 6 tuning (a worker count
  per OS) before reporting.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 0.6   |
| impact_pred         | 6.0   |
| cost_pred           | 1.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 4.42  |

### Step Metrics Rationale

`EV = 0.85 * 6.0 + 0.5 * 2.0 - 0.3 * 0.6 - 1.5 = 5.10 + 1.00 - 0.18 - 1.5 = 4.42`. The step is mechanical. The main risk
is CI availability, which is outside the agent's control.

---

## Step 9: Update the testing docs, lint, format and assemble the PR

- **Sub‑intent recommendation:** NO
- **Reasoning:** Required documentation with low risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Describe the hermetic git config, the template repositories and parallel execution, including how to
  run serially for debugging.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- `apps/sandbox-executor/docs/hermetic_testing.md`: add a short section on the session-wide test git config (the keys it
  sets and why, and that child suites inherit it), the per-worker template repositories with the read-only isolation
  guard, and parallel execution (`-n auto`, the chosen `--dist`, and that integration and stress stay serial). Explain
  how to debug serially with `-n 0` or `-p no:xdist`. Update the existing command example at the "run the unit
  selection" line to match `task test`.
- `docs/testing/testing_guide.md`: update the run commands to show the parallel default and the serial debug form.
- Use Australian English throughout (for example "behaviour", "initialise", "serialise"). Do not include harness paths,
  bean ids or review ids.
- Run `uv run task lint` (ruff, ruff format, uv lock check) and `uv run task lint-docs`. Fix the formatting with
  `uv run task format-docs` if needed, limited to the touched markdown files.
- Write the PR body with a summary, the declared file list, the before and after table from step 8, the stability
  evidence from step 7, and any production follow-ups recorded in steps 4 and 5.

### Dependencies & Criticality

- **Depends on:** Steps 2 to 8
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent constraints 6 and 7; `docs/safety.md` "Human review is the final gate" (the PR targets the
  intent branch, and the human merges to `main`).
- **Potential failure modes for this step:** prettier rewrites unrelated markdown; American spellings slip in.
- **Guardrails and early‑abort checks:** run prettier only on touched files, and check that `git diff --stat` lists only
  the declared files.

### Success & Discard Criteria

- **Success:** both lint tasks pass, the docs describe all three mechanisms and the serial debug flags, and the diff
  contains only declared files.
- **Discard:** none; documentation failures are fixed in place.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.5   |
| impact_pred         | 4.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 3.03  |

### Step Metrics Rationale

`EV = 0.92 * 4.0 + 0.5 * 1.0 - 0.3 * 0.5 - 1.0 = 3.68 + 0.50 - 0.15 - 1.0 = 3.03`. Routine documentation and lint.

---

## Strategy Rationale

The intent is accepted as is. It is specific and measured, it stays inside test-side files, and it has a large expected
payoff: every future PR runs three jobs that currently take 4 to 7 minutes each. The order puts the correctness
improvements first: the hermetic git config (step 2) and the deterministic turn-supervisor tests (step 4). The
performance changes come after them: templates (step 3) and xdist (step 6), followed by the audit (step 5) and the
stability proof (step 7). A failure under xdist can therefore be traced to one variable.

Aggregation of the step metrics:

- **Entropy:** sum of the step entropies, `0.3 + 1.2 + 2.0 + 0.6 + 1.0 + 1.5 + 0.5 + 0.6 + 0.5 = 8.2`, under the 10.0
  budget.
- **Cost:** sum of the step costs, `2.0 + 3.0 + 5.0 + 2.0 + 3.0 + 3.0 + 2.0 + 1.5 + 1.0 = 22.5`. The overall table uses
  22.5.
- **p_success:** not a product, because steps 3 and 5 have graceful fallbacks (revert one fixture, or serialise one test
  with `xdist_group`) that do not fail the plan. The bottleneck chain is step 2, then step 6, then step 7:
  `0.88 * 0.80 * 0.85 = 0.60`. Adjusted upwards for the fallbacks and the second attempt allowed in step 7, this gives
  an overall `p_success = 0.72`.
- **Impact:** a qualitative overall of 70. This is not the sum of the step impacts (110), because the speed gains from
  steps 2, 3 and 6 overlap. A faster pipeline on every PR plus the removal of a known flake and a known hermeticity
  failure justify a high value.
- **Learning value:** 6.0. The xdist tuning, the git process attribution and the audit list are reusable for the
  integration jobs.
- **EV:** `EV = 0.72 * 70.0 + 0.5 * 6.0 - 0.3 * 8.2 - 22.5 = 50.40 + 3.00 - 2.46 - 22.5 = 28.44`.

The plan makes two strong assumptions:

- A plain environment variable set for the whole session (through `MonkeyPatch.context`) is acceptable in conftest. It
  is the only way child processes inherit it.
- Execnet, which pytest-xdist requires, counts as part of the single allowed dependency. This is recorded explicitly in
  the execution notes.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md` section 3: test changes must be declared. See "Declared test-side change set" above.
  - `ruleset.md` section 1: dependencies are declared in `pyproject.toml` and managed by uv; Python 3.13.
  - `holon-config/world/constraints.md` section 1: work only on the execution branch under this plan; never commit to
    `main`.
  - `constraints.md` section 3: the ledgers are append-only, so tests must never write the real ledger (checked in step
    5).
  - `docs/safety.md`: sandboxing, entropy budgets and human review as the final gate.
  - Intent answers: no production change, exactly one new dependency, no test weakened, skipped or deleted.
- **Potential violations or edge cases:**
  - Template repositories could leak commits between tests if the remote URL is not rewritten.
  - `patch.dict(os.environ, clear=True)` sites could silently drop the hermetic config.
  - The lock could pull in more than pytest-xdist and execnet.
  - Making the stress canary's child parallel could weaken what the hermetic guard proves.
  - The temptation to loosen the turn-supervisor assertion instead of fixing the timing.
- **Mitigations built into the plan:**
  - A read-only template tree and a remote URL check per test.
  - An audit of the `clear=True` sites.
  - A line-by-line review of the lock diff, with `uv run task lint`.
  - Stress suite runs before and after `pytest_suite_command` changes, with a documented fallback to a serial child.
  - Unchanged assertions plus an injected join or signal.
  - A `git status` cleanliness check after each run.
- **Residual risk accepted (and why):** a rare scheduling flake may survive the 50 loaded runs. This is accepted because
  the intent's stability bar is met, and any remaining flake is reported, never masked. CI timings on the 3-core macOS
  runner may improve less than on ubuntu. This is accepted, with a worker count per OS as the fallback.
- **Allocated Entropy Budget:** 10.0
- **Predicted Plan Entropy:** 8.2
- **Budget Compliance:** The strategy fits within budget (8.2 of 10.0), so it runs as one plan with no sub-intent list.
