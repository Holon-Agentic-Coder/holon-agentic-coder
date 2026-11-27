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

_PREPARE_WRITE_NOTE_PLANT_HOOK_THEN_OOM_KILL_SNIPPET = (
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
    # why: the checkout may belong to the image's uid for the duration of the test
    res = subprocess.run(["git", "-c", "safe.directory=*", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return res.stdout.strip()


def _chown_through_root_container(root: Path, owner: str, *names: str) -> None:
    # why: the host user cannot chown, so a root helper container does it
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

        # why: Docker can bind-mount the system temp root on every platform
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
        # why: the image's uid gets the checkout only after the mounts are built, so preflight judges the host user
        with (
            patch.object(git_flow, "SANDBOX_CONTAINER_UID", os.getuid()),
            patch.object(git_flow, "SANDBOX_CONTAINER_GID", os.getgid()),
        ):
            mounts = sandbox_worktree_mounts(str(worktree), SANDBOX_WORKSPACE_MOUNT)
        if hand_over:
            self.hand_checkout_to_image_uid_until_cleanup(root)
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
                _PREPARE_WRITE_NOTE_PLANT_HOOK_THEN_OOM_KILL_SNIPPET,
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
        self.assert_container_note_visible_on_host_after_crash(main, worktree, head)
        self.assert_mounts_are_host_paths_not_named_volumes(mounts)

    def hand_checkout_to_image_uid_until_cleanup(self, root: Path) -> None:
        # why: a Linux bind mount keeps host ownership and the image must run as uid 1000 (/home/holon is private)
        # why: the host user cannot remove the temporary directory while uid 1000 owns the checkout
        _chown_through_root_container(root, IMAGE_UID, "repo", "flow-demo")
        self.addCleanup(_chown_through_root_container, root, f"{os.getuid()}:{os.getgid()}", "repo", "flow-demo")

    def assert_container_note_visible_on_host_after_crash(self, main: Path, worktree: Path, head: str) -> None:
        self.assertEqual((worktree / "crash-note.txt").read_text(encoding="utf-8"), "written in the container")
        self.assertIn("?? crash-note.txt", _git(worktree, "status", "--porcelain"))
        self.assertEqual(_git(worktree, "rev-parse", "HEAD"), head)
        self.assertFalse((main / ".git" / "hooks" / "post-checkout").exists())

    def assert_mounts_are_host_paths_not_named_volumes(self, mounts: list[str]) -> None:
        sources = [mounts[i + 1].split(":", 1)[0] for i in range(0, len(mounts), 2)]
        self.assertTrue(all(os.path.isabs(source) for source in sources), mounts)


if __name__ == "__main__":
    unittest.main()
