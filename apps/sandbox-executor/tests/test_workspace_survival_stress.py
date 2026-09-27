"""Stress suite: prove the test suite survives a real run from inside a live executor workspace.

The bean 0019 incident was not a unit test that failed. It was the *executor* losing its history:
inside the container the suite was run from ``/home/holon/.holon-sandbox/workspace``, an
un-hermetic test resolved that same path through ``get_workspace_dir()``, the cleanup whitelist
accepted it because ``~/.holon-sandbox`` is an ``ALLOWED_PARENTS`` member, and the workspace was
deleted underneath the running job. The next ``git rev-parse`` failed, ``executor.py`` took its
destructive re-initialisation fallback, and the pushed execution branch became an orphan root
commit holding nothing but ledger files.

A canary guard that runs the suite against a throwaway ``HOME`` cannot demonstrate that shape -- it
never runs *from* the workspace, so it cannot show that the workspace and its commit history
survive. This suite closes that gap: it clones the repository onto the exact workspace path, runs
the unit selection there, and requires the directory, its ``.git`` and its ``HEAD`` to be intact
afterwards.

It is marked ``stress`` and runs in a dedicated CI job (``Workspace survival`` in
``.github/workflows/test-unit.yml``) that also asserts the suite really executed instead of
skipping. It is excluded from the ordinary unit matrix by marker expression, because it clones the
repository and re-runs the whole suite and would otherwise nest.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

import pytest

from tests.hermetic_fixtures import (
    REPO_ROOT,
    UNIT_MARKER_EXPRESSION,
    UV,
    pytest_suite_command,
    simulated_container_env,
    uv_available,
)

#: Sentinel set in the child environment so the child run cannot recurse into this stress suite.
CHILD_ENV_VAR = "HOLON_STRESS_CHILD"

#: Suite directory relative to the clone that becomes the workspace.
TESTS_SUBDIR = "apps/sandbox-executor/tests"

#: A child run nests a full suite plus the canary guard's own child run.
CHILD_TIMEOUT_SECONDS = 1800


@pytest.mark.stress
class TestSuiteWorkspaceSurvival(unittest.TestCase):
    """Run the suite *from* a simulated executor workspace and require that workspace to live."""

    def setUp(self):
        """Skip inside a child run, so this suite can never recurse into itself.

        ``uv`` and ``git`` are hard requirements of the reproduction, so their absence is reported
        as a skip here and turned into a hard CI failure by the job's post-run assertion.
        """
        if os.environ.get(CHILD_ENV_VAR) == "1":
            self.skipTest("running inside the stress suite's own child run")

        if not uv_available():
            self.skipTest(f"{UV} is not available; this suite runs only under '{UV} run pytest'.")

        if not shutil.which("git"):
            self.skipTest("git is not available; the reproduction needs a clone.")

    def _git(self, *args: str, cwd: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60)

    def test_suite_run_from_live_executor_workspace_preserves_workspace_and_history(self):
        """Running the suite from the executor workspace must not eat the workspace.

        Asserts the three things the incident destroyed and that ``executor.py`` needs afterwards:
        the directory, a valid ``.git``, and a resolvable ``HEAD`` commit.
        """
        with tempfile.TemporaryDirectory() as sim_home:
            # ``~/.holon-sandbox`` is whitelisted in ALLOWED_PARENTS, so with HOME pointing at this
            # throwaway home the whitelist legitimately covers the clone. That is not a loophole to
            # patch out -- it is precisely the property that made the original deletion possible,
            # and therefore what makes this a faithful reproduction.
            workspace = os.path.join(sim_home, ".holon-sandbox", "workspace")
            os.makedirs(os.path.dirname(workspace), exist_ok=True)

            clone = subprocess.run(
                ["git", "clone", "--quiet", "--no-hardlinks", "--local", REPO_ROOT, workspace],
                capture_output=True,
                text=True,
                timeout=600,
            )
            self.assertEqual(
                clone.returncode,
                0,
                f"Failed to clone the repository into the simulated workspace:\n{clone.stderr}",
            )

            head_before = self._git("rev-parse", "HEAD", cwd=workspace)
            self.assertEqual(head_before.returncode, 0, f"Clone has no HEAD: {head_before.stderr}")
            head_sha = head_before.stdout.strip()

            canary = os.path.join(workspace, "canary-executor-workspace.txt")
            canary_content = "canary-must-survive-suite-run"
            with open(canary, "w") as f:
                f.write(canary_content)

            cmd = pytest_suite_command()
            res = subprocess.run(
                cmd,
                cwd=workspace,
                env=simulated_container_env(sim_home, CHILD_ENV_VAR),
                capture_output=True,
                text=True,
                timeout=CHILD_TIMEOUT_SECONDS,
            )

            # 1. the workspace itself survived -- checked first, because its disappearance is the
            #    incident, and a failed return code alone would not say so
            self.assertTrue(
                os.path.isdir(workspace),
                "The executor workspace was deleted by a suite run -- the bean 0019 incident is "
                "back, and a real execution would now wipe its own commit history.\n"
                f"Command: {' '.join(cmd)}\nStdout:\n{res.stdout}\nStderr:\n{res.stderr}",
            )
            # 2. the canary inside it survived, unmodified
            self.assertTrue(os.path.exists(canary), "A file inside the workspace was deleted.")
            with open(canary) as f:
                self.assertEqual(f.read(), canary_content, "A file inside the workspace changed.")
            # 3. the git metadata the executor commits and pushes from survived
            self.assertTrue(
                os.path.isdir(os.path.join(workspace, ".git")),
                "The workspace .git disappeared, which is what drives executor.py into its "
                "destructive git re-initialisation fallback.",
            )
            head_after = self._git("rev-parse", "HEAD", cwd=workspace)
            self.assertEqual(
                head_after.returncode,
                0,
                f"git can no longer resolve the workspace repository: {head_after.stderr}",
            )
            self.assertEqual(
                head_sha,
                head_after.stdout.strip(),
                "The workspace commit history changed during the suite run.",
            )
            # 4. and the suite genuinely passed from that location, under the selection the unit
            #    job uses (which excludes stress, so this run cannot nest again)
            self.assertEqual(
                res.returncode,
                0,
                f"The suite did not pass when run from a live executor workspace "
                f"(selection: -m '{UNIT_MARKER_EXPRESSION}').\n"
                f"Command: {' '.join(cmd)}\nStdout:\n{res.stdout}\nStderr:\n{res.stderr}",
            )
