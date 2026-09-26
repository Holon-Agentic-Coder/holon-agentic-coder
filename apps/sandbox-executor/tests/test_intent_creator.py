import json
import unittest
from unittest.mock import MagicMock, patch

from sandbox_executor.entrypoint import intent_creator


class TestIntentCreator(unittest.TestCase):
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

        with patch("builtins.open", side_effect=mock_open_impl):
            intent_creator.main()

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

        with patch("builtins.open", side_effect=mock_open_impl):
            intent_creator.main()

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

        with patch("builtins.open", side_effect=mock_open_impl):
            intent_creator.main()

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

        with patch("builtins.open", side_effect=mock_open_impl):
            intent_creator.main()

        # Check that intents.jsonl was written to
        written_file_path = next(k for k in file_contents if "intents.jsonl" in k)
        written_mock = file_contents[written_file_path]
        written_mock.write.assert_called()
        written_data = json.loads(written_mock.write.call_args[0][0].strip())
        self.assertTrue(written_data["branch"].startswith("I-"))
        self.assertTrue("bootstrap-holon-cli-intent" in written_data["branch"])
        self.assertEqual(written_data["slug"], "bootstrap-holon-cli-intent")
        self.assertEqual(written_data["status"], "proposed")


if __name__ == "__main__":
    unittest.main()
