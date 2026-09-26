import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest

import pytest


def _is_docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        res = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
        return res.returncode == 0
    except Exception:
        return False


@pytest.mark.integration_test
class TestPlannerIntegration(unittest.TestCase):
    def test_planner_docker_usage(self):
        """Test that running the orchestrator image with the planner role and no arguments
        correctly routes to planner.py and exits with usage instructions.
        """
        if not _is_docker_available():
            self.skipTest("Docker daemon is not available in test environment.")

        cmd = ["docker", "run", "--rm", "-e", "HOLON_ROLE=planner", "holon/orchestrator"]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            self.fail(f"Failed to execute docker run command: {e}")

        self.assertEqual(result.returncode, 1)
        self.assertIn("Usage: planner.py", result.stdout)

    def test_planner_docker_execution_all_agents_fail_fast(self):
        """Test that running the orchestrator with the planner role fails fast
        (returns non-zero exit code and does not push branches) when agent commands fail
        (due to missing API keys / missing local tools in the test environment).
        """
        if not _is_docker_available():
            self.skipTest("Docker daemon is not available in test environment.")

        with tempfile.TemporaryDirectory() as tmp_dir:
            # Git refuses to use a repository mounted from the host unless it is declared trusted;
            # the container runs as uid 1000 while the host temp dir is owned by the CI user.
            gitconfig_path = os.path.join(tmp_dir, "gitconfig")
            with open(gitconfig_path, "w", encoding="utf-8") as gf:
                gf.write("[safe]\n\tdirectory = /mock_remote.git\n")
            bare_repo_dir = os.path.join(tmp_dir, "remote.git")
            subprocess.run(["git", "init", "--bare", bare_repo_dir], check=True, capture_output=True)

            # Seed the bare repository with an initial commit on main
            seed_dir = os.path.join(tmp_dir, "seed_repo")
            subprocess.run(["git", "init", "-b", "main", seed_dir], check=True, capture_output=True)
            subprocess.run(["git", "-C", seed_dir, "config", "user.email", "test@holon.com"], check=True)
            subprocess.run(["git", "-C", seed_dir, "config", "user.name", "Test User"], check=True)

            ledger_dir = os.path.join(seed_dir, "holon-knowledge", "ledger")
            os.makedirs(ledger_dir, exist_ok=True)
            with open(os.path.join(ledger_dir, "intents.jsonl"), "w") as f:
                f.write("")

            subprocess.run(["git", "-C", seed_dir, "add", "."], check=True, capture_output=True)
            subprocess.run(["git", "-C", seed_dir, "commit", "-m", "init"], check=True, capture_output=True)
            subprocess.run(["git", "-C", seed_dir, "remote", "add", "origin", bare_repo_dir], check=True)
            subprocess.run(["git", "-C", seed_dir, "push", "origin", "main"], check=True, capture_output=True)

            intent_json_path = os.path.join(tmp_dir, "intent.json")
            intent_data = {
                "slug": "test-planner-integration",
                "description": "Integration test description for planner",
                "goal": "Verify planner fail-fast via docker",
                "target_branch": "main",
            }
            with open(intent_json_path, "w") as f:
                json.dump(intent_data, f)

            creator_cmd = [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "-e",
                "HOLON_ROLE=intent-creator",
                "-e",
                "HOLON_REPO_URL=/mock_remote.git",
                "-e",
                "GIT_CONFIG_GLOBAL=/tmp/holon-test.gitconfig",
                "-v",
                f"{gitconfig_path}:/tmp/holon-test.gitconfig:ro",
                "-v",
                f"{bare_repo_dir}:/mock_remote.git",
                "-v",
                f"{intent_json_path}:/tmp/intent.json",
                "holon/orchestrator",
            ]

            try:
                creator_result = subprocess.run(creator_cmd, capture_output=True, text=True, timeout=60)
            except Exception as e:
                self.fail(f"Failed to execute intent-creator: {e}")

            self.assertEqual(creator_result.returncode, 0, f"Failed to create intent branch: {creator_result.stderr}")

            match = re.search(
                r"Intent branch '(I-\d+-test-planner-integration/_)' created and intent logged",
                creator_result.stdout,
            )
            self.assertTrue(match, f"Could not find success message in output:\n{creator_result.stdout}")
            intent_branch = match.group(1)

            agents = [
                "pi-agent",
                "claude-agent",
                "gemini-agent",
                "opencode-agent",
                "codex-agent",
                "antigravity-agent",
            ]

            for agent in agents:
                with self.subTest(agent=agent):
                    time.sleep(1)

                    agent_images = {
                        "pi-agent": "holon/agent-pi",
                        "claude-agent": "holon/agent-claude",
                        "gemini-agent": "holon/agent-gemini",
                        "opencode-agent": "holon/agent-opencode",
                        "codex-agent": "holon/agent-codex",
                        "antigravity-agent": "holon/agent-antigravity",
                    }
                    image_name = agent_images.get(agent, "holon/orchestrator")

                    cmd = [
                        "docker",
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "-e",
                        "HOLON_ROLE=planner",
                        "-e",
                        "HOLON_REPO_URL=/mock_remote.git",
                        "-e",
                        "GIT_CONFIG_GLOBAL=/tmp/holon-test.gitconfig",
                        "-v",
                        f"{gitconfig_path}:/tmp/holon-test.gitconfig:ro",
                        "-v",
                        f"{bare_repo_dir}:/mock_remote.git",
                        image_name,
                        intent_branch,
                        agent,
                        "gemini-3.5-flash",
                    ]

                    try:
                        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                    except subprocess.TimeoutExpired:
                        self.fail(f"Docker run command for {agent} timed out after 60 seconds.")
                    except Exception as e:
                        self.fail(f"Failed to execute docker run command for {agent}: {e}")

                    # The container should exit with a non-zero code due to fail-fast
                    self.assertNotEqual(
                        result.returncode,
                        0,
                        f"Docker run for {agent} should have failed but returned 0.\n"
                        f"Stdout:\n{result.stdout}\nStderr:\n{result.stderr}",
                    )

                    combined_output = result.stdout + "\n" + result.stderr
                    has_error = (
                        "Error: agent command failed" in combined_output
                        or "Error: Exception running agent" in combined_output
                        or "Error: Missing required API credentials" in combined_output
                        or "Error: Missing required credentials" in combined_output
                        or "Error: Missing required environment variable" in combined_output
                    )
                    self.assertTrue(
                        has_error,
                        f"Combined output did not contain expected fail-fast error message for {agent}.\n"
                        f"Stdout:\n{result.stdout}\nStderr:\n{result.stderr}",
                    )

                    # Verify that no success/push message was printed
                    self.assertNotIn("successfully created, committed, and pushed", result.stdout)


if __name__ == "__main__":
    unittest.main()
