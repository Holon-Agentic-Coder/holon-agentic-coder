import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from sandbox_executor.entrypoint import intent_creator


class TestIntentCreator(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.env_patcher = patch.dict(os.environ, {"HOLON_REPO_URL": "git@github.com:example/repo.git"})
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()
        super().tearDown()

    @patch("sandbox_executor.entrypoint.intent_creator.cleanup_repo_dir")
    @patch("subprocess.run")
    @patch("os.path.exists")
    @patch("os.makedirs")
    def test_intent_creator_main(self, mock_makedirs, mock_exists, mock_run, mock_cleanup):
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
                file_contents[file_str] = mock_file
                return mock_file

        mock_run_result = MagicMock()
        mock_run_result.returncode = 0
        mock_run.return_value = mock_run_result

        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir}),
                patch("sandbox_executor.entrypoint.intent_creator.get_workspace_dir", return_value=tmp_dir),
                patch("builtins.open", side_effect=mock_open_impl),
            ):
                intent_creator.main()

            mock_cleanup.assert_called_once_with(tmp_dir, raise_on_error=True)

        # Check that subprocess.run was called for git operations
        self.assertTrue(mock_run.call_count >= 5)
        called_cmds = [" ".join(call[0][0]) for call in mock_run.call_args_list]
        self.assertTrue(any("git clone" in cmd for cmd in called_cmds))
        self.assertTrue(any("git checkout" in cmd for cmd in called_cmds))
        self.assertTrue(any("git commit" in cmd for cmd in called_cmds))

        # Check that intents.jsonl was written to
        written_file_path = next(k for k in file_contents if "intents.jsonl" in k)
        written_mock = file_contents[written_file_path]
        written_mock.write.assert_called()
        written_data = json.loads(written_mock.write.call_args[0][0].strip())
        self.assertEqual(written_data["branch"], "I-12345")
        self.assertEqual(written_data["status"], "proposed")

    @patch("sandbox_executor.entrypoint.intent_creator.cleanup_repo_dir")
    @patch("subprocess.run")
    @patch("os.path.exists")
    @patch("os.makedirs")
    def test_intent_creator_generate_branch_on_the_fly(self, mock_makedirs, mock_exists, mock_run, mock_cleanup):
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
                file_contents[file_str] = mock_file
                return mock_file

        mock_run_result = MagicMock()
        mock_run_result.returncode = 0
        mock_run.return_value = mock_run_result

        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir}),
                patch("sandbox_executor.entrypoint.intent_creator.get_workspace_dir", return_value=tmp_dir),
                patch("builtins.open", side_effect=mock_open_impl),
            ):
                intent_creator.main()

            mock_cleanup.assert_called_once_with(tmp_dir, raise_on_error=True)

        # Check that intents.jsonl was written to
        written_file_path = next(k for k in file_contents if "intents.jsonl" in k)
        written_mock = file_contents[written_file_path]
        written_mock.write.assert_called()
        written_data = json.loads(written_mock.write.call_args[0][0].strip())
        self.assertTrue(written_data["branch"].startswith("I-"))
        self.assertTrue("test-slug" in written_data["branch"])
        self.assertEqual(written_data["status"], "proposed")

    @patch("sandbox_executor.entrypoint.intent_creator.cleanup_repo_dir")
    @patch("subprocess.run")
    @patch("os.path.exists")
    @patch("os.makedirs")
    def test_intent_creator_with_target_branch(self, mock_makedirs, mock_exists, mock_run, mock_cleanup):
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
                file_contents[file_str] = mock_file
                return mock_file

        mock_run_result = MagicMock()
        mock_run_result.returncode = 0
        mock_run.return_value = mock_run_result

        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir}),
                patch("sandbox_executor.entrypoint.intent_creator.get_workspace_dir", return_value=tmp_dir),
                patch("builtins.open", side_effect=mock_open_impl),
            ):
                intent_creator.main()

            mock_cleanup.assert_called_once_with(tmp_dir, raise_on_error=True)

        # Check that subprocess.run was called for git clone/checkout with 'develop'
        self.assertTrue(mock_run.call_count >= 5)
        called_cmds = [" ".join(call[0][0]) for call in mock_run.call_args_list]
        self.assertTrue(any("git clone --branch develop" in cmd for cmd in called_cmds))
        self.assertTrue(any("git checkout -B I-12345/_ origin/develop" in cmd for cmd in called_cmds))

    @patch("sandbox_executor.entrypoint.intent_creator.cleanup_repo_dir")
    @patch("subprocess.run")
    @patch("os.path.exists")
    @patch("os.makedirs")
    def test_intent_creator_sanitize_slug_spaces(self, mock_makedirs, mock_exists, mock_run, mock_cleanup):
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
                file_contents[file_str] = mock_file
                return mock_file

        mock_run_result = MagicMock()
        mock_run_result.returncode = 0
        mock_run.return_value = mock_run_result

        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir}),
                patch("sandbox_executor.entrypoint.intent_creator.get_workspace_dir", return_value=tmp_dir),
                patch("builtins.open", side_effect=mock_open_impl),
            ):
                intent_creator.main()

            mock_cleanup.assert_called_once_with(tmp_dir, raise_on_error=True)

        # Check that intents.jsonl was written to
        written_file_path = next(k for k in file_contents if "intents.jsonl" in k)
        written_mock = file_contents[written_file_path]
        written_mock.write.assert_called()
        written_data = json.loads(written_mock.write.call_args[0][0].strip())
        self.assertTrue(written_data["branch"].startswith("I-"))
        self.assertTrue("bootstrap-holon-cli-intent" in written_data["branch"])
        self.assertEqual(written_data["slug"], "bootstrap-holon-cli-intent")
        self.assertEqual(written_data["status"], "proposed")

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

    @patch("sandbox_executor.entrypoint.intent_creator.cleanup_repo_dir")
    @patch("subprocess.run")
    @patch("os.path.exists")
    @patch("os.makedirs")
    def test_intent_creator_succeeds_with_resolved_questions(self, mock_makedirs, mock_exists, mock_run, mock_cleanup):
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
                file_contents[file_str] = mock_file
                return mock_file

        mock_run_result = MagicMock()
        mock_run_result.returncode = 0
        mock_run.return_value = mock_run_result

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir}),
            patch("sandbox_executor.entrypoint.intent_creator.get_workspace_dir", return_value=tmp_dir),
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
