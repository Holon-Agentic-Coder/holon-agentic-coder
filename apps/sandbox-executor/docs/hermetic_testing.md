# Hermetic Testing Standards in Sandbox Executor

## 1. Background & Root Cause Analysis

In previous versions, running the `apps/sandbox-executor` unit suite inside the executor container could delete the live
workspace of the running executor (`~/.holon-sandbox/workspace`), which forced the orphan-commit fallback seen in the
Bean 0019 incident.

### Corrected attribution

The mechanism below is accurate; the earlier blame assigned to `test_intent_creator.py` was **not**. That module already
patched `cleanup_repo_dir` (decorators at lines 9, 66, 113 and 161 of the pre-fix base), so running it alone never
removed a live workspace. An audit of every test that calls a role entrypoint without patching cleanup found the actual
exposed callers, all in one module:

- `test_executor.py`: `test_main_execution_flow`, `test_main_git_recovery_on_corrupted_repo`, the over-budget `main()`
  tests that run through `_run_over_budget_main` or `_run_over_budget_main_against_local_origin`,
  `test_main_custom_holon_repo_dir_not_deleted`, `test_main_default_workspace_deleted`,
  `test_main_raises_exception_on_failure`, `test_main_raises_runtime_error_on_cleanup_failure`,
  `test_main_mount_point_clears_contents`, `test_main_git_add_not_called_on_failure`. Two of these also exercise
  `_rmtree` directly: `test_main_default_workspace_deleted` and `test_main_raises_runtime_error_on_cleanup_failure`.

Because a guard test that cannot fail is worthless, `tests/test_sandbox_hermetic_guard.py` pairs the canary guard with a
negative control that runs a deliberately unpinned `cleanup_repo_dir()` and asserts the simulated workspace **is**
destroyed. If the resolution path ever stops pointing at the workspace, the negative control fails and the guard's
silence is no longer trusted.

### Root Cause

Under sandbox conditions (indicated by `HOLON_ROLE=executor`, `HOLON_IN_SANDBOX=1`, `/.dockerenv`, or `USER=holon`):

1. `get_workspace_dir()` defaults to `~/.holon-sandbox/workspace` when `HOLON_REPO_DIR` is not explicitly set.
2. Entrypoints (`intent_creator.main()`, `planner.main()`, `executor.main()`) invoke
   `cleanup_repo_dir(repo_dir, raise_on_error=True)` upon initialisation.
3. If a unit test executes an entrypoint without setting `HOLON_REPO_DIR` or patching `get_workspace_dir()`,
   `cleanup_repo_dir` deletes `~/.holon-sandbox/workspace`.
4. As a result, subsequent git operations in the host executor fail (`fatal: not a git repository`), forcing the
   executor into fallback re-initialisation and pushing orphan commits.

---

## 2. Mandatory Rules for Tests

### Rule 1: Every Role Entrypoint Test Must Pin `HOLON_REPO_DIR` to a Fixture

Any unit test invoking `intent_creator.main()`, `planner.main()`, or `executor.main()` MUST:

- Create an isolated temporary directory using `tempfile.TemporaryDirectory()`.
- Pin the environment variable `HOLON_REPO_DIR` to that temporary directory using
  `unittest.mock.patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir})`.
- Patch `get_workspace_dir` to return `tmp_dir`.
- Patch or scope `cleanup_repo_dir` to ensure cleanup only operates within the fixture.

Example pattern:

```python
with tempfile.TemporaryDirectory() as tmp_dir:
    with (
        patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir}),
        patch("sandbox_executor.entrypoint.intent_creator.get_workspace_dir", return_value=tmp_dir),
        patch("sandbox_executor.entrypoint.intent_creator.cleanup_repo_dir") as mock_cleanup,
    ):
        intent_creator.main()
        mock_cleanup.assert_called_once_with(tmp_dir, raise_on_error=True)
```

### Rule 2: Decouple Integration Tests from External Network and SSH Mounts

Integration tests (`test_intent_creator_integration.py`, `test_planner_integration.py`) must never touch real remotes
such as `github.com` or mount host `~/.ssh` keys:

- Use a named Docker volume as the bare git remote, seeded through `apps/sandbox-executor/tests/hermetic_fixtures.py`.
- Mount the volume at a path derived from its own volume name (`remote_path(volume)`), and pass that same value as
  `-e HOLON_REPO_URL=...`. Never hardcode a shared container path: a fixed path is shared state between concurrently
  running tests, and it happens to coincide with the CLI's `HOLON_MOCK_REMOTE_PATH` override, so a test would silently
  depend on -- or silently exercise -- that branch instead of the normal one.
- Isolate container networking using `--network none`.
- Inspect results and verify commits from inside the container against the same volume (`remote_show_file`), and reclaim
  the volume with `remove_remote_volume` via `self.addCleanup`.
- Gracefully skip if Docker daemon is not available.

### Rule 3: Assert Cleanup Invariants

Unit tests that verify cleanup behaviour (`_rmtree`, `_clear_dir_contents`) must affirmatively assert that all cleanup
operations target paths strictly within the allocated temporary fixture directory.

### Rule 4: Validate via Canary Regression Guard

The suite is protected by `test_sandbox_hermetic_guard.py`. This canary guard runs under simulated container conditions
(`HOLON_ROLE=executor`, `HOLON_IN_SANDBOX=1`, `USER=holon`) with a simulated `~/.holon-sandbox/workspace` containing
`canary.txt`, and fails if that directory or marker file is touched or deleted.

Two properties make the guard worth keeping:

- It runs the **whole** `apps/sandbox-executor/tests` directory. The bean 0019 hazard was suite-wide, so a curated
  module list cannot prove it is gone -- and such a list is exactly how the earlier version of this guard ended up
  excluding `test_agent_runner.py`, the module that exercises the real `cleanup_repo_dir`/`_rmtree`.
- It launches its child with `uv run pytest` and the unit job's marker selection
  (`-m "not integration_test and not stress"`) and parallel flags (`-n auto --dist worksteal`), through the shared
  helpers in `hermetic_fixtures.py` (`pytest_suite_command`, `simulated_container_env`). It must not shell out to
  `python -m unittest`: `unittest` ignores pytest markers, so it re-runs the `integration_test`-marked Docker image
  tests that the unit job never builds, and the guard then fails for an environment reason unrelated to hermeticity.

### Rule 5: Prove Suite-Wide Safety in CI, Not Locally

No run on a developer machine can establish that the suite is safe for _any_ future test author. Two checks do that job
on every push, and both are machine-enforced:

1. The canary guard above, which runs the entire directory -- so a new test file is covered the moment it is added. It
   is marked `stress` and runs in the `Workspace survival` job rather than the unit matrix, because re-running the whole
   unit selection in a child roughly doubled the unit job's wall time. Its negative control stays in the unit job.
2. `test_workspace_survival_stress.py`, marked `stress`, which clones the repository onto the exact path an executor
   works in (`~/.holon-sandbox/workspace` with `HOME` redirected), runs the unit selection from inside it, and requires
   the workspace, its `.git` and its `HEAD` commit to survive. That is the incident shape; a throwaway-`HOME` canary
   never runs _from_ the workspace and so cannot demonstrate it. It runs in the `Workspace survival` job of
   `.github/workflows/test-stress.yml`, which fails if the suite was skipped rather than executed, and is excluded from
   the ordinary unit matrix by marker expression because it clones the repository and re-runs the suite. That job runs
   on `ubuntu-latest` only, so neither suite-rerunning check runs on macOS.

Markers are declared in `pyproject.toml` and pytest runs with `--strict-markers`, so an unregistered or misspelled
marker fails the run instead of quietly escaping every `-m` selection.

---

## 3. Sandbox Safety Rail

Tests are run with `uv run pytest` and nothing else -- never `python3 -m unittest`, never a bare `pytest`, never a
`.venv` binary (see `.agents/rules.md`).

When verifying tests in an active container environment:

- **NEVER** run full discovery (`uv run pytest`) directly inside `/home/holon/.holon-sandbox/workspace`.
- Always verify from an isolated scratch copy:
  ```bash
  export HOLON_REPO_DIR=/tmp/holon-test-fixture
  cp -r /home/holon/.holon-sandbox/workspace /tmp/holon-fixture
  cd /tmp/holon-fixture
  uv run task test
  ```

---

## 4. Fast, Parallel-Safe Unit Runs

### Session-wide test git config

`tests/conftest.py` has a session-scoped autouse fixture, `hermetic_git_config`, that writes a test-only gitconfig and
sets `GIT_CONFIG_GLOBAL` to it and `GIT_CONFIG_NOSYSTEM=1` for the whole session. No git process started by a test reads
the developer's or the system's config. The file sets:

- `gc.auto=0`, `maintenance.auto=false`, `fetch.writeCommitGraph=false` and `core.fsmonitor=false`, so git never spawns
  housekeeping work that no test asked for (automatic `git maintenance` used to be about 1,300 extra processes over
  `test_flow.py` and `test_executor.py`);
- `core.hooksPath` pointing at an empty directory, so a developer's global hooks never run in a fixture repository;
- `init.defaultBranch=main`, a neutral `user.name` and `user.email`, `commit.gpgsign=false`, `tag.gpgsign=false` and
  `advice.detachedHead=false`, values that fixtures used to set one repository at a time.

The variables live in `os.environ`, so every child process inherits them: the stress canary's child suite, its xdist
workers and every git subprocess. A repository's own `.git/config` still overrides them. A test that builds its own git
environment (for example the `leak_env` tests in `test_cli.py`, which strip every `GIT_*` variable and point `HOME` at
an empty directory) keeps that isolation.

### Template repositories

`flow_repos` in `test_flow.py` no longer runs init, seed commit, push and clone for every test. A session-scoped
`_flow_repos_template` builds the seeded bare origin and the host clone once per session, which means once per xdist
worker, because `tmp_path_factory` is per worker. Each test copies both into its own `tmp_path` and re-points the host's
`origin` at its private copy, so it starts from an identical state and cannot see another test's commits. The template
tree is read-only: a push to or commit in the template instead of the copy fails loudly instead of leaking state into
later tests. Only convert a fixture to a template when its setup is measurably expensive, and never one whose test
asserts on the creation steps themselves.

### Parallel execution

The unit selection runs under pytest-xdist with `-n auto --dist worksteal`, in `uv run task test`, in the unit workflow
and in `pytest_suite_command`. `auto` starts one worker per CPU. `worksteal` rebalances the uneven test durations better
than `load` or `loadfile`. The integration and stress selections stay serial (`-n` is not in `addopts`). A test that
runs under xdist must not depend on order, on the current working directory or on another test's state, and must never
write to a fixed path, the repository working tree or the real home directory: use `tmp_path`, `tmp_path_factory` or a
per-test patch of the default path. Parametrise ids must be identical in every worker, so never put a random value into
an id; give such a case a fixed `pytest.param(..., id=...)`.

To debug serially, either keep xdist loaded but start no workers, or disable the plugin:

```bash
uv run task test -n 0
uv run pytest -p no:xdist -m "not integration_test and not stress"
```
