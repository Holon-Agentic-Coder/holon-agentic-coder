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
            test_modules = [
                "tests.test_intent_creator",
                "tests.test_planner",
                "tests.test_executor",
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


if __name__ == "__main__":
    unittest.main()
