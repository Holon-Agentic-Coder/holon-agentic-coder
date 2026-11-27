import os
import pathlib
import subprocess
import tempfile
import unittest

import pytest

from tests.hermetic_fixtures import (
    CHILD_SUITE_SENTINEL,
    KNOWLEDGE_INDEX_SUBDIR,
    REPO_ROOT,
    TESTS_DIR,
    UV,
    describe_index_changes,
    knowledge_index_snapshot,
    simulated_container_env,
    uv_available,
)
from tests.stress_selection import canary_selection
from tests.stress_support import (
    canary_violations,
    child_context,
    child_env,
    child_suite_command,
    run_child,
    seed_canary_workspace,
)

CHILD_ENV_VAR = "HOLON_HERMETIC_GUARD_CHILD"

# why: generous ceiling because the child runs the canary selection plus the nested negative-control harness
CHILD_TIMEOUT_SECONDS = 1800


class TestSandboxHermeticGuard(unittest.TestCase):
    def setUp(self):
        if (
            os.environ.get(CHILD_ENV_VAR) == "1"
            or os.environ.get("HOLON_STRESS_CHILD") == "1"
            or os.environ.get(CHILD_SUITE_SENTINEL) == "1"
        ):
            self.skipTest("running inside a child suite")

        if not uv_available():
            self.skipTest(f"{UV} is not available; this suite runs only under '{UV} run pytest'.")

        # why: pytest exits 5 on empty collection, which would mask a missing repo or test directory
        self.assertTrue(os.path.isdir(REPO_ROOT), f"repo root does not exist: {REPO_ROOT}")
        tests_path = os.path.join(REPO_ROOT, TESTS_DIR)
        self.assertTrue(os.path.isdir(tests_path), f"test directory does not exist: {tests_path}")
        self.assertTrue(
            any(name.startswith("test_") for name in os.listdir(tests_path)),
            f"no test modules found under {tests_path}",
        )

    @pytest.mark.stress
    def test_canary_workspace_survives_entrypoint_tests(self):
        with child_context("canary") as context:
            seed = seed_canary_workspace(context)
            index_before = knowledge_index_snapshot(REPO_ROOT)
            cmd = child_suite_command(context, targets=canary_selection(REPO_ROOT, TESTS_DIR))
            res = run_child(
                cmd,
                label=context.label,
                cwd=REPO_ROOT,
                env=child_env(context, CHILD_ENV_VAR),
                timeout=CHILD_TIMEOUT_SECONDS,
            ).completed
            self.assertEqual(
                res.returncode,
                0,
                f"The suite failed under simulated sandbox environment!\n"
                f"Command: {' '.join(cmd)}\n"
                f"Stdout:\n{res.stdout}\nStderr:\n{res.stderr}",
            )
            # why: knowledge index, workspace, canary marker, dummy repo file, and dummy .git must all stay untouched
            self.assertEqual(canary_violations(context, seed, REPO_ROOT, index_before), [])

    def test_negative_control_canary_is_actually_live(self):
        with tempfile.TemporaryDirectory() as sim_home:
            sim_sandbox_workspace = os.path.join(sim_home, ".holon-sandbox", "workspace")
            os.makedirs(sim_sandbox_workspace, exist_ok=True)
            canary_marker = os.path.join(sim_sandbox_workspace, "canary.txt")
            with open(canary_marker, "w") as f:
                f.write("canary-should-not-survive")

            env = simulated_container_env(sim_home, CHILD_ENV_VAR)
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


class TestKnowledgeIndexSnapshotHelpers:
    def test_absent_directory_returns_empty_snapshot(self, tmp_path: pathlib.Path) -> None:
        snap = knowledge_index_snapshot(str(tmp_path))
        assert snap == {}

    def test_created_file_is_reported(self, tmp_path: pathlib.Path) -> None:
        before = knowledge_index_snapshot(str(tmp_path))
        index_dir = tmp_path / KNOWLEDGE_INDEX_SUBDIR
        index_dir.mkdir(parents=True)
        db = index_dir / "action_index.db"
        db.write_bytes(b"fake-db")
        after = knowledge_index_snapshot(str(tmp_path))
        changes = describe_index_changes(before, after)
        assert len(changes) == 1
        assert "created" in changes[0]
        assert "holon-knowledge/.index/action_index.db" in changes[0]

    def test_modified_file_is_reported(self, tmp_path: pathlib.Path) -> None:
        index_dir = tmp_path / KNOWLEDGE_INDEX_SUBDIR
        index_dir.mkdir(parents=True)
        db = index_dir / "action_index.db"
        db.write_bytes(b"v1")
        before = knowledge_index_snapshot(str(tmp_path))
        db.write_bytes(b"v2-longer")
        after = knowledge_index_snapshot(str(tmp_path))
        changes = describe_index_changes(before, after)
        assert len(changes) == 1
        assert "modified" in changes[0]
        assert "holon-knowledge/.index/action_index.db" in changes[0]

    def test_deleted_file_is_reported(self, tmp_path: pathlib.Path) -> None:
        index_dir = tmp_path / KNOWLEDGE_INDEX_SUBDIR
        index_dir.mkdir(parents=True)
        db = index_dir / "action_index.db"
        db.write_bytes(b"data")
        before = knowledge_index_snapshot(str(tmp_path))
        db.unlink()
        after = knowledge_index_snapshot(str(tmp_path))
        changes = describe_index_changes(before, after)
        assert len(changes) == 1
        assert "deleted" in changes[0]
        assert "holon-knowledge/.index/action_index.db" in changes[0]

    def test_unchanged_snapshot_reports_no_changes(self, tmp_path: pathlib.Path) -> None:
        index_dir = tmp_path / KNOWLEDGE_INDEX_SUBDIR
        index_dir.mkdir(parents=True)
        (index_dir / "action_index.db").write_bytes(b"data")
        before = knowledge_index_snapshot(str(tmp_path))
        after = knowledge_index_snapshot(str(tmp_path))
        assert describe_index_changes(before, after) == []

    def test_unreadable_directory_propagates_error(self, tmp_path: pathlib.Path) -> None:
        # why: os.walk defaults to onerror=None and swallows errors, so the snapshot helper must fail closed
        if os.geteuid() == 0:
            pytest.skip("running as root: permission bits are ignored, the directory stays readable")
        index_dir = tmp_path / KNOWLEDGE_INDEX_SUBDIR
        index_dir.mkdir(parents=True)
        (index_dir / "action_index.db").write_bytes(b"data")
        os.chmod(index_dir, 0o000)
        try:
            with pytest.raises(PermissionError):
                knowledge_index_snapshot(str(tmp_path))
        finally:
            os.chmod(index_dir, 0o755)
