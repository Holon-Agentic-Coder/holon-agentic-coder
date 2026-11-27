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

CHILD_RECURSION_SENTINEL_VAR = "HOLON_STRESS_CHILD"
# why: the timeout is long because a child run executes the full non-integration suite
CHILD_TIMEOUT_SECONDS = 1800


# why: the workspace-deletion incident only shows when the suite runs from the live executor workspace path
# why: kept out of the unit matrix by marker, because it clones the repository and re-runs the suite
@pytest.mark.stress
class TestSuiteWorkspaceSurvival(unittest.TestCase):
    def setUp(self):
        if os.environ.get(CHILD_RECURSION_SENTINEL_VAR) == "1" or os.environ.get(CHILD_SUITE_SENTINEL) == "1":
            self.skipTest("running inside the stress suite's own child run")

        # why: uv and git are hard requirements; their absence is a skip here that the CI job post-run assertion fails
        if not uv_available():
            self.skipTest(f"{UV} is not available; this suite runs only under '{UV} run pytest'.")

        if not shutil.which("git"):
            self.skipTest("git is not available; the reproduction needs a clone.")

    def test_suite_run_from_live_executor_workspace_preserves_workspace_and_history(self):
        # why: its own HOME, workspace, TMPDIR and --basetemp let it run concurrently with the hermetic canary guard
        with child_context("survival") as context:
            # why: ALLOWED_PARENTS deliberately covers the clone under this HOME, as it did in the original deletion
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
                env=child_env(context, CHILD_RECURSION_SENTINEL_VAR),
                timeout=CHILD_TIMEOUT_SECONDS,
            ).completed

            # why: survival is asserted before the return code, because the disappearance is the incident
            self.assertEqual(
                survival_violations(workspace, canary, canary_content, head_sha),
                [],
                f"Command: {' '.join(cmd)}\nStdout:\n{res.stdout}\nStderr:\n{res.stderr}",
            )
            self.assertEqual(
                res.returncode,
                0,
                f"The suite did not pass when run from a live executor workspace "
                f"(selection: -m '{UNIT_MARKER_EXPRESSION}').\n"
                f"Command: {' '.join(cmd)}\nStdout:\n{res.stdout}\nStderr:\n{res.stderr}",
            )
