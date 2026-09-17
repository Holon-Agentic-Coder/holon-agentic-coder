import json
import os
import tempfile
import unittest
from unittest.mock import patch

from sandbox_executor.cli import main
from sandbox_executor.scaffold import (
    GENERIC_GITIGNORE,
    PYTHON_GITIGNORE,
    init_project,
)


class TestHolonInit(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.target_dir = self.temp_dir.name

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_init_scaffolds_default_python_directories_and_files(self):
        """Verify full directory scaffolding with default python template."""
        ret = init_project(target_dir=self.target_dir, template="python", force=False)
        self.assertEqual(ret, 0)

        # Verify holon-config structure
        expected_config_files = [
            "holon-config/world/ruleset.md",
            "holon-config/world/constraints.md",
            "holon-config/metrics/README.md",
            "holon-config/metrics/entropy_config.json",
            "holon-config/metrics/ev_config.json",
            "holon-config/metrics/system_entropy_config.json",
            "holon-config/prompts/planner.template.md",
            "holon-config/prompts/executor.template.md",
        ]
        for rel_path in expected_config_files:
            full_path = os.path.join(self.target_dir, rel_path)
            self.assertTrue(os.path.exists(full_path), f"Expected file {rel_path} was not created")

        # Verify python-specific ruleset content
        with open(os.path.join(self.target_dir, "holon-config/world/ruleset.md"), encoding="utf-8") as f:
            content = f.read()
            self.assertIn("Python Runtime:", content)
            self.assertIn("pytest", content)

        # Verify metrics config JSON validity
        for json_file in ["entropy_config.json", "ev_config.json", "system_entropy_config.json"]:
            full_path = os.path.join(self.target_dir, "holon-config/metrics", json_file)
            with open(full_path, encoding="utf-8") as f:
                data = json.load(f)
                self.assertIsInstance(data, dict)

        # Verify holon-knowledge ledgers and directories
        expected_knowledge_files = [
            "holon-knowledge/ledger/intents.jsonl",
            "holon-knowledge/ledger/plans.jsonl",
            "holon-knowledge/ledger/executions.jsonl",
            "holon-knowledge/plans/.gitkeep",
            "holon-knowledge/kb/.gitkeep",
        ]
        for rel_path in expected_knowledge_files:
            full_path = os.path.join(self.target_dir, rel_path)
            self.assertTrue(os.path.exists(full_path), f"Expected knowledge file {rel_path} was not created")

        # Verify .gitignore creation and entries
        gitignore_path = os.path.join(self.target_dir, ".gitignore")
        self.assertTrue(os.path.exists(gitignore_path))
        with open(gitignore_path, encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines()]
            for item in PYTHON_GITIGNORE:
                self.assertIn(item, lines)

    def test_init_template_generic(self):
        """Verify generic template creates generic ruleset and ignores."""
        ret = init_project(target_dir=self.target_dir, template="generic", force=False)
        self.assertEqual(ret, 0)

        with open(os.path.join(self.target_dir, "holon-config/world/ruleset.md"), encoding="utf-8") as f:
            content = f.read()
            self.assertIn("Project Management:", content)
            self.assertNotIn("Python Runtime:", content)

        gitignore_path = os.path.join(self.target_dir, ".gitignore")
        with open(gitignore_path, encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines()]
            for item in GENERIC_GITIGNORE:
                self.assertIn(item, lines)
            self.assertNotIn(".pytest_cache/", lines)

    def test_idempotency_invariant_never_truncates_ledgers(self):
        """IDEMPOTENCY INVARIANT: Ledger files must NEVER be truncated or overwritten, even with --force."""
        # 1. First init
        init_project(target_dir=self.target_dir, template="python")

        # 2. Write real data into ledgers
        intents_path = os.path.join(self.target_dir, "holon-knowledge/ledger/intents.jsonl")
        plans_path = os.path.join(self.target_dir, "holon-knowledge/ledger/plans.jsonl")
        execs_path = os.path.join(self.target_dir, "holon-knowledge/ledger/executions.jsonl")

        intent_data = '{"slug": "test-intent", "branch": "I-123-test"}\n'
        plan_data = '{"plan_id": "P-123", "status": "proposed"}\n'
        exec_data = '{"execution_id": "E-123", "status": "success"}\n'

        with open(intents_path, "w", encoding="utf-8") as f:
            f.write(intent_data)
        with open(plans_path, "w", encoding="utf-8") as f:
            f.write(plan_data)
        with open(execs_path, "w", encoding="utf-8") as f:
            f.write(exec_data)

        # 3. Second init WITHOUT force - data must persist
        ret1 = init_project(target_dir=self.target_dir, template="python", force=False)
        self.assertEqual(ret1, 0)
        with open(intents_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), intent_data)
        with open(plans_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), plan_data)
        with open(execs_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), exec_data)

        # 4. Third init WITH force - data must STILL persist!
        ret2 = init_project(target_dir=self.target_dir, template="python", force=True)
        self.assertEqual(ret2, 0)
        with open(intents_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), intent_data)
        with open(plans_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), plan_data)
        with open(execs_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), exec_data)

    def test_force_overwrites_config_files(self):
        """Verify that --force overwrites config files, while non-force preserves modifications."""
        ruleset_path = os.path.join(self.target_dir, "holon-config/world/ruleset.md")
        os.makedirs(os.path.dirname(ruleset_path), exist_ok=True)
        with open(ruleset_path, "w", encoding="utf-8") as f:
            f.write("CUSTOM USER RULESET")

        # Without force: must preserve custom content
        init_project(target_dir=self.target_dir, template="python", force=False)
        with open(ruleset_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "CUSTOM USER RULESET")

        # With force: must overwrite with standard template
        init_project(target_dir=self.target_dir, template="python", force=True)
        with open(ruleset_path, encoding="utf-8") as f:
            content = f.read()
            self.assertNotEqual(content, "CUSTOM USER RULESET")
            self.assertIn("Holon World Ruleset", content)

    def test_gitignore_appends_without_duplication(self):
        """Verify .gitignore updates existing files without creating duplicates."""
        gitignore_path = os.path.join(self.target_dir, ".gitignore")
        initial_content = "# Pre-existing ignores\nnode_modules/\n.venv/\n"
        with open(gitignore_path, "w", encoding="utf-8") as f:
            f.write(initial_content)

        init_project(target_dir=self.target_dir, template="python")

        with open(gitignore_path, encoding="utf-8") as f:
            content = f.read()
            lines = [line.strip() for line in content.splitlines() if line.strip()]

        # node_modules/ preserved
        self.assertIn("node_modules/", lines)
        # .venv/ appears exactly once
        self.assertEqual(lines.count(".venv/"), 1)
        # new entries added
        self.assertIn(".holon-cache/", lines)
        self.assertIn("__pycache__/", lines)
        self.assertIn(".pytest_cache/", lines)

        # Run again to ensure total idempotency (no duplicates added on second run)
        init_project(target_dir=self.target_dir, template="python")
        with open(gitignore_path, encoding="utf-8") as f:
            content2 = f.read()
            lines2 = [line.strip() for line in content2.splitlines() if line.strip()]
        self.assertEqual(lines, lines2)

    @patch("sandbox_executor.cli.init_project", return_value=0)
    def test_cli_main_init_subcommand(self, mock_init):
        """Verify holon init CLI arguments parsing and dispatch."""
        test_args = ["holon", "init", "/custom/path", "--template", "generic", "--force"]
        with patch("sys.argv", test_args):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)
            mock_init.assert_called_once_with(
                target_dir="/custom/path",
                template="generic",
                force=True,
            )

        mock_init.reset_mock()
        test_default_args = ["holon", "init"]
        with patch("sys.argv", test_default_args):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)
            mock_init.assert_called_once_with(
                target_dir=".",
                template="python",
                force=False,
            )


if __name__ == "__main__":
    unittest.main()
