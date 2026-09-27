"""Canary guard test simulating container conditions to prove the whole suite stays hermetic.

Scope note: this guard runs the *entire* ``apps/sandbox-executor/tests`` directory, not a curated
module list. The bean 0019 defect was suite-wide -- an unguarded test could resolve the live
workspace and delete it -- so a single-module or few-module canary cannot establish that the
hazard is gone. A list of "the modules we remember" silently rots as the suite grows, which is
exactly how the original guard ended up excluding ``test_agent_runner.py``, the one module that
exercises the real ``cleanup_repo_dir``/``_rmtree``. Running the directory also covers any new
test file the moment it is added.

The child suite is launched with ``uv run pytest`` -- the repository's only sanctioned test runner
-- so it inherits the root pytest configuration and the unit job's marker selection. Launching it
with ``python -m unittest`` bypasses both: ``unittest`` has no concept of pytest markers, so it
re-runs the ``integration_test``-marked Docker image tests that the unit job never builds, and the
guard then fails for an environment reason that has nothing to do with hermeticity.
"""

import os
import subprocess
import tempfile
import unittest

from tests.hermetic_fixtures import (
    REPO_ROOT,
    TESTS_DIR,
    UV,
    pytest_suite_command,
    simulated_container_env,
    uv_available,
)

#: Sentinel set in the child environment so a child run can never recurse back into this guard.
CHILD_ENV_VAR = "HOLON_HERMETIC_GUARD_CHILD"

#: Generous ceiling: the child runs the whole non-integration suite, which itself nests the
#: negative-control harness.
CHILD_TIMEOUT_SECONDS = 1800


class TestSandboxHermeticGuard(unittest.TestCase):
    """Canary test to verify that running entrypoint test suites under simulated container conditions
    (HOLON_ROLE=executor, HOLON_IN_SANDBOX=1, USER=holon) does not touch the simulated sandbox workspace.
    """

    def setUp(self):
        """Skip inside a child run, so the guard can never recurse into itself."""
        if os.environ.get(CHILD_ENV_VAR) == "1":
            self.skipTest("running inside the guard's own child suite")

        if not uv_available():
            self.skipTest(f"{UV} is not available; this suite runs only under '{UV} run pytest'.")

        # ``cwd=REPO_ROOT`` below must be a real directory, and the child must have a suite to run:
        # pytest exits 5 on "no tests collected", which would otherwise surface as an opaque
        # failure of this guard rather than a missing directory.
        self.assertTrue(os.path.isdir(REPO_ROOT), f"repo root does not exist: {REPO_ROOT}")
        tests_path = os.path.join(REPO_ROOT, TESTS_DIR)
        self.assertTrue(os.path.isdir(tests_path), f"test directory does not exist: {tests_path}")
        self.assertTrue(
            any(name.startswith("test_") for name in os.listdir(tests_path)),
            f"no test modules found under {tests_path}",
        )

    def test_canary_workspace_survives_entrypoint_tests(self):
        with tempfile.TemporaryDirectory() as sim_home:
            sim_sandbox_workspace = os.path.join(sim_home, ".holon-sandbox", "workspace")
            os.makedirs(sim_sandbox_workspace, exist_ok=True)

            canary_marker = os.path.join(sim_sandbox_workspace, "canary.txt")
            canary_content = "canary-alive-guard-marker-2026"
            with open(canary_marker, "w") as f:
                f.write(canary_content)

            # Create dummy repo files inside the simulated workspace
            dummy_repo_file = os.path.join(sim_sandbox_workspace, "important_code.py")
            with open(dummy_repo_file, "w") as f:
                f.write("# important code\n")

            dummy_git_dir = os.path.join(sim_sandbox_workspace, ".git")
            os.makedirs(dummy_git_dir, exist_ok=True)

            # Run the whole suite through the sanctioned runner and the unit job's selection, from
            # the repository root so the child inherits the root pytest configuration.
            cmd = pytest_suite_command()
            res = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                env=simulated_container_env(sim_home, CHILD_ENV_VAR),
                capture_output=True,
                text=True,
                timeout=CHILD_TIMEOUT_SECONDS,
            )
            self.assertEqual(
                res.returncode,
                0,
                f"The suite failed under simulated sandbox environment!\n"
                f"Command: {' '.join(cmd)}\n"
                f"Stdout:\n{res.stdout}\nStderr:\n{res.stderr}",
            )

            # Assert canary workspace still exists
            self.assertTrue(
                os.path.exists(sim_sandbox_workspace),
                "Simulated sandbox workspace was deleted during entrypoint test runs!",
            )

            # Assert canary marker file is intact
            self.assertTrue(
                os.path.exists(canary_marker),
                "Canary marker file was deleted during entrypoint test runs!",
            )
            with open(canary_marker) as f:
                self.assertEqual(
                    f.read(),
                    canary_content,
                    "Canary marker content was modified during entrypoint test runs!",
                )

            # Assert other workspace files remain intact
            self.assertTrue(
                os.path.exists(dummy_repo_file),
                "Dummy workspace file was deleted during entrypoint test runs!",
            )
            self.assertTrue(
                os.path.exists(dummy_git_dir),
                "Dummy .git directory was deleted during entrypoint test runs!",
            )

    def test_negative_control_canary_is_actually_live(self):
        """Prove the simulated workspace is the path an unpatched cleanup really destroys.

        Without this control the canary guard could pass vacuously: if nothing ever resolved to the
        simulated workspace, nothing would ever delete it and the guard would be unfalsifiable. Here
        the same simulated container environment runs a deliberately *unpinned* call to the real
        ``cleanup_repo_dir`` against ``get_workspace_dir()``, and the canary is expected to die.
        """
        with tempfile.TemporaryDirectory() as sim_home:
            sim_sandbox_workspace = os.path.join(sim_home, ".holon-sandbox", "workspace")
            os.makedirs(sim_sandbox_workspace, exist_ok=True)
            canary_marker = os.path.join(sim_sandbox_workspace, "canary.txt")
            with open(canary_marker, "w") as f:
                f.write("canary-should-not-survive")

            env = simulated_container_env(sim_home, CHILD_ENV_VAR)
            env["SIM_EXPECTED_WORKSPACE"] = sim_sandbox_workspace

            script = (
                "import os\n"
                "from sandbox_executor.agent_runner import cleanup_repo_dir, get_workspace_dir\n"
                "ws = get_workspace_dir()\n"
                "expected = os.environ['SIM_EXPECTED_WORKSPACE']\n"
                "assert ws == expected, f'workspace resolution drifted: {ws!r} != {expected!r}'\n"
                "cleanup_repo_dir(ws, raise_on_error=True)\n"
            )
            res = subprocess.run(
                [UV, "run", "--no-sync", "python", "-c", script],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=CHILD_TIMEOUT_SECONDS,
            )
            self.assertEqual(
                res.returncode,
                0,
                f"Negative control harness failed instead of cleaning up:\nStdout:\n{res.stdout}\n"
                f"Stderr:\n{res.stderr}",
            )
            self.assertFalse(
                os.path.exists(sim_sandbox_workspace),
                "Negative control is not live: the unpatched cleanup did not delete the simulated "
                "workspace, so the positive canary guard proves nothing.",
            )
            self.assertFalse(
                os.path.exists(canary_marker),
                "Negative control canary survived an unpatched cleanup_repo_dir() call.",
            )
