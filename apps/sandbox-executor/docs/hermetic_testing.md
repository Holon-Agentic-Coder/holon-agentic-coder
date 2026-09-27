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

- `test_executor.py`: `test_main_execution_flow`, `test_main_git_recovery_on_corrupted_repo`,
  `test_main_decomposition_flow`, `test_main_custom_holon_repo_dir_not_deleted`, `test_main_default_workspace_deleted`,
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
   `cleanup_repo_dir(repo_dir, raise_on_error=True)` upon initialization.
3. If a unit test executes an entrypoint without setting `HOLON_REPO_DIR` or patching `get_workspace_dir()`,
   `cleanup_repo_dir` deletes `~/.holon-sandbox/workspace`.
4. As a result, subsequent git operations in the host executor fail (`fatal: not a git repository`), forcing the
   executor into fallback re-initialization and pushing orphan commits.

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

- Use `git init --bare` to create local bare git repositories in temporary directories.
- Mount the bare repository into Docker containers using `-v {bare_repo}:/mock_remote.git` and
  `-e HOLON_REPO_URL=/mock_remote.git`.
- Isolate container networking using `--network none`.
- Inspect results and verify commits directly from the local bare repository.
- Gracefully skip if Docker daemon is not available.

### Rule 3: Assert Cleanup Invariants

Unit tests that verify cleanup behaviour (`_rmtree`, `_clear_dir_contents`) must affirmatively assert that all cleanup
operations target paths strictly within the allocated temporary fixture directory.

### Rule 4: Validate via Canary Regression Guard

All entrypoint test suites are protected by `test_sandbox_hermetic_guard.py`. This canary guard runs tests under
simulated container conditions (`HOLON_ROLE=executor`, `HOLON_IN_SANDBOX=1`, `USER=holon`) with a simulated
`~/.holon-sandbox/workspace` containing `canary.txt`. The test fails if the canary directory or marker file is touched
or deleted.

---

## 3. Sandbox Safety Rail

When verifying tests in an active container environment:

- **NEVER** run full discovery (`python3 -m unittest discover`) directly inside `/home/holon/.holon-sandbox/workspace`.
- Always verify from an isolated scratch copy:
  ```bash
  export HOLON_REPO_DIR=/tmp/holon-test-fixture
  cp -r /home/holon/.holon-sandbox/workspace /tmp/holon-fixture
  cd /tmp/holon-fixture
  PYTHONPATH=apps/sandbox-executor/src python3 -m unittest discover -s apps/sandbox-executor/tests
  ```
