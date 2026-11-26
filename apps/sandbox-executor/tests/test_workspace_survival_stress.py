"""Stress suite: prove the test suite survives a real run from inside a live executor workspace.

The workspace-deletion incident was not a unit test that failed. It was the *executor* losing its history:
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
``.github/workflows/test-stress.yml``) that also asserts the suite really executed instead of
skipping. It is excluded from the ordinary unit matrix by marker expression, because it clones the
repository and re-runs the whole suite and would otherwise nest.
"""

import os
import shutil
import subprocess
import unittest

import pytest

from tests.hermetic_fixtures import (
    CHILD_SUITE_SENTINEL,
    REPO_ROOT,
    UNIT_MARKER_EXPRESSION,
    UV,
    uv_available,
)
from tests.stress_support import (
    child_context,
    child_env,
    child_suite_command,
    git_head,
    run_child,
    survival_violations,
)

#: Sentinel set in the child environment so the child run cannot recurse into this stress suite.
CHILD_ENV_VAR = "HOLON_STRESS_CHILD"

#: Suite directory relative to the clone that becomes the workspace.
TESTS_SUBDIR = "apps/sandbox-executor/tests"

#: A child run runs a full non-integration suite.
CHILD_TIMEOUT_SECONDS = 1800


@pytest.mark.stress
class TestSuiteWorkspaceSurvival(unittest.TestCase):
    """Run the suite *from* a simulated executor workspace and require that workspace to live."""

    def setUp(self):
        """Skip inside a child run, so this suite can never recurse into itself.

        ``uv`` and ``git`` are hard requirements of the reproduction, so their absence is reported
        as a skip here and turned into a hard CI failure by the job's post-run assertion.
        """
        if os.environ.get(CHILD_ENV_VAR) == "1" or os.environ.get(CHILD_SUITE_SENTINEL) == "1":
            self.skipTest("running inside the stress suite's own child run")

        if not uv_available():
            self.skipTest(f"{UV} is not available; this suite runs only under '{UV} run pytest'.")

        if not shutil.which("git"):
            self.skipTest("git is not available; the reproduction needs a clone.")

    def test_suite_run_from_live_executor_workspace_preserves_workspace_and_history(self):
        """Running the suite from the executor workspace must not eat the workspace.

        Asserts the three things the incident destroyed and that ``executor.py`` needs afterwards:
        the directory, a valid ``.git``, and a resolvable ``HEAD`` commit.
        """
        # The context gives this test its own HOME, workspace, TMPDIR and pytest --basetemp, so it can
        # run concurrently with the hermetic canary guard (see tests/stress_support.py).
        with child_context("survival") as context:
            # ``~/.holon-sandbox`` is whitelisted in ALLOWED_PARENTS, so with HOME pointing at this
            # throwaway home the whitelist legitimately covers the clone. That is not a loophole to
            # patch out -- it is precisely the property that made the original deletion possible,
            # and therefore what makes this a faithful reproduction.
            workspace = context.workspace

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

            head_before = git_head(workspace)
            self.assertEqual(head_before.returncode, 0, f"Clone has no HEAD: {head_before.stderr}")
            head_sha = head_before.stdout.strip()

            canary = os.path.join(workspace, "canary-executor-workspace.txt")
            canary_content = "canary-must-survive-suite-run"
            with open(canary, "w") as f:
                f.write(canary_content)

            cmd = child_suite_command(context)
            res = run_child(
                cmd,
                label=context.label,
                cwd=workspace,
                env=child_env(context, CHILD_ENV_VAR),
                timeout=CHILD_TIMEOUT_SECONDS,
            ).completed

            # 1-3. the workspace, the canary inside it, its .git and its HEAD commit survived --
            #      checked first, because their disappearance is the incident, and a failed return
            #      code alone would not say so
            self.assertEqual(
                survival_violations(workspace, canary, canary_content, head_sha),
                [],
                f"Command: {' '.join(cmd)}\nStdout:\n{res.stdout}\nStderr:\n{res.stderr}",
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
