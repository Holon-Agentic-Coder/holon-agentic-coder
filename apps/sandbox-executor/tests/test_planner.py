import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from sandbox_executor.entrypoint import planner


@patch.dict("os.environ", {"HOLON_AGENT_KEY": "dummy"})
class TestPlanner(unittest.TestCase):
    def test_get_file_structure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = pathlib.Path(tmp_dir)
            # Create some mock files and directories
            (tmp_path / "dir1").mkdir()
            (tmp_path / "dir1" / "file1.py").write_text("print('hello')")
            (tmp_path / "dir2").mkdir()
            (tmp_path / ".git").mkdir()  # should be ignored
            (tmp_path / "dir1" / ".hidden").write_text("hidden")  # should be ignored

            structure = planner.get_file_structure(str(tmp_path))
            self.assertIn("dir1/", structure)
            self.assertIn("file1.py", structure)
            self.assertNotIn(".git", structure)
            self.assertNotIn(".hidden", structure)

    def test_parse_metrics(self):
        content = """
# Plan

| metric              | value                 |
|---------------------|-----------------------|
| p_success_pred      | 0.85                  |
| entropy_pred        | 2.1                   |
| impact_pred         | 1.0                   |
| cost_pred           | 0.4                   |
| learning_value_pred | 3.5                   |
| ev_pred             | 4.2                   |
"""
        metrics = planner.parse_metrics(content)
        self.assertEqual(metrics.get("p_success"), 0.85)
        self.assertEqual(metrics.get("entropy"), 2.1)
        self.assertEqual(metrics.get("impact"), 1.0)
        self.assertEqual(metrics.get("cost"), 0.4)
        self.assertEqual(metrics.get("learning_value"), 3.5)

    def test_load_metrics_config(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Case 1: missing config file -> default values (lambda=0.3, mu=0.5)
            config = planner.load_metrics_config(tmp_dir)
            self.assertEqual(config["lambda"], 0.3)
            self.assertEqual(config["mu"], 0.5)

            # Case 2: valid ev_config.json present
            metrics_dir = pathlib.Path(tmp_dir) / "holon-config" / "metrics"
            metrics_dir.mkdir(parents=True)
            ev_file = metrics_dir / "ev_config.json"
            ev_file.write_text(json.dumps({"lambda": 0.4, "mu": 0.6}))

            config2 = planner.load_metrics_config(tmp_dir)
            self.assertEqual(config2["lambda"], 0.4)
            self.assertEqual(config2["mu"], 0.6)

            # Case 3: malformed JSON -> defaults returned, no exception raised
            ev_file.write_text("not valid json")
            config3 = planner.load_metrics_config(tmp_dir)
            self.assertEqual(config3["lambda"], 0.3)
            self.assertEqual(config3["mu"], 0.5)

            # Case 4: partial keys -> present key used, missing key falls back to default
            ev_file.write_text(json.dumps({"lambda": 0.2}))
            config4 = planner.load_metrics_config(tmp_dir)
            self.assertEqual(config4["lambda"], 0.2)
            self.assertEqual(config4["mu"], 0.5)

            # Case 5: non-numeric value -> falls back to default for that key
            ev_file.write_text(json.dumps({"lambda": "abc", "mu": 0.5}))
            config5 = planner.load_metrics_config(tmp_dir)
            self.assertEqual(config5["lambda"], 0.3)
            self.assertEqual(config5["mu"], 0.5)

            # Case 6: out-of-range bounds -> both fall back to defaults
            ev_file.write_text(json.dumps({"lambda": -1.0, "mu": 2.0}))
            config6 = planner.load_metrics_config(tmp_dir)
            self.assertEqual(config6["lambda"], 0.3)
            self.assertEqual(config6["mu"], 0.5)

    @patch("subprocess.run")
    @patch("os.path.exists")
    @patch("os.path.getsize")
    @patch("os.makedirs")
    @patch("sandbox_executor.entrypoint.planner.cleanup_repo_dir")
    def test_planner_main(self, mock_rmtree, mock_makedirs, mock_getsize, mock_exists, mock_run):
        test_args = ["planner.py", "I-12345/_", "pi-agent", "gemini"]

        intent_data = {
            "branch": "I-12345",
            "intent_id": "I-12345",
            "description": "Test description",
            "goal": "Test goal",
            "entropy_budget": "15.0",
        }

        file_contents = {}

        def mock_open_impl(file, mode="r", *args, **kwargs):
            file_str = str(file)
            if "intents.jsonl" in file_str:
                mock_file = MagicMock()
                mock_file.__iter__.return_value = [json.dumps(intent_data) + "\n"]
                mock_file.__enter__.return_value = mock_file
                return mock_file
            elif "planner.template.md" in file_str:
                mock_file = MagicMock()
                mock_file.read.return_value = "Template content {intent_json} {plan_id} {safe_model}"
                mock_file.__enter__.return_value = mock_file
                return mock_file
            elif "plans/P-" in file_str and "md" in file_str and "r" in mode:
                mock_file = MagicMock()
                plan_md_content = """# Plan

## Overall Plan Metrics

| metric | value |
| p_success_pred | 0.8 |
| entropy_pred | 2.5 |
| impact_pred | 1.0 |
| cost_pred | 0.5 |
| learning_value_pred | 0.5 |
| ev_pred | 0.6 |
"""
                mock_file.read.return_value = plan_md_content
                mock_file.__enter__.return_value = mock_file
                return mock_file
            else:
                mock_file = MagicMock()
                mock_file.write = MagicMock()
                mock_file.__enter__.return_value = mock_file
                file_contents[file_str] = mock_file
                return mock_file

        def mock_exists_impl(path):
            path_str = str(path)
            return "intents.jsonl" in path_str or "planner.template.md" in path_str or "plans/P-" in path_str

        mock_exists.side_effect = mock_exists_impl
        mock_getsize.return_value = 100

        mock_run_result = MagicMock()
        mock_run_result.returncode = 0
        mock_run_result.stdout = "Successful Agent Run Output"
        mock_run.return_value = mock_run_result

        # Directly modify and restore sys.argv
        old_argv = sys.argv
        sys.argv = test_args
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                with (
                    patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir}),
                    patch("sandbox_executor.entrypoint.planner.get_workspace_dir", return_value=tmp_dir),
                    patch("builtins.open", side_effect=mock_open_impl),
                ):
                    planner.main()
            finally:
                sys.argv = old_argv

            mock_rmtree.assert_called_once_with(tmp_dir, raise_on_error=True)

        self.assertGreaterEqual(mock_run.call_count, 5)
        called_cmds = [" ".join(call[0][0]) for call in mock_run.call_args_list]
        self.assertTrue(any("git clone" in cmd for cmd in called_cmds))
        self.assertTrue(any("git checkout -b" in cmd for cmd in called_cmds))
        self.assertTrue(any("pi -p" in cmd for cmd in called_cmds))
        self.assertTrue(any("git commit" in cmd for cmd in called_cmds))
        self.assertTrue(any("git push" in cmd for cmd in called_cmds))

        # Check plans.jsonl written
        plans_file = next(k for k in file_contents if "plans.jsonl" in k)
        plans_mock = file_contents[plans_file]
        plans_mock.write.assert_called()
        plans_entry = json.loads(plans_mock.write.call_args[0][0].strip())
        self.assertEqual(plans_entry["p_success"], 0.8)
        self.assertEqual(plans_entry["entropy"], 2.5)

    @patch("subprocess.run")
    @patch("os.path.exists")
    @patch("os.path.getsize")
    @patch("os.makedirs")
    @patch("sandbox_executor.entrypoint.planner.cleanup_repo_dir")
    def test_planner_main_fail_fast(self, mock_rmtree, mock_makedirs, mock_getsize, mock_exists, mock_run):
        test_args = ["planner.py", "I-12345/_", "pi-agent", "gemini"]

        intent_data = {
            "branch": "I-12345",
            "intent_id": "I-12345",
            "description": "Test description",
            "goal": "Test goal",
            "entropy_budget": "15.0",
        }

        def mock_open_impl(file, mode="r", *args, **kwargs):
            file_str = str(file)
            if "intents.jsonl" in file_str:
                mock_file = MagicMock()
                mock_file.__iter__.return_value = [json.dumps(intent_data) + "\n"]
                mock_file.__enter__.return_value = mock_file
                return mock_file
            elif "planner.template.md" in file_str:
                mock_file = MagicMock()
                mock_file.read.return_value = "Template content"
                mock_file.__enter__.return_value = mock_file
                return mock_file
            else:
                mock_file = MagicMock()
                mock_file.write = MagicMock()
                mock_file.__enter__.return_value = mock_file
                return mock_file

        def mock_exists_impl(path):
            path_str = str(path)
            return "intents.jsonl" in path_str

        mock_exists.side_effect = mock_exists_impl
        mock_getsize.return_value = 0

        # Git commands succeed, but agent npx/pi command fails
        def mock_run_side_effect(args, *a, **kw):
            result = MagicMock()
            if args[0] in ["pi", "npx"]:
                result.returncode = 1
            else:
                result.returncode = 0
            return result

        mock_run.side_effect = mock_run_side_effect

        # Directly modify and restore sys.argv
        old_argv = sys.argv
        sys.argv = test_args
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                with (
                    patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir}),
                    patch("sandbox_executor.entrypoint.planner.get_workspace_dir", return_value=tmp_dir),
                    patch("builtins.open", side_effect=mock_open_impl),
                ):
                    with self.assertRaises(SystemExit) as cm:
                        planner.main()
                    self.assertEqual(cm.exception.code, 1)
            finally:
                sys.argv = old_argv

    @patch("subprocess.run")
    @patch("os.path.exists")
    @patch("os.path.getsize")
    @patch("os.makedirs")
    @patch("sandbox_executor.entrypoint.planner.cleanup_repo_dir")
    def test_planner_main_model_sanitization(self, mock_rmtree, mock_makedirs, mock_getsize, mock_exists, mock_run):
        # Test model names with special characters to ensure robust sanitization
        test_cases = [
            ("gemini/3.5-flash (Medium) [Special]*?", "gemini_3.5-flash-Medium-Special"),
            ("gemini..3.5", "gemini.3.5"),
            (".gemini.pro.", "gemini.pro"),
            ("gemini 3.5  flash", "gemini-3.5-flash"),
            ("gemini\t3.5\nflash", "gemini-3.5-flash"),
            ("a" * 70, "a" * 64),
            ("a" * 63 + "-extra", "a" * 63),
            ("*?($", "unknown-model"),
        ]

        intent_data = {
            "branch": "I-12345",
            "intent_id": "I-12345",
            "description": "Test description",
            "goal": "Test goal",
            "entropy_budget": "15.0",
        }

        def _make_mock_open(intent_data, file_contents):
            """Factory for mock_open_impl — defined outside the loop to avoid fragile redefinition."""

            def mock_open_impl(file, mode="r", *args, _fc=file_contents, **kwargs):
                file_str = str(file)
                if "intents.jsonl" in file_str:
                    mock_file = MagicMock()
                    mock_file.__iter__.return_value = [json.dumps(intent_data) + "\n"]
                    mock_file.__enter__.return_value = mock_file
                    return mock_file
                elif "planner.template.md" in file_str:
                    mock_file = MagicMock()
                    mock_file.read.return_value = "Template content {intent_json} {plan_id} {safe_model}"
                    mock_file.__enter__.return_value = mock_file
                    return mock_file
                elif "plans/P-" in file_str and "md" in file_str and "r" in mode:
                    mock_file = MagicMock()
                    plan_md_content = """# Plan

## Overall Plan Metrics

| metric | value |
| --- | --- |
| p_success_pred | 0.8 |
| entropy_pred | 2.5 |
| impact_pred | 1.0 |
| cost_pred | 0.5 |
| learning_value_pred | 0.5 |
| ev_pred | 0.6 |
"""
                    mock_file.read.return_value = plan_md_content
                    mock_file.__enter__.return_value = mock_file
                    return mock_file
                else:
                    mock_file = MagicMock()
                    mock_file.write = MagicMock()
                    mock_file.__enter__.return_value = mock_file
                    _fc[file_str] = mock_file
                    return mock_file

            return mock_open_impl

        def _make_mock_exists():
            """Factory for mock_exists_impl — defined outside the loop to avoid fragile redefinition."""

            def mock_exists_impl(path):
                path_str = str(path)
                return "intents.jsonl" in path_str or "planner.template.md" in path_str or "plans/P-" in path_str

            return mock_exists_impl

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir}),
            patch("sandbox_executor.entrypoint.planner.get_workspace_dir", return_value=tmp_dir),
        ):
            for model_name, expected_safe_model in test_cases:
                with self.subTest(model_name=model_name):
                    mock_run.reset_mock()
                    test_args = ["planner.py", "I-12345/_", "pi-agent", model_name]

                    file_contents = {}
                    mock_exists.side_effect = _make_mock_exists()
                    mock_getsize.return_value = 100

                    mock_run_result = MagicMock()
                    mock_run_result.returncode = 0
                    mock_run_result.stdout = "Successful Agent Run Output"
                    mock_run.return_value = mock_run_result

                    old_argv = sys.argv
                    sys.argv = test_args
                    try:
                        with patch("builtins.open", side_effect=_make_mock_open(intent_data, file_contents)):
                            planner.main()
                    finally:
                        sys.argv = old_argv

                    # Check git branch checkout command — use explicit list assertion for a clear
                    # AssertionError (not StopIteration) when no matching command is found.
                    called_cmds = [" ".join(call[0][0]) for call in mock_run.call_args_list]
                    checkout_cmds = [cmd for cmd in called_cmds if "git checkout -b" in cmd]
                    self.assertTrue(checkout_cmds, f"No 'git checkout -b' command found in: {called_cmds}")
                    checkout_cmd = checkout_cmds[0]

                    self.assertIn(expected_safe_model, checkout_cmd)

    @patch("subprocess.run")
    @patch("os.path.exists")
    @patch("os.path.getsize")
    @patch("os.makedirs")
    @patch("sandbox_executor.entrypoint.planner.cleanup_repo_dir")
    def test_planner_invalid_plan_structure_fails_fast(
        self, mock_rmtree, mock_makedirs, mock_getsize, mock_exists, mock_run
    ):
        """Test that planner fails fast when agent output is trivial conversational text."""
        test_args = ["planner.py", "I-12345/_", "pi-agent", "gemini"]

        intent_data = {
            "branch": "I-12345",
            "intent_id": "I-12345",
            "description": "Test description",
            "goal": "Test goal",
        }

        def mock_open_impl(file, mode="r", *args, **kwargs):
            file_str = str(file)
            if "intents.jsonl" in file_str:
                mock_file = MagicMock()
                mock_file.__iter__.return_value = [json.dumps(intent_data) + "\n"]
                mock_file.__enter__.return_value = mock_file
                return mock_file
            elif "planner.template.md" in file_str:
                mock_file = MagicMock()
                mock_file.read.return_value = "Template content"
                mock_file.__enter__.return_value = mock_file
                return mock_file
            elif "plans/P-" in file_str and "md" in file_str and "r" in mode:
                mock_file = MagicMock()
                mock_file.read.return_value = "I will search for files in the repository to locate safety.md and files."
                mock_file.__enter__.return_value = mock_file
                return mock_file
            else:
                mock_file = MagicMock()
                mock_file.write = MagicMock()
                mock_file.__enter__.return_value = mock_file
                return mock_file

        mock_exists.side_effect = lambda p: "intents.jsonl" in str(p) or "plans/P-" in str(p)
        mock_getsize.return_value = 80

        mock_run_result = MagicMock()
        mock_run_result.returncode = 0
        mock_run_result.stdout = "I will search for files in the repository to locate safety.md and files."
        mock_run.return_value = mock_run_result

        old_argv = sys.argv
        sys.argv = test_args
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                with (
                    patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir}),
                    patch("sandbox_executor.entrypoint.planner.get_workspace_dir", return_value=tmp_dir),
                    patch("builtins.open", side_effect=mock_open_impl),
                ):
                    with self.assertRaises(SystemExit) as cm:
                        planner.main()
                    self.assertEqual(cm.exception.code, 1)
            finally:
                sys.argv = old_argv


if __name__ == "__main__":
    unittest.main()
