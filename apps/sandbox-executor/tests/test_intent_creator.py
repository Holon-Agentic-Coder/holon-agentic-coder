import json
import os
import tempfile
import unittest
import unittest.mock
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.entrypoint import intent_creator

from tests.leak_assertions import assert_no_fragment, make_gh_probe

REMOTE_GIT_VERBS = ("clone", "fetch", "pull", "push", "ls-remote")


def remote_git_cmds(cmds):
    return [c for c in cmds if any(f"git {verb}" in c for verb in REMOTE_GIT_VERBS)]


class TestIntentCreator(unittest.TestCase):
    @patch("subprocess.run")
    @patch("os.path.exists")
    @patch("os.makedirs")
    def test_intent_creator_main(self, mock_makedirs, mock_exists, mock_run):
        mock_exists.return_value = True

        intent_json_data = {
            "branch": "I-12345",
            "description": "Test description",
            "goal": "Test goal",
            "slug": "test-slug",
        }

        file_contents = {}

        def mock_open_impl(file, mode="r", *args, **kwargs):
            file_str = str(file)
            if "/tmp/intent.json" in file_str:
                mock_file = MagicMock()
                mock_file.read.return_value = json.dumps(intent_json_data)
                mock_file.__enter__.return_value = mock_file
                return mock_file
            else:
                mock_file = MagicMock()
                mock_file.write = MagicMock()
                mock_file.__enter__.return_value = mock_file
                # Keep the handle opened for writing: the ledger prefix guard re-opens the file in 'rb' mode.
                if "r" not in mode:
                    file_contents[file_str] = mock_file
                return mock_file

        mock_run_result = MagicMock()
        mock_run_result.returncode = 0
        mock_run.return_value = mock_run_result

        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": tmp_dir}),
                patch("sandbox_executor.entrypoint.intent_creator.get_workspace_dir", return_value=tmp_dir),
                patch("sandbox_executor.entrypoint.intent_creator.start_branch_from_host_fetch") as mock_start,
                patch("builtins.open", side_effect=mock_open_impl),
            ):
                intent_creator.main()

            mock_start.assert_called_once()

        # Check that subprocess.run was called for git operations
        self.assertTrue(mock_run.call_count >= 5)
        called_cmds = [" ".join(call[0][0]) for call in mock_run.call_args_list]
        self.assertEqual(remote_git_cmds(called_cmds), [])
        identity = "git -c user.email=intent-agent@holon-agentic-coder.com -c user.name=Holon Intent Agent commit -m "
        self.assertTrue(any(cmd.startswith(identity) for cmd in called_cmds))
        self.assertFalse(any(cmd.startswith("git config") for cmd in called_cmds))

        # Check that intents.jsonl was written to
        written_file_path = next(k for k in file_contents if "intents.jsonl" in k)
        written_mock = file_contents[written_file_path]
        written_mock.write.assert_called()
        written_data = json.loads(written_mock.write.call_args[0][0].strip())
        self.assertEqual(written_data["branch"], "I-12345")
        self.assertEqual(written_data["status"], "proposed")

    @patch("subprocess.run")
    @patch("os.path.exists")
    @patch("os.makedirs")
    def test_intent_creator_generate_branch_on_the_fly(self, mock_makedirs, mock_exists, mock_run):
        mock_exists.return_value = True

        intent_json_data = {"description": "Test description", "goal": "Test goal", "slug": "test-slug"}

        file_contents = {}

        def mock_open_impl(file, mode="r", *args, **kwargs):
            file_str = str(file)
            if "/tmp/intent.json" in file_str:
                mock_file = MagicMock()
                mock_file.read.return_value = json.dumps(intent_json_data)
                mock_file.__enter__.return_value = mock_file
                return mock_file
            else:
                mock_file = MagicMock()
                mock_file.write = MagicMock()
                mock_file.__enter__.return_value = mock_file
                # Keep the handle opened for writing: the ledger prefix guard re-opens the file in 'rb' mode.
                if "r" not in mode:
                    file_contents[file_str] = mock_file
                return mock_file

        mock_run_result = MagicMock()
        mock_run_result.returncode = 0
        mock_run.return_value = mock_run_result

        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": tmp_dir}),
                patch("sandbox_executor.entrypoint.intent_creator.get_workspace_dir", return_value=tmp_dir),
                patch("sandbox_executor.entrypoint.intent_creator.start_branch_from_host_fetch") as mock_start,
                patch("builtins.open", side_effect=mock_open_impl),
            ):
                intent_creator.main()

            mock_start.assert_called_once()

        # Check that intents.jsonl was written to
        written_file_path = next(k for k in file_contents if "intents.jsonl" in k)
        written_mock = file_contents[written_file_path]
        written_mock.write.assert_called()
        written_data = json.loads(written_mock.write.call_args[0][0].strip())
        self.assertTrue(written_data["branch"].startswith("I-"))
        self.assertTrue("test-slug" in written_data["branch"])
        self.assertEqual(written_data["status"], "proposed")

    @patch("subprocess.run")
    @patch("os.path.exists")
    @patch("os.makedirs")
    def test_intent_creator_with_target_branch(self, mock_makedirs, mock_exists, mock_run):
        mock_exists.return_value = True

        intent_json_data = {
            "branch": "I-12345",
            "description": "Test description",
            "goal": "Test goal",
            "slug": "test-slug",
            "target_branch": "develop",
        }

        file_contents = {}

        def mock_open_impl(file, mode="r", *args, **kwargs):
            file_str = str(file)
            if "/tmp/intent.json" in file_str:
                mock_file = MagicMock()
                mock_file.read.return_value = json.dumps(intent_json_data)
                mock_file.__enter__.return_value = mock_file
                return mock_file
            else:
                mock_file = MagicMock()
                mock_file.write = MagicMock()
                mock_file.__enter__.return_value = mock_file
                # Keep the handle opened for writing: the ledger prefix guard re-opens the file in 'rb' mode.
                if "r" not in mode:
                    file_contents[file_str] = mock_file
                return mock_file

        mock_run_result = MagicMock()
        mock_run_result.returncode = 0
        mock_run.return_value = mock_run_result

        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": tmp_dir}),
                patch("sandbox_executor.entrypoint.intent_creator.get_workspace_dir", return_value=tmp_dir),
                patch("sandbox_executor.entrypoint.intent_creator.start_branch_from_host_fetch") as mock_start,
                patch("builtins.open", side_effect=mock_open_impl),
            ):
                intent_creator.main()

            mock_start.assert_called_once()

        # The branch starts locally from the host-fetched develop; no remote verb is issued.
        self.assertEqual(mock_start.call_args[0][2:], ("develop", "I-12345/_"))
        called_cmds = [" ".join(call[0][0]) for call in mock_run.call_args_list]
        self.assertEqual(remote_git_cmds(called_cmds), [])

    @patch("subprocess.run")
    @patch("os.path.exists")
    @patch("os.makedirs")
    def test_intent_creator_sanitize_slug_spaces(self, mock_makedirs, mock_exists, mock_run):
        mock_exists.return_value = True

        intent_json_data = {
            "description": "Test description",
            "goal": "Test goal",
            "slug": "bootstrap holon cli intent",
        }

        file_contents = {}

        def mock_open_impl(file, mode="r", *args, **kwargs):
            file_str = str(file)
            if "/tmp/intent.json" in file_str:
                mock_file = MagicMock()
                mock_file.read.return_value = json.dumps(intent_json_data)
                mock_file.__enter__.return_value = mock_file
                return mock_file
            else:
                mock_file = MagicMock()
                mock_file.write = MagicMock()
                mock_file.__enter__.return_value = mock_file
                # Keep the handle opened for writing: the ledger prefix guard re-opens the file in 'rb' mode.
                if "r" not in mode:
                    file_contents[file_str] = mock_file
                return mock_file

        mock_run_result = MagicMock()
        mock_run_result.returncode = 0
        mock_run.return_value = mock_run_result

        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": tmp_dir}),
                patch("sandbox_executor.entrypoint.intent_creator.get_workspace_dir", return_value=tmp_dir),
                patch("sandbox_executor.entrypoint.intent_creator.start_branch_from_host_fetch") as mock_start,
                patch("builtins.open", side_effect=mock_open_impl),
            ):
                intent_creator.main()

            mock_start.assert_called_once()

        # Check that intents.jsonl was written to
        written_file_path = next(k for k in file_contents if "intents.jsonl" in k)
        written_mock = file_contents[written_file_path]
        written_mock.write.assert_called()
        written_data = json.loads(written_mock.write.call_args[0][0].strip())
        self.assertTrue(written_data["branch"].startswith("I-"))
        self.assertTrue("bootstrap-holon-cli-intent" in written_data["branch"])
        self.assertEqual(written_data["slug"], "bootstrap-holon-cli-intent")
        self.assertEqual(written_data["status"], "proposed")

    def test_unresolved_base_branch_exits_with_the_error(self):
        data = json.dumps({"branch": "I-1", "description": "d", "goal": "g"})
        err = RuntimeError(
            "branch 'main' does not resolve in the mounted workspace; the host fetches it before dispatch"
        )
        with (
            patch("os.path.exists", return_value=True),
            patch("builtins.open", unittest.mock.mock_open(read_data=data)),
            patch.object(intent_creator, "get_workspace_dir", return_value="/ws"),
            patch.object(intent_creator, "start_branch_from_host_fetch", side_effect=err),
            patch("subprocess.run") as mock_run,
            self.assertRaises(SystemExit) as cm,
        ):
            intent_creator.main()
        self.assertEqual(cm.exception.code, 1)
        mock_run.assert_not_called()

    @patch("os.path.exists")
    def test_intent_creator_fails_fast_on_unresolved_questions(self, mock_exists):
        mock_exists.return_value = True
        intent_json_data = {
            "branch": "I-12345",
            "description": "Test description",
            "goal": "Test goal",
            "open_questions": ["What database should we use?"],
        }

        mock_file = MagicMock()
        mock_file.read.return_value = json.dumps(intent_json_data)
        mock_file.__enter__.return_value = mock_file

        with (
            patch("builtins.open", return_value=mock_file),
            patch("sys.stderr.write") as mock_stderr,
            self.assertRaises(SystemExit) as cm,
        ):
            intent_creator.main()

        self.assertEqual(cm.exception.code, 1)
        mock_stderr.assert_called()

    @patch("subprocess.run")
    @patch("os.path.exists")
    @patch("os.makedirs")
    def test_intent_creator_succeeds_with_resolved_questions(self, mock_makedirs, mock_exists, mock_run):
        mock_exists.return_value = True
        intent_json_data = {
            "branch": "I-12345",
            "description": "Test description",
            "goal": "Test goal",
            "slug": "test-slug",
            "questions": ["What database should we use?"],
            "answers": {"What database should we use?": "Postgres"},
        }

        file_contents = {}

        def mock_open_impl(file, mode="r", *args, **kwargs):
            file_str = str(file)
            if "/tmp/intent.json" in file_str:
                mock_file = MagicMock()
                mock_file.read.return_value = json.dumps(intent_json_data)
                mock_file.__enter__.return_value = mock_file
                return mock_file
            else:
                mock_file = MagicMock()
                mock_file.write = MagicMock()
                mock_file.__enter__.return_value = mock_file
                # Keep the handle opened for writing: the ledger prefix guard re-opens the file in 'rb' mode.
                if "r" not in mode:
                    file_contents[file_str] = mock_file
                return mock_file

        mock_run_result = MagicMock()
        mock_run_result.returncode = 0
        mock_run.return_value = mock_run_result

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": tmp_dir}),
            patch("sandbox_executor.entrypoint.intent_creator.get_workspace_dir", return_value=tmp_dir),
            patch("sandbox_executor.entrypoint.intent_creator.start_branch_from_host_fetch"),
            patch("builtins.open", side_effect=mock_open_impl),
        ):
            intent_creator.main()

        # Check that intents.jsonl contains resolved questions and answers
        written_file_path = next(k for k in file_contents if "intents.jsonl" in k)
        written_mock = file_contents[written_file_path]
        written_mock.write.assert_called()
        written_data = json.loads(written_mock.write.call_args[0][0].strip())
        self.assertEqual(written_data["questions"], ["What database should we use?"])
        self.assertEqual(written_data["answers"], {"What database should we use?": "Postgres"})

        # Check commit message includes resolved questions summary
        commit_calls = [call[0][0] for call in mock_run.call_args_list if "commit" in call[0][0]]
        self.assertTrue(len(commit_calls) > 0)
        commit_msg = commit_calls[0][commit_calls[0].index("-m") + 1]
        self.assertIn("Resolved Questions", commit_msg)
        self.assertIn("Postgres", commit_msg)


# --------------------------------------------------------------------------------------
# Credential leaks in the echoed clone command and its failure diagnostics
# --------------------------------------------------------------------------------------


@pytest.fixture
def clone_leak_env(monkeypatch):
    sample = make_gh_probe()
    for name in ("GH_TOKEN", "HOLON_AGENT_KEY", "HOLON_MITM_WEB_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", sample)
    url = f"https://x-access-token:{sample}@github.com/myorg/myrepo.git"
    argv = ["git", "clone", "--branch", "I-1792762862-x/_", "--single-branch", "--depth", "1", url, "."]
    failed = MagicMock(
        returncode=128,
        stdout=f"Cloning into '.' from {url}",
        stderr=f"fatal: unable to access '{url}/': The requested URL returned error: 403",
    )
    return sample, argv, failed


def test_run_cmd_clone_echo_and_failure_carry_no_token_fragment(clone_leak_env, capsys):
    sample, argv, failed = clone_leak_env
    with patch("subprocess.run", return_value=failed), pytest.raises(SystemExit):
        intent_creator.run_cmd(argv, check=True)
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert_no_fragment(output, sample)
    assert "Running: git clone --branch I-1792762862-x/_ --single-branch" in output
    assert "github.com/myorg/myrepo.git" in output
    assert "returned error: 403" in output
