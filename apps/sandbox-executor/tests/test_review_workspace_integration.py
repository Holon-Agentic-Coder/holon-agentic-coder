"""Integration test for multi-pass review workspace reuse across real container boundaries.

Proves, against the real image and a seeded-remote volume (hermetic_testing.md Rule 2: ``--network
none``, no external network, no host ``~/.ssh``), that two successive review-loop preparation passes
sharing one workspace volume behave as designed: the first pass clones, the second fetches the
inter-run commit incrementally into the same checkout, and neither an untracked file nor an
uncommitted ledger row crosses the pass boundary. Only the preparation phase is driven, so no model
or credential is needed. Skips gracefully wherever a Docker daemon is unavailable.
"""

import shutil
import subprocess
import unittest
import uuid

import pytest

from sandbox_executor.agent_runner import SANDBOX_WORKSPACE_MOUNT, SANDBOX_WORKSPACE_UID_GID
from tests.hermetic_fixtures import (
    DEFAULT_FIXTURE_IMAGE,
    create_seeded_remote,
    remote_path,
    remove_remote_volume,
)

#: Drives exactly the entrypoint's preparation phase (resolve, clone or fetch, verify HEAD) and prints
#: the resolved head SHA; the review itself never runs, so the pass needs no model.
_PREPARE_SNIPPET = (
    "import os\n"
    "from sandbox_executor.entrypoint.review_loop import _prepare_workspace\n"
    "target = _prepare_workspace(os.environ['HOLON_TARGET_WORKTREE'], 'main')\n"
    "print(target.head_sha)\n"
)


def _is_docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        res = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
        return res.returncode == 0
    except Exception:
        return False


@pytest.mark.integration_test
class TestPersistentReviewWorkspaceIntegration(unittest.TestCase):
    """Two real preparation passes over one workspace volume: clone once, then sync incrementally."""

    def _run(self, cmd: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        self.assertEqual(
            result.returncode,
            0,
            f"command {cmd} failed with code {result.returncode}.\n"
            f"Stdout:\n{result.stdout}\nStderr:\n{result.stderr}",
        )
        return result

    def _helper(self, mounts: list[str], script: str, user: str | None = None) -> subprocess.CompletedProcess:
        """Run a bash script in a throwaway helper container over the given volume mounts."""
        cmd = ["docker", "run", "--rm", "--network", "none"]
        if user:
            cmd.extend(["--user", user])
        cmd.extend([*mounts, "--entrypoint", "bash", DEFAULT_FIXTURE_IMAGE, "-c", script])
        return self._run(cmd)

    def test_second_pass_fetches_incrementally_and_leaks_no_state(self):
        if not _is_docker_available():
            self.skipTest("Docker daemon is not available in test environment.")

        remote_volume = create_seeded_remote()
        self.addCleanup(remove_remote_volume, remote_volume)
        remote = remote_path(remote_volume)
        remote_mount = ["-v", f"{remote_volume}:{remote}"]

        # The session workspace volume, initialised exactly as flow.ReviewWorkspaceSession does it:
        # created, then chowned to the image's unprivileged user by a one-shot root container.
        ws_volume = f"holon-it-ws-{uuid.uuid4().hex[:16]}"
        self._run(["docker", "volume", "create", "--label", "holon.review-session=1", ws_volume], timeout=60)
        self.addCleanup(remove_remote_volume, ws_volume)
        ws_mount = ["-v", f"{ws_volume}:{SANDBOX_WORKSPACE_MOUNT}"]
        self._run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "root",
                *ws_mount,
                "--entrypoint",
                "chown",
                DEFAULT_FIXTURE_IMAGE,
                "-R",
                SANDBOX_WORKSPACE_UID_GID,
                SANDBOX_WORKSPACE_MOUNT,
            ]
        )

        def prepare() -> str:
            result = self._run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "-e",
                    f"HOLON_REPO_URL={remote}",
                    "-e",
                    f"HOLON_TARGET_WORKTREE={SANDBOX_WORKSPACE_MOUNT}",
                    *remote_mount,
                    *ws_mount,
                    "--entrypoint",
                    "python3",
                    DEFAULT_FIXTURE_IMAGE,
                    "-c",
                    _PREPARE_SNIPPET,
                ]
            )
            return (result.stdout or "").strip().splitlines()[-1]

        first_sha = prepare()
        self.assertTrue(first_sha)

        # Pollute the workspace as a previous pass could, and mark .git to detect a re-clone.
        self._helper(
            ws_mount,
            f"set -eu\n"
            f"touch {SANDBOX_WORKSPACE_MOUNT}/.git/holon-reuse-marker\n"
            f"echo junk > {SANDBOX_WORKSPACE_MOUNT}/junk.txt\n"
            f'echo \'{{"leak": 1}}\' >> {SANDBOX_WORKSPACE_MOUNT}/holon-knowledge/ledger/intents.jsonl\n',
        )

        # A resolver-style push lands on the seeded remote between the two passes.
        push = self._helper(
            remote_mount,
            f"set -eu\n"
            f"git clone -q {remote} /tmp/holon-resolver\n"
            f"cd /tmp/holon-resolver\n"
            f"git config user.email resolver@holon.com\n"
            f'git config user.name "Holon Resolver"\n'
            f"echo fix > fix.txt\n"
            f"git add -A\n"
            f"git commit -qm 'resolver fix'\n"
            f"git push -q origin main\n"
            f"git rev-parse HEAD\n",
        )
        pushed_sha = (push.stdout or "").strip().splitlines()[-1]
        self.assertNotEqual(pushed_sha, first_sha)

        second_sha = prepare()
        self.assertEqual(second_sha, pushed_sha)

        # Inspect the reused workspace: the .git marker survived (no re-clone), HEAD is the pushed
        # commit (incremental fetch), and both planted leaks are gone (hermetic boundary).
        inspect = self._helper(
            ws_mount,
            f"set -eu\n"
            f"test -f {SANDBOX_WORKSPACE_MOUNT}/.git/holon-reuse-marker\n"
            f"test ! -e {SANDBOX_WORKSPACE_MOUNT}/junk.txt\n"
            f"git -C {SANDBOX_WORKSPACE_MOUNT} rev-parse HEAD\n"
            f"grep -c leak {SANDBOX_WORKSPACE_MOUNT}/holon-knowledge/ledger/intents.jsonl || true\n",
        )
        lines = (inspect.stdout or "").strip().splitlines()
        self.assertEqual(lines[0], pushed_sha)
        self.assertEqual(lines[-1], "0", "an uncommitted ledger row leaked across the pass boundary")


if __name__ == "__main__":
    unittest.main()
