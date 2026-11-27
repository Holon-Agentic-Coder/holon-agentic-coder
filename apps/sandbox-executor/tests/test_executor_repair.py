"""Tests for ``entrypoint/executor/repair.py``: probing and repairing the workspace git repository around the agent."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sandbox_executor.entrypoint import executor

from tests.executor_fixtures import (
    GitRemoteHarness,
    _agent_turns_use_run_cmd,  # noqa: F401  (autouse fixture)
    _cuts_branch,
    _fake_token,
    _remote_git_cmds,
    _validate_stub,
    _workspace_is_mounted,  # noqa: F401  (autouse fixture)
)


class TestExecutor(GitRemoteHarness, unittest.TestCase):
    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.validate_mounted_workspace", new=_validate_stub)
    def test_main_unusable_repo_after_the_agent_is_an_error_not_a_rebuild(self, mock_get_runner, mock_run_cmd):
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.0.0"
        mock_runner.build_cmd.return_value = ["agy", "--model", "gemini-3.5-flash", "prompt"]
        mock_get_runner.return_value = mock_runner

        def side_effect(args, cwd=None, **kwargs):
            mock_res = MagicMock()
            if _cuts_branch(args):
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(
                        json.dumps(
                            {
                                "plan_id": "P-123",
                                "intent_branch": "I-456/_",
                                "entropy": 2.0,
                                "entropy_budget": 5.0,
                            }
                        )
                        + "\n"
                    )
                mock_res.returncode = 0
                mock_res.stdout = "OK"
            elif args == ["git", "rev-parse", "--is-inside-work-tree"]:
                # Simulate corrupted git repository after agent execution
                mock_res.returncode = 1
                mock_res.stdout = ""
                mock_res.stderr = f"fatal: not a git repository: /work/{_fake_token('ghp_')}"
            else:
                mock_res.returncode = 0
                mock_res.stdout = "OK"
                mock_res.stderr = ""
            return mock_res

        mock_run_cmd.side_effect = side_effect

        with (
            tempfile.TemporaryDirectory(prefix="sandbox_executor_test_") as tmp_dir,
            patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": tmp_dir}),
            patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
        ):
            # A .git directory the repair cannot restore: the mounted worktree is the host's, so it is never rebuilt
            os.makedirs(os.path.join(tmp_dir, ".git"), exist_ok=True)

            with (
                patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]),
                self.assertRaises(RuntimeError) as raised,
            ):
                executor.main()

            self.assertIn(
                "the mounted worktree's git repository is unusable after the agent ran", str(raised.exception)
            )
            # The failure line printed for the operator redacts the git diagnostic.
            self.assertIn("Execution failed:", mock_stderr.getvalue())
            self.assertNotIn(_fake_token("ghp_"), mock_stderr.getvalue())
            # No backup directory, no re-initialisation and no remote verb
            self.assertEqual([d for d in os.listdir(tmp_dir) if d.startswith(".git-unusable-")], [])
            called_cmds = [call.args[0] for call in mock_run_cmd.call_args_list if call.args]
            self.assertNotIn(["git", "init"], called_cmds)
            self.assertEqual(_remote_git_cmds(called_cmds), [])

    @patch("sandbox_executor.entrypoint.executor.get_runner")
    def test_corrupted_git_after_the_agent_keeps_the_worktree_and_publishes_nothing(self, mock_get_runner):
        with tempfile.TemporaryDirectory(prefix="sandbox_test_") as base_dir:
            plan_branch = "I-456/P-123/_"
            bare_dir, _plan_tip = self._create_bare_remote_with_plan(base_dir, plan_branch)
            workspace_dir = self._clone_workspace(base_dir, bare_dir, plan_branch)
            origin_refs = subprocess.run(
                ["git", "for-each-ref"], cwd=bare_dir, capture_output=True, text=True, check=True
            ).stdout

            mock_runner = MagicMock()
            mock_runner.get_version.return_value = "1.0.0"
            agent_script = (
                "import os, shutil; "
                "os.makedirs('src', exist_ok=True); "
                "open('src/agent_edit.py', 'w').write('# agent edit'); "
                "shutil.rmtree('.git/objects')"
            )
            mock_runner.build_cmd.return_value = ["python3", "-c", agent_script]
            mock_runner.validate.return_value = None
            mock_get_runner.return_value = mock_runner

            with (
                patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": workspace_dir}),
                patch("sys.argv", ["executor.py", plan_branch, "antigravity-agent", "gemini-3.5-flash"]),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(RuntimeError) as raised,
            ):
                executor.main()

            self.assertIn(
                "the mounted worktree's git repository is unusable after the agent ran", str(raised.exception)
            )
            # The host's worktree is never rebuilt: the agent's file stays and no backup appears.
            self.assertTrue(os.path.exists(os.path.join(workspace_dir, "src", "agent_edit.py")))
            self.assertEqual([d for d in os.listdir(workspace_dir) if d.startswith(".git-unusable-")], [])
            self.assertFalse(os.path.isdir(os.path.join(workspace_dir, ".git", "objects")))
            # The executor never touches the remote.
            self.assertEqual(
                subprocess.run(
                    ["git", "for-each-ref"], cwd=bare_dir, capture_output=True, text=True, check=True
                ).stdout,
                origin_refs,
            )

    def test_benign_dubious_ownership_is_repaired_without_rebuild(self):
        plan_branch = "I-456/P-123/_"
        with tempfile.TemporaryDirectory(prefix="sandbox_test_") as base_dir:
            bare_dir, _ = self._create_bare_remote_with_plan(base_dir, plan_branch)
            workspace_dir = self._clone_workspace(base_dir, bare_dir, plan_branch)

            dubious_err = f"fatal: detected dubious ownership in repository at '{workspace_dir}'"
            self.assertTrue(executor._repair_git_repo(workspace_dir, dubious_err, "main"))
            safe_dirs = executor._get_local_safe_directories(workspace_dir)
            self.assertIn(workspace_dir, safe_dirs)
            self.assertTrue(executor._probe_git_repo(workspace_dir)[0])
            self.assertEqual([d for d in os.listdir(workspace_dir) if d.startswith(".git-unusable-")], [])

    def test_safe_directory_probe_plumbing_and_realpath_normalization(self):
        plan_branch = "I-456/P-123/_"
        with tempfile.TemporaryDirectory(prefix="sandbox_test_") as base_dir:
            bare_dir, _ = self._create_bare_remote_with_plan(base_dir, plan_branch)
            workspace_dir = self._clone_workspace(base_dir, bare_dir, plan_branch)

            # Register safe.directory with redundant trailing slash or relative segments
            canonical_path = os.path.realpath(workspace_dir)
            redundant_path = os.path.join(canonical_path, "sub", "..")
            subprocess.run(
                ["git", "config", "--local", "--add", "safe.directory", redundant_path],
                cwd=workspace_dir,
                check=True,
            )

            # _get_local_safe_directories retrieves the entry via plumbing passing -c safe.directory
            with patch("subprocess.run", wraps=subprocess.run) as mock_subproc:
                entries = executor._get_local_safe_directories(workspace_dir)
                self.assertIn(redundant_path, entries)
                plumbing_cmd = mock_subproc.call_args[0][0]
                self.assertEqual(plumbing_cmd[:3], ["git", "-c", f"safe.directory={workspace_dir}"])

            # _probe_git_repo detects the normalized path matches
            healthy, _ = executor._probe_git_repo(workspace_dir)
            self.assertTrue(healthy)

    def test_safe_directory_fallback_on_unwritable_config(self):
        plan_branch = "I-456/P-123/_"
        with tempfile.TemporaryDirectory(prefix="sandbox_test_") as base_dir:
            bare_dir, _ = self._create_bare_remote_with_plan(base_dir, plan_branch)
            workspace_dir = self._clone_workspace(base_dir, bare_dir, plan_branch)
            canonical_path = os.path.realpath(workspace_dir)

            # Simulate unwritable .git/config by mocking run_cmd failure when executing config --add
            orig_run_cmd = executor.run_cmd

            def mock_run_cmd(args, **kwargs):
                if "config" in args and "--add" in args and "safe.directory" in args:
                    return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="permission denied")
                return orig_run_cmd(args, **kwargs)

            try:
                with patch("sandbox_executor.entrypoint.executor.run_cmd", side_effect=mock_run_cmd):
                    dubious_err = "fatal: detected dubious ownership in repository"
                    # Should not raise exception
                    executor._repair_git_repo(workspace_dir, dubious_err, "main")

                # Verify workspace_dir is tracked in fallback safe directories
                self.assertIn(canonical_path, executor._FALLBACK_SAFE_DIRECTORIES)

                # Verify run_cmd injects per-invocation -c safe.directory when dubious
                # ownership repair config write fails
                with patch("subprocess.run") as mock_subproc:
                    mock_subproc.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
                    executor.run_cmd(["git", "status"], cwd=workspace_dir, check=False)
                    called_args = mock_subproc.call_args[0][0]
                    self.assertEqual(called_args[0], "git")
                    self.assertEqual(called_args[1], "-c")
                    self.assertEqual(called_args[2], f"safe.directory={workspace_dir}")
                    self.assertEqual(called_args[3], "status")

                # Verify run_cmd injects -c safe.directory even when other -c flags are present
                with patch("subprocess.run") as mock_subproc:
                    mock_subproc.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
                    executor.run_cmd(["git", "-c", "core.quotePath=false", "status"], cwd=workspace_dir, check=False)
                    called_args = mock_subproc.call_args[0][0]
                    self.assertEqual(called_args[0], "git")
                    self.assertEqual(called_args[1], "-c")
                    self.assertEqual(called_args[2], f"safe.directory={workspace_dir}")
                    self.assertEqual(called_args[3:], ["-c", "core.quotePath=false", "status"])

                # Verify run_cmd does not duplicate safe.directory if already specified
                with patch("subprocess.run") as mock_subproc:
                    mock_subproc.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
                    executor.run_cmd(
                        ["git", "-c", f"safe.directory={workspace_dir}", "status"], cwd=workspace_dir, check=False
                    )
                    called_args = mock_subproc.call_args[0][0]
                    self.assertEqual(called_args, ["git", "-c", f"safe.directory={workspace_dir}", "status"])

                # Verify fallback skips _get_local_safe_directories subprocess call
                with (
                    patch("sandbox_executor.entrypoint.executor._get_local_safe_directories") as mock_local_safe,
                    patch("subprocess.run") as mock_subproc,
                ):
                    mock_subproc.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
                    executor.run_cmd(["git", "status"], cwd=workspace_dir, check=False)
                    mock_local_safe.assert_not_called()

                # Verify clean repo without safe directory config or fallback does NOT inject -c safe.directory
                clean_dir = os.path.join(base_dir, "clean_repo")
                os.makedirs(clean_dir, exist_ok=True)
                with patch("subprocess.run") as mock_subproc:
                    mock_subproc.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
                    executor.run_cmd(["git", "status"], cwd=clean_dir, check=False)
                    called_args = mock_subproc.call_args[0][0]
                    self.assertEqual(called_args, ["git", "status"])
            finally:
                executor._FALLBACK_SAFE_DIRECTORIES.discard(canonical_path)

    def test_lock_repair_removes_only_abandoned_locks(self):
        plan_branch = "I-456/P-123/_"
        with tempfile.TemporaryDirectory(prefix="sandbox_test_") as base_dir:
            bare_dir, _ = self._create_bare_remote_with_plan(base_dir, plan_branch)
            workspace_dir = self._clone_workspace(base_dir, bare_dir, plan_branch)
            git_dir = os.path.join(workspace_dir, ".git")

            abandoned = os.path.join(git_dir, "index.lock")
            with open(abandoned, "w") as lf:
                lf.write("abandoned by a crashed agent")
            stale_age = time.time() - 3600
            os.utime(abandoned, (stale_age, stale_age))
            # A young ref lock and a protected config lock may belong to live git processes.
            young = os.path.join(git_dir, "refs", "heads", "in-flight.lock")
            with open(young, "w") as lf:
                lf.write("held by a running git process")
            protected = os.path.join(git_dir, "config.lock")
            with open(protected, "w") as lf:
                lf.write("held by git config")
            os.utime(protected, (stale_age, stale_age))

            healthy, err = executor._probe_git_repo(workspace_dir)
            self.assertFalse(healthy)
            self.assertIn("index.lock", err)

            removed, still_held = executor._remove_abandoned_locks(workspace_dir)
            self.assertEqual([abandoned], removed)
            self.assertIn(young, still_held)
            self.assertFalse(os.path.exists(abandoned))
            self.assertTrue(os.path.exists(young))
            self.assertTrue(os.path.exists(protected))

            self.assertTrue(executor._repair_git_repo(workspace_dir, err, "main"))
            self.assertTrue(executor._probe_git_repo(workspace_dir)[0])
            self.assertEqual([d for d in os.listdir(workspace_dir) if d.startswith(".git-unusable-")], [])

    def test_missing_head_is_restored_on_the_execution_branch(self):
        plan_branch = "I-456/P-123/_"
        exec_branch = "I-456/P-123/E-1790000000-antigravity-agent-gemini-3.5-flash/_"
        with tempfile.TemporaryDirectory(prefix="sandbox_test_") as base_dir:
            bare_dir, plan_tip = self._create_bare_remote_with_plan(base_dir, plan_branch)
            workspace_dir = self._clone_workspace(base_dir, bare_dir, plan_branch)
            os.remove(os.path.join(workspace_dir, ".git", "HEAD"))

            self.assertFalse(executor._probe_git_repo(workspace_dir)[0])
            self.assertTrue(
                executor._repair_git_repo(
                    workspace_dir,
                    "dangling HEAD",
                    default_branch=exec_branch,
                    plan_branch=plan_branch,
                )
            )

            head_ref = subprocess.run(
                ["git", "symbolic-ref", "HEAD"],
                cwd=workspace_dir,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            self.assertEqual(
                head_ref,
                f"refs/heads/{exec_branch}",
                "HEAD was restored onto an arbitrary branch instead of the execution branch",
            )
            head_tip = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=workspace_dir,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            self.assertEqual(head_tip, plan_tip)
            self.assertEqual([d for d in os.listdir(workspace_dir) if d.startswith(".git-unusable-")], [])

    def _add_local_submodule(self, base_dir: str, workspace_dir: str, name: str = "holon-coherence") -> str:
        sub_src = os.path.join(base_dir, f"{name}_src")
        subprocess.run(["git", "init", "-q", "-b", "main", sub_src], check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=sub_src, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=sub_src, check=True)
        Path(sub_src, "README.md").write_text("submodule\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=sub_src, check=True)
        subprocess.run(["git", "commit", "-qm", "init submodule"], cwd=sub_src, check=True)
        subprocess.run(
            ["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q", sub_src, name],
            cwd=workspace_dir,
            check=True,
        )
        subprocess.run(["git", "commit", "-qm", f"add {name} submodule"], cwd=workspace_dir, check=True)
        rel_mod = subprocess.run(
            ["git", "rev-parse", "--git-path", f"modules/{name}"],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return rel_mod if os.path.isabs(rel_mod) else os.path.join(workspace_dir, rel_mod)

    def test_submodule_gitfile_container_path_drift_is_probed_and_repaired(self):
        plan_branch = "I-456/P-123/_"
        with tempfile.TemporaryDirectory(prefix="sandbox_test_") as base_dir:
            bare_dir, _ = self._create_bare_remote_with_plan(base_dir, plan_branch)
            workspace_dir = self._clone_workspace(base_dir, bare_dir, plan_branch)
            module_git_dir = self._add_local_submodule(base_dir, workspace_dir)

            gitfile = Path(workspace_dir, "holon-coherence", ".git")
            gitfile.write_text(
                "gitdir: /nonexistent-container/workspace/.git/modules/holon-coherence\n",
                encoding="utf-8",
            )
            subprocess.run(
                [
                    "git",
                    "config",
                    "--file",
                    os.path.join(module_git_dir, "config"),
                    "core.worktree",
                    "/nonexistent-container/workspace/holon-coherence",
                ],
                cwd=workspace_dir,
                check=True,
            )

            healthy, err = executor._probe_git_repo(workspace_dir)
            self.assertFalse(healthy)
            self.assertIn("holon-coherence/.git", err)

            self.assertTrue(executor._repair_git_repo(workspace_dir, err, "main"))
            rewritten = gitfile.read_text(encoding="utf-8").strip()
            self.assertTrue(rewritten.startswith("gitdir: "))
            self.assertFalse(os.path.isabs(rewritten.removeprefix("gitdir: ")))

            worktree = subprocess.run(
                ["git", "config", "--file", os.path.join(module_git_dir, "config"), "--get", "core.worktree"],
                cwd=workspace_dir,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            self.assertFalse(os.path.isabs(worktree))
            self.assertEqual(
                subprocess.run(
                    ["git", "rev-parse", "--is-inside-work-tree"],
                    cwd=os.path.join(workspace_dir, "holon-coherence"),
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip(),
                "true",
            )
            subprocess.run(["git", "checkout", "-q", "-b", "verify-repaired-checkout"], cwd=workspace_dir, check=True)

    def test_submodule_gitfile_repair_in_linked_worktree(self):
        plan_branch = "I-456/P-123/_"
        with tempfile.TemporaryDirectory(prefix="sandbox_test_") as base_dir:
            bare_dir, _ = self._create_bare_remote_with_plan(base_dir, plan_branch)
            workspace_dir = self._clone_workspace(base_dir, bare_dir, plan_branch)
            self._add_local_submodule(base_dir, workspace_dir)

            wt_dir = os.path.join(base_dir, "linked_wt")
            subprocess.run(["git", "worktree", "add", "-q", "-b", "linked", wt_dir], cwd=workspace_dir, check=True)
            subprocess.run(
                ["git", "-c", "protocol.file.allow=always", "submodule", "update", "--init", "-q"],
                cwd=wt_dir,
                check=True,
            )

            gitfile = Path(wt_dir, "holon-coherence", ".git")
            gitfile.write_text(
                "gitdir: /nonexistent-container/workspace/.git/modules/holon-coherence\n",
                encoding="utf-8",
            )

            healthy, err = executor._probe_git_repo(wt_dir)
            self.assertFalse(healthy)
            self.assertIn("holon-coherence/.git", err)

            self.assertTrue(executor._repair_git_repo(wt_dir, err, "main"))
            rewritten = gitfile.read_text(encoding="utf-8").strip()
            self.assertTrue(rewritten.startswith("gitdir: "))
            self.assertFalse(os.path.isabs(rewritten.removeprefix("gitdir: ")))
            self.assertEqual(
                subprocess.run(
                    ["git", "rev-parse", "--is-inside-work-tree"],
                    cwd=os.path.join(wt_dir, "holon-coherence"),
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip(),
                "true",
            )

    def test_unrepairable_submodule_gitfile_fails_closed(self):
        plan_branch = "I-456/P-123/_"
        with tempfile.TemporaryDirectory(prefix="sandbox_test_") as base_dir:
            bare_dir, _ = self._create_bare_remote_with_plan(base_dir, plan_branch)
            workspace_dir = self._clone_workspace(base_dir, bare_dir, plan_branch)
            module_git_dir = self._add_local_submodule(base_dir, workspace_dir)

            Path(workspace_dir, "holon-coherence", ".git").write_text(
                "gitdir: /nonexistent-container/workspace/.git/modules/holon-coherence\n",
                encoding="utf-8",
            )
            shutil.rmtree(module_git_dir)

            healthy, err = executor._probe_git_repo(workspace_dir)
            self.assertFalse(healthy)
            self.assertIn("holon-coherence/.git", err)

            with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
                self.assertFalse(executor._repair_git_repo(workspace_dir, err, "main"))
            self.assertIn("holon-coherence/.git", mock_stderr.getvalue())
