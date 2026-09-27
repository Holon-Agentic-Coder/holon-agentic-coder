import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest

import pytest

from tests.hermetic_fixtures import (
    create_seeded_remote,
    remote_path,
    remote_volume_args,
    remove_remote_volume,
)


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
            # A named volume owns its own uid, so the container can clone from and push back to the
            # fixture without any host/container uid negotiation.
            volume = create_seeded_remote()
            self.addCleanup(remove_remote_volume, volume)

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
                f"HOLON_REPO_URL={remote_path(volume)}",
                *remote_volume_args(volume),
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
                        f"HOLON_REPO_URL={remote_path(volume)}",
                        *remote_volume_args(volume),
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
