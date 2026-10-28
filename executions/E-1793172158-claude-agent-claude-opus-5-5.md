# Execution Record: E-1793172158-claude-agent-claude-opus-5-5

## Summary

Implementation of plan `P-1793104256-claude-agent-claude-opus-5-5` for intent
`I-1793104241-faster-unit-tests-xdist-and-cheaper-git-fixtures`. All nine steps were carried out on this execution
branch, and only test-side files, dev dependencies, the unit workflow and testing docs changed (no production code). The
changes: a session-wide hermetic git config (`GIT_CONFIG_GLOBAL` plus `GIT_CONFIG_NOSYSTEM`, inherited by child suites),
a per-worker read-only template for `flow_repos`, deterministic lingering-pipe and process-sampler tests, a
parallel-safety audit that moved every test write out of the repository and the real `~/.holon`, and pytest-xdist 3.8.0
(`-n auto --dist worksteal`) in `task test`, the unit workflow and the stress canary's child suite.

Results on this host (18 cores): the unit selection went from 89.57 s serially to 29.82 s (a 3.0x speed-up, bounded by
one 24.9 s CPU-bound test), and the stress suite from 185.83 s to 61.80 s. Over `test_flow.py` and `test_executor.py`,
git processes went from 12,481 to 9,234 (−26 per cent, under the 40 per cent target, because the rest is spawned by
production flow code under test), with automatic `git maintenance` runs going from 1,308 to 0. The suite now passes
under a hostile developer git config, including the intent's known `core.hooksPath` failure of the workspace-survival
stress test. Stability: 5 of 5 parallel full runs, 50 of 50 loaded runs plus one loaded parallel run, and the stress
suite are all green. On CI, at the first all-green head `8925e19`, `unit (macos-latest)` went from 404 s to 178 s,
`unit (ubuntu-latest)` from 251 s to 136 s and `Workspace survival` from 430 s to 273 s (see CI After-Numbers below).

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.85  |
| entropy_actual        | 7.6   |
| impact_actual         | 60.0  |
| cost_actual           | 21.0  |
| learning_value_actual | 6.5   |

## Steps Executed

1. **Step 1 (baseline).** Host: Linux 7.0.14 (linuxkit, aarch64), 18 cores, git 2.47.3, Python 3.13.14, pytest 9.1.1.
   `uv` was missing and was installed with `pip install --user uv` (0.13.6), then `uv sync --locked`.
   - Serial unit selection (`uv run pytest -m "not integration_test and not stress" --durations=30`):
     `2464 passed, 15 deselected, 651 subtests passed in 89.57s`.
   - Slowest calls: `test_agent_output_anchor_on_a_preceding_line_is_redacted` 24.93 s (CPU-bound redaction, no git),
     `test_no_credential_fragment_in_any_message[doctor]` 9.11 s,
     `test_long_tool_call_is_not_judged_by_the_streaming_stall_window` 5.02 s,
     `test_agent_output_cut_never_splits_a_secret_from_its_anchor` 5.00 s; everything else is under 2.3 s.
   - Fixture setup over `test_flow.py` and `test_executor.py` is only 2.46 s in total on this host (`flow_repos` setup
     is about 0.02 s per test). Almost all git work happens inside test bodies (the `FakeRoleAgents` clones), so fixture
     templating has a small ceiling here.
   - GIT_TRACE over `test_flow.py` and `test_executor.py` (573 tests, serial, 53.78 s): 12,481 built-in git processes
     (about 21.8 per test): rev-parse 2,132, config 1,395, maintenance 1,308, upload-pack 919, rev-list 838, fetch 605,
     for-each-ref 596, unpack-objects 540, pack-objects 540, show 462, commit 382, add 364, receive-pack 324, push 324,
     clone 313, init 208. The trace file was written to a fresh temporary directory and deleted after counting.
   - Stress suite (`uv run pytest -m stress`): `2 passed, 2477 deselected in 185.83s`.
   - CI reference row (intent baseline, main fbd3b35): unit (ubuntu) 251 s job / 242 s pytest, unit (macos) 404 s / 382
     s, Workspace survival 430 s / 417 s.
2. **Step 2 (hermetic git config).** Added the session-scoped autouse fixture `hermetic_git_config` to
   `tests/conftest.py`. It writes a test-only gitconfig (`gc.auto=0`, `maintenance.auto=false`,
   `fetch.writeCommitGraph=false`, `core.hooksPath=<empty dir>`, `core.fsmonitor=false`, `init.defaultBranch=main`, a
   neutral `user.name`/`user.email`, `commit.gpgsign=false`, `tag.gpgsign=false`, `advice.detachedHead=false`) and sets
   `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_NOSYSTEM=1` through a session `pytest.MonkeyPatch.context()`. Added
   `test_suite_git_config_is_the_hermetic_test_config` to `tests/test_git.py` (hooksPath resolves to the empty test
   hooks directory, `--show-origin` names the test file).
   - `clear=True` audit: instead of reading 69 sites by hand, a temporary spawn hook (appended to `conftest.py` for one
     run, then removed) logged every git process started without `GIT_CONFIG_GLOBAL`. 817 spawns, all in `test_cli.py`
     and all under the `leak_env` fixture, which deliberately deletes every `GIT_*` variable and sets `HOME` to an empty
     per-test directory, because its tests assert that no `GIT_CONFIG_*` variable reaches the container environment.
     Those tests are already isolated from the developer's config through `HOME` and stay unchanged. None of the 69
     `patch.dict(os.environ, ..., clear=True)` sites in the other modules spawns git.
   - No test asserts on a missing identity or on the global or system scope
     (`test_credential_helper_answers_from_the_environment` builds its own env).
   - Hostile developer config check (a temporary HOME whose `.gitconfig` sets `core.hooksPath` to failing `pre-commit`
     and `pre-push` hooks, `init.defaultBranch=trunk` and `commit.gpgsign=true` with a missing key): full unit selection
     `2465 passed`; the same HOME with the original conftest gives `5 failed, 300 passed, 19 errors` on `test_flow.py`
     plus `test_git.py`.
   - GIT_TRACE after step 2: 11,173 built-in git processes, 0 `git maintenance` (was 1,308).
   - Undeclared file notice (recorded before editing, as the plan requires): the step 5 audit found that
     `tests/test_local_llm.py` mocks `cli.tempfile.mkdtemp` to the fixed paths `/tmp/holon-pi-agent-test` and
     `/tmp/generated-agent`, and the CLI really writes `models.json` and `settings.json` into them. That is a shared
     writable path between xdist workers, so `test_local_llm.py` is added to the change set for a path-only fix (a
     per-test temporary directory; the three assertions that name the path compare against the same per-test value).
3. **Step 3 (template repositories).** `tests/test_flow.py` gained a session-scoped `_flow_repos_template` (per xdist
   worker, because `tmp_path_factory` is per worker) that builds the bare origin, the seed commit and the host clone
   once and then strips every write bit from the tree. `flow_repos` now copies `origin.git` and `host` into the test's
   `tmp_path`, restores the owner write bit, re-points `origin` with `git remote set-url` and asserts the host's origin
   URL is its private copy. HOME handling and the return value are unchanged. The per-dispatch `_configure_identity`
   calls in `FakeRoleAgents.__call__` and `_push_child_branch` were removed (the hermetic config supplies the identity,
   and no test asserts on author or committer). The three in-test-body calls were left as they are.
   - `test_flow.py` fixture setup: 2.46 s before, 0.88 s after (−64 per cent). `test_flow.py` alone:
     `305 passed in 19.80s`.
   - GIT_TRACE after step 3: 9,234 built-in git processes (config 1,395 → 279, receive-pack and push 324 → 249, clone
     313 → 238).
   - Not converted: the seeded origins in `test_executor.py` (`TestExecutorPrePushSync`, `TestExecutorIncrementalNotes`
     and the inline seeds) together take 0.61 s for 21 tests, under the plan's 1 s threshold. The remaining git work in
     `test_flow.py` (for example 2,629 spawns in `TestEntropyBudgetHandoffInFlow`) comes from production flow code
     driving the fake role agents (one clone per dispatch, as the real containers do), so it is out of scope.
4. **Step 4 (deterministic turn-supervisor tests).** `TickStream.close()` now sets `closed` and puts an EOF sentinel on
   the queue, so a blocked `read1` returns at once. `FakeProc.signal` uses it. A `RecordedReaders` helper patches
   `turns._StreamReader` with a recording subclass, so the test's fake `killpg` joins exactly the readers that its
   `run_turn` call started (5 s bound, only a safety net) after closing the pipe. `run_turn`'s own 0.05 s join then
   always finds them finished. Applied to `test_lingering_pipe_holder_group_is_terminated_after_exit` (at SIGTERM) and
   `test_lingering_pipe_holder_ignoring_sigterm_gets_sigkill` (at SIGKILL). The two never-closing tests keep the 0.05 s
   patch. All assertions are unchanged.
   - Before the fix, the old file under load (36 busy loops on 18 cores) gave 0 failures in 20 runs. The macOS CI flake
     does not reproduce on this Linux host, so the fix is justified from the code path, not from a local reproduction.
   - Real-time audit:
     - `TestProcessSamplerOnThisHost::test_busy_grandchild_is_sampled_on_this_host` raced a fixed `time.sleep(1.2)`
       against the child's CPU use (more than 0.3 s expected). It now polls the sampler until the child's CPU exceeds
       0.3 s (20 s deadline). The child burns up to 3 s of CPU (30 s wall cap) and the group is killed in cleanup. The
       assertions are unchanged, and the test went from 2.02 s to 0.39 s.
     - `test_ledger_lock.py:45` and `test_ledger_query.py:671` (`timeout=0.1`) hold the lock for the whole wait, so load
       only delays them; no change.
     - `test_ledger_query.py` (`barrier.wait(5.0)`, `join(15.0)`) and `test_ledger_lock.py` (`join(60)`) have generous
       bounds; no change.
     - `test_long_tool_call_is_not_judged_by_the_streaming_stall_window` waits the real 5 s
       `READER_JOIN_TIMEOUT_SECONDS`, because its stream stays open after the fake exit. The outcome is deterministic
       (the reader cannot finish), so it is left as it is. Production follow-up: an injectable reader-join timeout in
       `turns.py` would let it run in milliseconds.
5. **Step 5 (parallel-safety audit).** Method: a temporary `pytest_runtest_teardown` hook (appended to `conftest.py` for
   the audit runs only, then removed) reported, after each test, any file under the candidate paths in the repository
   and in the real home.
   - `/tmp/...` literals: all are mock strings, except `test_local_llm.py`, whose mocked `mkdtemp` returned the fixed
     `/tmp/holon-pi-agent-test` and `/tmp/generated-agent`, which the launcher really wrote into. Fixed with per-test
     directories in a new `setUp`.
   - Writes into the repository (gitignored, so `git status` did not show them):
     - `holon-knowledge/.index/action_index.db` (SQLite): written by `test_flow.py` tests that build a `FlowContext`
       with the default `repo_dir="."` (`test_stage1_intent_validation_failure`,
       `test_intent_stage_fails_fast_on_unresolved_open_questions` and both
       `test_plan_stage_forwards_only_non_blank_stripped_feedback` cases), now `repo_dir=str(tmp_path)`. Also written by
       `test_knowledge.py::test_cli_knowledge_commands`, now `monkeypatch.chdir(temp_dir)`. Production follow-up:
       `holon record` syncs the cwd-relative `DEFAULT_PROJECT_DB_PATH` and ignores `--repo-dir`.
     - `todo/mitm_wire_logs/` and `todo/cache/`: written by `setup_token_reduction_proxy` (repository root from
       `cli._find_git_root`) and by `MitmproxyAddon()` with the cwd-relative default `WIRE_LOG_DIR`. Fixed with an
       autouse `_default_wire_log_dir_in_tmp` fixture in `test_token_reduction.py`, with `host_paths` also patching
       `cli._find_git_root` (and now used by `test_cli_directory_permissions_0o775_and_socket_conflict` and
       `test_setup_token_reduction_proxy_chmod_oserror_fallback`), and with the same patches in
       `test_local_llm.py::test_setup_token_reduction_proxy_excludes_gateway_host_args`.
     - `custom/` (empty directories from `test_setup_token_reduction_proxy_relative_paths`): fixed by the same
       `host_paths` change.
   - Writes into the real home: two tests generated a real root CA into `~/.holon/certs` and `~/.holon/proxy-ca` (fixed
     through `host_paths`). `test_context_cleaner.py::test_mitm_interceptor_and_addon` and
     `test_dump_flow_transaction_case_insensitive_agent_headers` wrote the SQLite `~/.holon/cache/llm_cache.db` (now a
     per-test `cache_dir`). Left unchanged: an empty `~/.holon/tmp` that `run_docker_container` creates with
     `makedirs(exist_ok=True)`, which is safe across workers. A global per-test `HOME` redirect was rejected, because it
     would void the hermetic canary (which relies on an unpatched `get_workspace_dir()` resolving into the simulated
     home).
   - Leaks that are not shared state: `test_executor.py` tests that patch `executor.shutil.rmtree` (the global
     `shutil.rmtree`) make their own `TemporaryDirectory` cleanup a no-op, leaving uniquely named `/tmp/sandbox_test_*`
     and `/tmp/sandbox_executor_test_*` directories. They are unique per test, so they are not a parallel hazard; left
     as they are.
   - cwd, ports and Docker: the only cwd changes use `monkeypatch.chdir`, and no unit test binds a fixed port or creates
     a named Docker resource (the socket tests use fakes).
   - Mistake during the audit: the first version of the audit hook deleted the real `~/.holon` when it found it. That
     directory did not exist when the session started (it was not in the first `ls -a ~`), so it held only artefacts
     written by this session's own test runs. No pre-existing user data was lost. The later audit hooks only reported
     paths that were known to be test-created.
   - Result: after a full serial run, nothing is left under `holon-knowledge/.index`, `todo/cache`,
     `todo/mitm_wire_logs` or `custom`, and `git status --ignored` shows only `.venv/` and `__pycache__`.
     `2465 passed, 15 deselected, 651 subtests passed in 87.43s`.
6. **Step 6 (pytest-xdist).** Added `"pytest-xdist==3.8.0"` (the current release; it requires `pytest>=7.0.0` and
   `execnet>=2.1`) to the root dev group. `uv lock` added exactly two packages, `pytest-xdist` 3.8.0 and its required
   dependency `execnet` 2.1.2. Execnet is counted as part of the one allowed dependency, not as a second one. No other
   package version changed. This host's uv (0.13.6) also bumped the lock-file `revision` from 3 to 5. That line was set
   back to 3, `uv lock --check` accepts it, and the lock diff is now only the two package entries plus the dev-group
   entries. The optional `psutil` extra was not added (`-n auto` falls back to the CPU count).
   - Parallel collection exposed one nondeterministic test id: `test_cli.py::test_strip_url_credentials` embedded a
     random token probe in its parametrize id, so the workers collected different ids. That entry is now a
     `pytest.param` with `id="token-as-user-name"` (same URL and expected value).
   - Tuning (unit selection, this host, wall time from the pytest summary): `-n auto --dist worksteal` 29.82 s,
     `--dist load` 33.96 s, `--dist loadfile` 36.30 s, `-n 4` worksteal 27.20 s, `-n 8` worksteal 27.54 s, `-n logical`
     30.21 s (no psutil, so the same as `auto`). Every configuration is bounded below by the single 24.9 s
     `test_agent_output_anchor_on_a_preceding_line_is_redacted`, so more workers cannot go lower. Chosen:
     `-n auto --dist worksteal`. `worksteal` clearly beats `load` and `loadfile`, and `auto` resolves to the runner's
     CPU count (4 on `ubuntu-latest`, 3 on `macos-latest`), so no per-OS worker count is needed. The 2.6 s gap to `-n 4`
     on this 18-core host is worker start-up.
   - Wired into `task test` (`pytest -n auto --dist worksteal -m ...`), the `Run unit tests` step of `test-unit.yml`,
     and `pytest_suite_command` (so both stress children run in parallel). `addopts`, `test-integration`, `test-stress`
     and the integration and stress workflows stay serial.
   - Debug paths: `uv run task test -n 0` gives `2465 passed in 87.34s`;
     `uv run pytest -p no:xdist -m "not integration_test and not stress"` gives `2465 passed in 86.70s`.
   - Stress suite with the parallel child (against a temporary local commit, because the workspace-survival test clones
     `HEAD`; the commit was undone with `git reset --soft`): `2 passed in 61.80s` (baseline 185.83 s).
   - Known-failure reproduction from the intent: with a HOME whose `.gitconfig` sets `core.hooksPath` to failing hooks,
     `test_workspace_survival_stress.py` fails on the old code (`1 failed in 0.09s`, the clone trips the hook) and
     passes on the new code (`1 passed in 31.54s`).
   - New warning under xdist: `test_ledger_lock.py::test_concurrent_process_appends_do_not_interleave` uses the `fork`
     start method, and an xdist worker has execnet threads, so Python emits a `DeprecationWarning` about forking a
     multi-threaded process (8 warnings, one per child). The children only run the append worker. It is left visible and
     unchanged (the file is not in the change set), and step 7 covers it.
7. **Step 7 (stability).**
   - 5 full runs of `uv run task test` (xdist): all `2465 passed, 651 subtests passed`, in 29.90 s, 29.72 s, 30.42 s,
     30.07 s and 29.58 s.
   - Under load (one shell busy loop per core, started by a script whose `trap` kills them on exit): 50 serial runs of
     `test_turn_supervisor.py` plus `test_url_message_fuzz.py`, `pass=50 fail=0` (each `75 passed`, about 30 s), then
     one run with `-n auto --dist worksteal`, `75 passed in 16.97s`. Total 1,619 s.
   - Stress suite with the final configuration: `2 passed in 65.16s`.
   - Afterwards, `git status --short --ignored` showed only the intended changes, `.venv/`, `.ruff_cache/` (from lint)
     and `__pycache__`. None of the formerly written paths (`holon-knowledge/.index`, `todo/cache`,
     `todo/mitm_wire_logs`, `custom`, `/tmp/generated-agent`, `/tmp/holon-pi-agent-test`) existed.
8. **Step 8 (after-measurements).** All local figures come from the same host as step 1.

   | environment       | step or job                              | before     | after      | delta        | speed-up    |
   | ----------------- | ---------------------------------------- | ---------- | ---------- | ------------ | ----------- |
   | local, 18 cores   | unit selection, serial (`-n 0`)          | 89.57 s    | 87.42 s    | −2.15 s      | 1.02x       |
   | local, 18 cores   | unit selection, `task test` (xdist)      | 89.57 s    | 29.82 s    | −59.75 s     | 3.00x       |
   | local, 18 cores   | stress suite (`-m stress`)               | 185.83 s   | 61.80 s    | −124.03 s    | 3.01x       |
   | local, 18 cores   | git processes, flow and executor (573)   | 12,481     | 9,234      | −3,247       | −26 %       |
   | local, 18 cores   | of which `git maintenance`               | 1,308      | 0          | −1,308       | n/a         |
   | local, 18 cores   | `test_flow.py` fixture setup             | 2.46 s     | 0.88 s     | −1.58 s      | 2.80x       |
   | CI, ubuntu-latest | unit: job / `Run unit tests` pytest time | 251/241.8s | 136/123.4s | −115/−118.4s | 1.85x/1.96x |
   | CI, macos-latest  | unit: job / `Run unit tests` pytest time | 404/382.4s | 178/153.1s | −226/−229.3s | 2.27x/2.50x |
   | CI, ubuntu-latest | Workspace survival: job / pytest time    | 430/417.0s | 273/257.7s | −157/−159.3s | 1.58x/1.62x |

   The CI "before" values are the intent's fbd3b35 baseline. The CI "after" values were filled in during PR review from
   the PR's own GitHub Actions runs at `8925e19`, the first head where every job passed (see CI After-Numbers below).
   Per test, the git process count went from 21.8 to 16.1 (`12481 / 573` and `9234 / 573`), which misses the 40 per cent
   target. The remaining processes are spawned by the production flow under test (for example `FakeRoleAgents` clones
   origin once per dispatch, as the containers do), and the plan does not allow changing that. The parallel figure is
   held at about 30 s by the single CPU-bound `test_agent_output_anchor_on_a_preceding_line_is_redacted` (24.9 s), which
   no worker count can shorten. A repeat xdist run during step 8 gave 32.36 s while the host load average was still
   falling after step 7.

9. **Step 9 (docs, lint).** `apps/sandbox-executor/docs/hermetic_testing.md` gained a section on fast, parallel-safe
   unit runs (the session git config and its keys, the template repositories with the read-only guard,
   `-n auto --dist worksteal`, the parallel-safety rules including fixed parametrize ids, and serial debugging with
   `-n 0` or `-p no:xdist`). Rule 4 now names the child's parallel flags, and the scratch-copy example uses
   `uv run task test`. `docs/testing/testing_guide.md` shows `uv run task test` and both serial debug forms. Prettier
   was run on the touched markdown files only.

## Change Set and Deviations

- Declared files changed: `tests/conftest.py`, `tests/hermetic_fixtures.py`, `tests/test_flow.py`, `tests/test_git.py`,
  `tests/test_turn_supervisor.py`, `tests/test_cli.py` (step 5 parallel-safety list), `pyproject.toml`, `uv.lock`,
  `.github/workflows/test-unit.yml`, `apps/sandbox-executor/docs/hermetic_testing.md`, `docs/testing/testing_guide.md`.
  `tests/test_executor.py` was not changed.
- Files outside the declared set, all path-only redirections found by the step 5 audit, with no assertion removed or
  weakened: `tests/test_local_llm.py` (recorded above before editing), and `tests/test_token_reduction.py`,
  `tests/test_context_cleaner.py` and `tests/test_knowledge.py`. The last three were not recorded in these notes before
  they were edited, as the plan requires; they are recorded here after the fact.
- Assertions: every changed assertion line compares against the same value as before through a per-test variable (the
  three `test_local_llm.py` lines that named `/tmp/generated-agent`). No test was skipped, deleted or loosened.
- Working inside the live workspace: `hermetic_testing.md` section 3 says not to run full discovery directly inside
  `~/.holon-sandbox/workspace`, but the intent requires running from the repository root, which here is that directory.
  The workspace and its history were intact after every run.
- Temporary commits: three local commits were made only so that the workspace-survival stress test (which clones `HEAD`)
  and lint's `git diff --exit-code uv.lock` see the change. Each was undone with `git reset --soft HEAD~1`, so the
  branch tip is still the plan commit and all changes are left uncommitted.

## Production Follow-ups

- `turns.py`: an injectable reader-join timeout (or join hook) would let
  `test_long_tool_call_is_not_judged_by_the_streaming_stall_window` skip its real 5 s `READER_JOIN_TIMEOUT_SECONDS`
  wait.
- `holon record` (`knowledge/records.py`, `append_action_event`) syncs the cwd-relative `DEFAULT_PROJECT_DB_PATH` and
  ignores `--repo-dir`.
- `FlowContext.repo_dir` defaults to `"."`, so a flow stage run without an explicit `repo_dir` reads the real ledgers
  and writes the project index of whatever directory it runs in.
- `test_ledger_lock.py::test_concurrent_process_appends_do_not_interleave` forks from a multi-threaded xdist worker (a
  `DeprecationWarning`). A `forkserver` context would remove the warning if it ever becomes a problem.

## Tests Run

| command                                                                                    | result                                   |
| ------------------------------------------------------------------------------------------ | ---------------------------------------- |
| `uv run pytest -m "not integration_test and not stress" --durations=30` (baseline, serial) | `2464 passed, 651 subtests` in 89.57 s   |
| `uv run pytest -m stress` (baseline)                                                       | `2 passed` in 185.83 s                   |
| full unit selection under a hostile developer git config (after step 2)                    | `2465 passed`                            |
| `uv run task test` (xdist), 5 times                                                        | 5 of 5 `2465 passed`, 29.58 s to 30.42 s |
| `uv run task test -n 0`                                                                    | `2465 passed` in 87.42 s                 |
| `uv run pytest -p no:xdist -m "not integration_test and not stress"`                       | `2465 passed` in 86.70 s                 |
| turn-supervisor plus fuzz modules, 50 serial runs under load                               | 50 of 50 passed                          |
| turn-supervisor plus fuzz modules, `-n auto` under load                                    | `75 passed` in 16.97 s                   |
| `uv run task test-stress` (final configuration)                                            | `2 passed` in 65.16 s                    |
| `test_workspace_survival_stress.py` with a hostile `core.hooksPath`, old code and new code | old: `1 failed`; new: `1 passed`         |
| GIT_TRACE over `test_flow.py` and `test_executor.py`, serial, after                        | 9,234 git processes, 0 `git maintenance` |
| `uv run task lint` (on a temporary commit, so `git diff --exit-code uv.lock` applies)      | passed                                   |
| `uv run task lint-docs`                                                                    | passed                                   |

## CI After-Numbers (Added During PR Review)

Real GitHub Actions timings for this pull request, against the baseline on `main` at `fbd3b35`. Each cell gives two
figures on fixed bases: the job total (job start to job end, from the Actions API) and pytest's own reported time (the
`in N.NNs` figure on pytest's final summary line in the job log). The pytest time excludes the rest of its step (for
example `uv sync` and interpreter start-up), so it is a few seconds below the Actions step duration. Deltas and
speed-ups are computed from these figures. The "after" column is head `8925e19`: the first head on which every job
passed, and the last head that changes code or tests. The commits after `8925e19` change documentation only (the
execution notes and the wording of `hermetic_testing.md`); no code, test, configuration or workflow file changed, so the
`8925e19` figures describe the final test code. CI stayed green at the later heads (`917ef43` job totals: unit macOS 185
s, unit Ubuntu 123 s, Workspace survival 241 s, integration 138 s). The earlier heads are kept for reference only.

| job                                  | baseline `main` `fbd3b35` (job / pytest) | after: head `8925e19` (job / pytest) | delta (job / pytest) | speed-up (job / pytest) | earlier head `7204b9a` (job / pytest) | earlier head `7aa6b25` (job / pytest) |
| ------------------------------------ | ---------------------------------------- | ------------------------------------ | -------------------- | ----------------------- | ------------------------------------- | ------------------------------------- |
| `unit (macos-latest)`                | 404 s / 382.41 s                         | 178 s / 153.14 s                     | −226 s / −229.27 s   | 2.27x / 2.50x           | 171 s / 154.37 s                      | 169 s / 147.41 s                      |
| `unit (ubuntu-latest)`               | 251 s / 241.76 s                         | 136 s / 123.35 s                     | −115 s / −118.41 s   | 1.85x / 1.96x           | 122 s / 111.04 s (failed, see below)  | 140 s / 124.00 s                      |
| `Workspace survival (ubuntu-latest)` | 430 s / 416.95 s                         | 273 s / 257.66 s                     | −157 s / −159.29 s   | 1.58x / 1.62x           | 178 s / 166.51 s                      | cancelled                             |
| `integration (ubuntu-latest)`        | 131 s / 29.71 s                          | 159 s / 30.37 s                      | +28 s / +0.66 s      | 0.82x / 0.98x           | 162 s / 30.79 s                       | 146 s / 29.78 s                       |

Runs: `fbd3b35` 40501662438 (unit), 40501662572 (survival), 40501662750 (integration); `8925e19` 40663895210,
40663895236, 40663895001; `7204b9a` 40662540049, 40662540178, 40662540095; `7aa6b25` 40661853200, 40661853199.

- The `integration` job-total difference is the `Build all Docker images` step (84 s at `fbd3b35`, 95 s at `7aa6b25`,
  109 s at `7204b9a`, 106 s at `8925e19`), which varies with the Docker layer cache. The integration tests themselves
  are flat at about 30 s, as expected: they stay serial and this pull request does not change them.
- `Workspace survival` varied considerably from run to run, although the survival suite itself did not change between
  those heads: 178 s at `7204b9a` against 273 s at `8925e19`. The `8925e19` figure is the one reported as "after"; it is
  still 1.58x faster than the baseline.
- At `7204b9a`, `unit (ubuntu-latest)` failed only on `test_consult_latency_benchmark`, which timed the consult in wall
  time: under `-n auto` the other workers' CPU load pushed its p95 to 71.28 ms against the 50 ms limit. The follow-up
  commit `8925e19` measures the consult in the consulting thread's CPU time (`time.thread_time()`) with the same 100
  iterations, cold prime, match assertion and 50 ms limit.
- At `7aa6b25` every job passed except `Workspace survival`, which was cancelled by a superseding push.
