import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox_executor import git_flow
from sandbox_executor.agent_runner import SANDBOX_WORKSPACE_MOUNT
from sandbox_executor.git_flow import sandbox_worktree_mounts

from tests.hermetic_fixtures import DEFAULT_FIXTURE_IMAGE, IMAGE_UID

_PREPARE_THEN_CRASH_SNIPPET = (
    "import os, subprocess\n"
    "from sandbox_executor.entrypoint.review_loop import _prepare_workspace\n"
    "ws = os.environ['HOLON_TARGET_WORKTREE']\n"
    "target = _prepare_workspace(ws, 'feature')\n"
    "print('HEAD', target.head_sha, flush=True)\n"
    "status = subprocess.run(['git', 'status', '--porcelain'], cwd=ws, capture_output=True, text=True)\n"
    "print('STATUS', status.returncode, flush=True)\n"
    "with open(os.path.join(ws, 'crash-note.txt'), 'w') as f:\n"
    "    f.write('written in the container')\n"
    "common = subprocess.run(['git', 'rev-parse', '--git-common-dir'], cwd=ws, capture_output=True, text=True)\n"
    "hook = os.path.join(ws, common.stdout.strip(), 'hooks', 'post-checkout')\n"
    "try:\n"
    "    open(hook, 'w').close()\n"
    "    print('HOOK written', flush=True)\n"
    "except OSError:\n"
    "    print('HOOK refused', flush=True)\n"
    "os._exit(137)\n"
)


def _is_docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        res = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
        return res.returncode == 0
    except Exception:
        return False


def _git(cwd: Path, *args: str) -> str:
    # why: checkout may belong to the image uid during the test (see _chown), so safe.directory=* is required
    res = subprocess.run(["git", "-c", "safe.directory=*", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return res.stdout.strip()


def _chown(root: Path, owner: str, *names: str) -> None:
    # why: the host user cannot chown to another uid, so ownership is changed via a root helper container
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0",
            "-v",
            f"{root}:/fixture",
            "--entrypoint",
            "chown",
            DEFAULT_FIXTURE_IMAGE,
            "-R",
            owner,
            *(f"/fixture/{name}" for name in names),
        ],
        capture_output=True,
        timeout=120,
        check=True,
    )


@pytest.mark.integration_test
class TestHostWorktreeBindMountIntegration(unittest.TestCase):
    def test_container_works_in_place_and_its_edits_survive_a_crash(self):
        if not _is_docker_available():
            self.skipTest("Docker daemon is not available in test environment.")

        # why: Docker can bind-mount paths under the system temp root on every platform
        root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="holon-it-worktree-")))
        main = root / "repo"
        main.mkdir()
        _git(main, "init", "-q", "-b", "main")
        _git(main, "config", "user.email", "test@holon.com")
        _git(main, "config", "user.name", "Holon Test")
        (main / "README.md").write_text("seed\n", encoding="utf-8")
        _git(main, "add", "-A")
        _git(main, "commit", "-qm", "seed")
        worktree = root / "flow-demo"
        _git(main, "worktree", "add", "-q", "-b", "feature", str(worktree))
        head = _git(worktree, "rev-parse", "HEAD")

        hand_over = sys.platform.startswith("linux") and f"{os.getuid()}:{os.getgid()}" != IMAGE_UID
        # why: host builds mounts before chown to image uid, so let write preflight check as the host user
        with (
            patch.object(git_flow, "SANDBOX_CONTAINER_UID", os.getuid()),
            patch.object(git_flow, "SANDBOX_CONTAINER_GID", os.getgid()),
        ):
            mounts = sandbox_worktree_mounts(str(worktree), SANDBOX_WORKSPACE_MOUNT)
        if hand_over:
            # why: Linux bind mounts keep host ownership and the image requires uid 1000; restore before cleanup
            _chown(root, IMAGE_UID, "repo", "flow-demo")
            self.addCleanup(_chown, root, f"{os.getuid()}:{os.getgid()}", "repo", "flow-demo")
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "-e",
                f"HOLON_TARGET_WORKTREE={SANDBOX_WORKSPACE_MOUNT}",
                *mounts,
                "--entrypoint",
                "python3",
                DEFAULT_FIXTURE_IMAGE,
                "-c",
                _PREPARE_THEN_CRASH_SNIPPET,
            ],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        output = f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

        self.assertEqual(result.returncode, 137, output)
        self.assertIn(f"HEAD {head}", result.stdout, output)
        self.assertIn("STATUS 0", result.stdout, output)
        self.assertIn("HOOK refused", result.stdout, output)
        self.assertEqual((worktree / "crash-note.txt").read_text(encoding="utf-8"), "written in the container")
        self.assertIn("?? crash-note.txt", _git(worktree, "status", "--porcelain"))
        self.assertEqual(_git(worktree, "rev-parse", "HEAD"), head)
        self.assertFalse((main / ".git" / "hooks" / "post-checkout").exists())
        sources = [mounts[i + 1].split(":", 1)[0] for i in range(0, len(mounts), 2)]
        self.assertTrue(all(os.path.isabs(source) for source in sources), mounts)


if __name__ == "__main__":
    unittest.main()
