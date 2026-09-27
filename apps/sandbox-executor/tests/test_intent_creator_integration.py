import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

import pytest

from tests.hermetic_fixtures import create_seeded_remote, remote_show_file, remote_volume_args, remove_remote_volume


def _is_docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        res = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
        return res.returncode == 0
    except Exception:
        return False


@pytest.mark.integration_test
class TestIntentCreatorIntegration(unittest.TestCase):
    def test_intent_creator_docker_usage_no_file(self):
        """Test that running the intent creator without mounting intent.json
        exits with error code 1.
        """
        if not _is_docker_available():
            self.skipTest("Docker daemon is not available in test environment.")

        cmd = ["docker", "run", "--rm", "-e", "HOLON_ROLE=intent-creator", "holon/orchestrator"]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            self.fail(f"Failed to execute docker run command: {e}")

        self.assertEqual(result.returncode, 1)
        self.assertIn("Error: /tmp/intent.json does not exist", result.stdout)

    def test_intent_creator_docker_execution(self):
        """Test that running the orchestrator with the intent-creator role successfully
        creates the intent branch and appends the intent to the ledger using a bare repository held in a Docker volume.
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
                "slug": "test-intent-integration",
                "description": "Integration test description",
                "goal": "Verify intent creator via docker",
                "target_branch": "main",
            }
            with open(intent_json_path, "w") as f:
                json.dump(intent_data, f)

            cmd = [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "-e",
                "HOLON_ROLE=intent-creator",
                "-e",
                "HOLON_REPO_URL=/mock_remote.git",
                *remote_volume_args(volume),
                "-v",
                f"{intent_json_path}:/tmp/intent.json",
                "holon/orchestrator",
            ]

            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            except subprocess.TimeoutExpired:
                self.fail("Docker run command timed out after 60 seconds.")
            except Exception as e:
                self.fail(f"Failed to execute docker run command: {e}")

            self.assertEqual(
                result.returncode,
                0,
                f"Docker run failed with code {result.returncode}.\nStdout:\n{result.stdout}\nStderr:\n{result.stderr}",
            )

            # Parse the branch name from stdout
            match = re.search(
                r"Intent branch '(I-\d+-test-intent-integration/_)' created and intent logged", result.stdout
            )
            self.assertTrue(match, f"Could not find success message in output:\n{result.stdout}")

            intent_branch = match.group(1)

            # Verify the pushed branch carries our intent record. The fixture never surfaces on the
            # host, so the read is done by a container running as the volume's own user.
            show_result = remote_show_file(intent_branch, "holon-knowledge/ledger/intents.jsonl", volume)

            self.assertEqual(
                show_result.returncode,
                0,
                f"Failed to show intents ledger from branch {intent_branch}. Stderr:\n{show_result.stderr}",
            )

            # Verify the ledger contains our intent JSON
            ledger_lines = show_result.stdout.strip().splitlines()
            found = False
            for line in ledger_lines:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    if (
                        data.get("slug") == "test-intent-integration"
                        and data.get("goal") == "Verify intent creator via docker"
                    ):
                        found = True
                        self.assertEqual(data.get("status"), "proposed")
                        break
                except json.JSONDecodeError:
                    continue

            self.assertTrue(
                found, f"Could not find intent with slug 'test-intent-integration' in ledger:\n{show_result.stdout}"
            )


if __name__ == "__main__":
    unittest.main()
