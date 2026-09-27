"""Canary guard test simulating container environment to ensure unit tests do not mutate live workspaces."""

import os
import subprocess
import sys
import tempfile
import unittest


class TestSandboxHermeticGuard(unittest.TestCase):
    """Canary test to verify that running entrypoint test suites under simulated container conditions
    (HOLON_ROLE=executor, HOLON_IN_SANDBOX=1, USER=holon) does not touch the simulated sandbox workspace.
    """

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

            # Prepare environment simulating active executor container
            env = os.environ.copy()
            env["HOME"] = sim_home
            env["USER"] = "holon"
            env["USERNAME"] = "holon"
            env["HOLON_ROLE"] = "executor"
            env["HOLON_IN_SANDBOX"] = "1"
            # Ensure HOLON_REPO_DIR is NOT set so any unpatched get_workspace_dir would resolve to sim_sandbox_workspace
            env.pop("HOLON_REPO_DIR", None)

            # Run entrypoint unit test modules
            # test_agent_runner is deliberately included: it is the module that exercises the real
            # cleanup_repo_dir/_rmtree, so excluding it would leave the hazard it guards against
            # unmonitored.
            test_modules = [
                "tests.test_intent_creator",
                "tests.test_planner",
                "tests.test_executor",
                "tests.test_agent_runner",
            ]

            pkg_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            src_dir = os.path.join(pkg_dir, "src")
            pythonpath = f"{src_dir}:{pkg_dir}"
            if "PYTHONPATH" in env:
                pythonpath = f"{pythonpath}:{env['PYTHONPATH']}"
            env["PYTHONPATH"] = pythonpath

            for mod in test_modules:
                cmd = [sys.executable, "-m", "unittest", mod]
                res = subprocess.run(
                    cmd,
                    cwd=pkg_dir,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertEqual(
                    res.returncode,
                    0,
                    f"Test module {mod} failed under simulated sandbox environment!\n"
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

            env = os.environ.copy()
            env["HOME"] = sim_home
            env["USER"] = "holon"
            env["USERNAME"] = "holon"
            env["HOLON_ROLE"] = "executor"
            env["HOLON_IN_SANDBOX"] = "1"
            env.pop("HOLON_REPO_DIR", None)
            env["SIM_EXPECTED_WORKSPACE"] = sim_sandbox_workspace

            pkg_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            env["PYTHONPATH"] = f"{os.path.join(pkg_dir, 'src')}:{pkg_dir}"

            script = (
                "import os\n"
                "from sandbox_executor.agent_runner import cleanup_repo_dir, get_workspace_dir\n"
                "ws = get_workspace_dir()\n"
                "expected = os.environ['SIM_EXPECTED_WORKSPACE']\n"
                "assert ws == expected, f'workspace resolution drifted: {ws!r} != {expected!r}'\n"
                "cleanup_repo_dir(ws, raise_on_error=True)\n"
            )
            res = subprocess.run(
                [sys.executable, "-c", script],
                cwd=pkg_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
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


if __name__ == "__main__":
    unittest.main()
