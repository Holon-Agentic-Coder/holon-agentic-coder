"""Canary guard test simulating container environment to ensure unit tests do not mutate live workspaces.

The child suite is launched with ``uv run pytest`` -- the repository's only sanctioned test runner
-- so it inherits the suite's own marker policy (``-m "not integration_test"``). Launching it with
``python -m unittest`` instead bypasses that policy: ``unittest`` has no concept of pytest markers,
so it re-runs the ``integration_test``-marked Docker image tests that the unit job never builds,
and the guard then fails for an environment reason that has nothing to do with hermeticity.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

#: The only sanctioned way to run this suite (see ``.agents/rules.md``): never invoke
#: ``python``/``pytest`` binaries directly.
UV = "uv"

#: Sentinel set in the child environment so a child run can never recurse back into this guard.
CHILD_ENV_VAR = "HOLON_HERMETIC_GUARD_CHILD"

#: Entrypoint test modules that can reach ``get_workspace_dir`` / ``cleanup_repo_dir``.
#: ``test_agent_runner`` is deliberately included: it is the module that exercises the real
#: ``cleanup_repo_dir``/``_rmtree``, so excluding it would leave the hazard this guard covers
#: unmonitored. This list must never contain the guard module itself.
ENTRYPOINT_TEST_PATHS = [
    "apps/sandbox-executor/tests/test_intent_creator.py",
    "apps/sandbox-executor/tests/test_planner.py",
    "apps/sandbox-executor/tests/test_executor.py",
    "apps/sandbox-executor/tests/test_agent_runner.py",
]

#: Repository root -- the directory that owns ``pyproject.toml``, hence the pytest
#: ``testpaths`` / ``pythonpath`` / ``markers`` configuration this guard must inherit.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

#: Marker expression mirroring the unit job's selection in ``.github/workflows/test-unit.yml``.
NOT_INTEGRATION = "not integration_test"

CHILD_TIMEOUT_SECONDS = 300


def _container_env(sim_home: str) -> dict[str, str]:
    """Build the environment of an executor container whose home is a throwaway directory.

    ``HOLON_REPO_DIR`` is deliberately absent so that any unpatched ``get_workspace_dir()`` resolves
    into the simulated workspace -- which is the hazard being guarded.
    """
    env = os.environ.copy()
    env["HOME"] = sim_home
    env["USER"] = "holon"
    env["USERNAME"] = "holon"
    env["HOLON_ROLE"] = "executor"
    env["HOLON_IN_SANDBOX"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("HOLON_REPO_DIR", None)
    env[CHILD_ENV_VAR] = "1"
    return env


class TestSandboxHermeticGuard(unittest.TestCase):
    """Canary test to verify that running entrypoint test suites under simulated container conditions
    (HOLON_ROLE=executor, HOLON_IN_SANDBOX=1, USER=holon) does not touch the simulated sandbox workspace.
    """

    def setUp(self):
        """Skip inside a child run, so the guard can never recurse into itself."""
        if os.environ.get(CHILD_ENV_VAR) == "1":
            self.skipTest("running inside the guard's own child suite")

        if not shutil.which(UV):
            self.skipTest(f"{UV} is not available; this suite runs only under '{UV} run pytest'.")

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

            # Run the entrypoint test modules through the suite's sanctioned runner and selection,
            # from the repository root so the child inherits the root pytest configuration.
            cmd = [
                UV,
                "run",
                "--no-sync",
                "pytest",
                "-p",
                "no:cacheprovider",
                "-q",
                "--tb=short",
                "-m",
                NOT_INTEGRATION,
                *ENTRYPOINT_TEST_PATHS,
            ]
            res = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                env=_container_env(sim_home),
                capture_output=True,
                text=True,
                timeout=CHILD_TIMEOUT_SECONDS,
            )
            self.assertEqual(
                res.returncode,
                0,
                f"Entrypoint test modules failed under simulated sandbox environment!\n"
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

            env = _container_env(sim_home)
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
