"""Integration test for the review-loop container working in place on a bind-mounted host worktree.

Proves, against the real image (hermetic_testing.md Rule 2: ``--network none``, no external network, no host
``~/.ssh``), that a linked host worktree mounted with :func:`git_flow.sandbox_worktree_mounts` is usable by git inside
the container without any clone or fetch, that what the container writes lands on the host immediately and survives
an abrupt exit, and that the repository's hooks cannot be written from inside the container. Only the preparation
phase is driven, so no model or credential is needed. Skips gracefully wherever a Docker daemon is unavailable.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest
from sandbox_executor.agent_runner import SANDBOX_WORKSPACE_MOUNT
from sandbox_executor.git_flow import sandbox_worktree_mounts

from tests.hermetic_fixtures import DEFAULT_FIXTURE_IMAGE, IMAGE_UID

#: Drives the entrypoint's preparation phase on the mounted worktree, writes an untracked file, tries to plant a
#: hook, then dies abruptly with the OOM-kill exit code before any cleanup could run.
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
    # The checkout may belong to the image's uid for the duration of the test (see _chown).
    res = subprocess.run(["git", "-c", "safe.directory=*", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return res.stdout.strip()


def _chown(root: Path, owner: str, *names: str) -> None:
    """Recursively hand root/<name> to owner through a root helper container (the host user cannot chown)."""
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
    """One real container pass over a bind-mounted linked worktree: no clone, host-visible, crash-safe."""

    def test_container_works_in_place_and_its_edits_survive_a_crash(self):
        if not _is_docker_available():
            self.skipTest("Docker daemon is not available in test environment.")

        # Under the system temp root, which Docker can bind-mount on every platform.
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

        mounts = sandbox_worktree_mounts(str(worktree), SANDBOX_WORKSPACE_MOUNT)
        if sys.platform.startswith("linux") and f"{os.getuid()}:{os.getgid()}" != IMAGE_UID:
            # A Linux bind mount keeps host ownership, and the image runs as uid 1000 (it cannot run as another uid:
            # /home/holon is private), so the review-loop container needs a worktree owned by uid 1000 -- as on a
            # host whose user is uid 1000. Hand it back before the temporary directory is removed.
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
        # Visible on the host at once, and still there after the abrupt exit.
        self.assertEqual((worktree / "crash-note.txt").read_text(encoding="utf-8"), "written in the container")
        self.assertIn("?? crash-note.txt", _git(worktree, "status", "--porcelain"))
        self.assertEqual(_git(worktree, "rev-parse", "HEAD"), head)
        self.assertFalse((main / ".git" / "hooks" / "post-checkout").exists())
        # No named volume was involved: the only mounts are host paths.
        sources = [mounts[i + 1].split(":", 1)[0] for i in range(0, len(mounts), 2)]
        self.assertTrue(all(os.path.isabs(source) for source in sources), mounts)


if __name__ == "__main__":
    unittest.main()
