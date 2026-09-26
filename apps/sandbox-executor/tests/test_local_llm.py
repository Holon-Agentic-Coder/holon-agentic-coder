"""Tests for host-local LLM support in the sandbox launcher (Bean 0049).

Covers the authority rewrite decision table, the generated container-side agent directory, the gateway host mapping, and
the keyless validation path for local providers.
"""

import json
import os
import stat
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from sandbox_executor import cli, local_llm
from sandbox_executor.agent_runner import get_runner


class TestRewriteAuthority(unittest.TestCase):
    """Only loopback and explicitly declared authorities may be redirected to the Docker gateway."""

    def test_loopback_hosts_are_rewritten(self):
        cases = {
            "http://localhost:11434/v1": "http://host.docker.internal:11434/v1",
            "http://127.0.0.1:8081/v1": "http://host.docker.internal:8081/v1",
            "http://0.0.0.0:8081/v1": "http://host.docker.internal:8081/v1",
            "http://[::1]:8081/v1": "http://host.docker.internal:8081/v1",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(local_llm.rewrite_authority(url), expected)

    def test_port_path_and_scheme_are_preserved(self):
        self.assertEqual(
            local_llm.rewrite_authority("https://localhost:11434/v1?stream=1"),
            "https://host.docker.internal:11434/v1?stream=1",
        )

    def test_declared_host_local_authority_is_rewritten(self):
        self.assertEqual(
            local_llm.rewrite_authority("http://192.168.2.13:8081/v1", {"192.168.2.13"}),
            "http://host.docker.internal:8081/v1",
        )

    def test_undeclared_lan_authority_is_untouched(self):
        """Safety invariant inherited from Bean 0027: never blanket-rewrite RFC1918 ranges."""
        self.assertEqual(
            local_llm.rewrite_authority("http://192.168.2.13:8081/v1"),
            "http://192.168.2.13:8081/v1",
        )
        self.assertEqual(
            local_llm.rewrite_authority("http://192.168.2.99:8081/v1", {"192.168.2.13"}),
            "http://192.168.2.99:8081/v1",
        )

    def test_public_authority_is_untouched(self):
        self.assertEqual(
            local_llm.rewrite_authority("https://api.openai.com/v1", {"192.168.2.13"}),
            "https://api.openai.com/v1",
        )

    def test_gateway_authority_is_idempotent(self):
        self.assertEqual(
            local_llm.rewrite_authority("http://host.docker.internal:8081/v1"),
            "http://host.docker.internal:8081/v1",
        )

    def test_malformed_authority_is_untouched(self):
        self.assertEqual(local_llm.rewrite_authority("not a url"), "not a url")
        self.assertEqual(local_llm.rewrite_authority(""), "")

    def test_allow_list_is_case_insensitive(self):
        self.assertEqual(
            local_llm.rewrite_authority("http://MyBox.lan:8081/v1", {"mybox.lan"}),
            "http://host.docker.internal:8081/v1",
        )


class TestOptIn(unittest.TestCase):
    def test_off_by_default_and_never_inferred(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(local_llm.local_llm_requested())

    def test_truthy_values(self):
        for value in ("1", "true", "TRUE", "yes", "on"):
            with self.subTest(value=value), patch.dict(os.environ, {"HOLON_LOCAL_LLM": value}, clear=True):
                self.assertTrue(local_llm.local_llm_requested())

    def test_falsy_values(self):
        for value in ("0", "false", "off", ""):
            with self.subTest(value=value), patch.dict(os.environ, {"HOLON_LOCAL_LLM": value}, clear=True):
                self.assertFalse(local_llm.local_llm_requested())

    def test_allow_list_parsing_drops_blanks_and_lowercases(self):
        with patch.dict(os.environ, {"HOLON_HOST_LOCAL_HOSTS": " MyBox.lan, ,192.168.2.13,"}, clear=True):
            self.assertEqual(local_llm.host_local_allow_list(), {"mybox.lan", "192.168.2.13"})


class TestBuildContainerConfig(unittest.TestCase):
    def test_every_provider_base_url_is_rewritten_and_other_fields_survive(self):
        host_config = {
            "providers": {
                "vmlx": {
                    "baseUrl": "http://192.168.2.13:8081/v1",
                    "api": "openai-completions",
                    "apiKey": "placeholder",
                    "models": [{"id": "Qwen/Test", "contextWindow": 999424}],
                },
                "cloud": {
                    "baseUrl": "https://api.example.com/v1",
                    "apiKey": "$MY_KEY",
                    "models": [{"id": "gpt-x"}],
                },
            },
        }
        config = local_llm.build_container_config(host_config, {"192.168.2.13"})
        providers = config["providers"]
        self.assertEqual(providers["vmlx"]["baseUrl"], "http://host.docker.internal:8081/v1")
        self.assertEqual(providers["vmlx"]["api"], "openai-completions")
        self.assertEqual(providers["vmlx"]["models"][0]["contextWindow"], 999424)
        self.assertEqual(providers["cloud"]["baseUrl"], "https://api.example.com/v1")
        self.assertEqual(providers["cloud"]["apiKey"], "$MY_KEY")

    def test_host_config_is_not_mutated(self):
        host_config = {"providers": {"local": {"baseUrl": "http://localhost:11434/v1"}}}
        local_llm.build_container_config(host_config)
        self.assertEqual(host_config["providers"]["local"]["baseUrl"], "http://localhost:11434/v1")

    def test_synthesized_from_environment_when_no_host_config(self):
        env = {
            "HOLON_LOCAL_LLM": "1",
            "HOLON_LOCAL_BASE_URL": "http://localhost:11434/v1",
            "HOLON_LOCAL_MODELS": "llama3.2, qwen3:8b",
            "HOLON_LOCAL_PROVIDER": "ollama",
        }
        with patch.dict(os.environ, env, clear=True):
            config = local_llm.build_container_config(None)
        self.assertEqual(config["providers"]["ollama"]["baseUrl"], "http://host.docker.internal:11434/v1")
        self.assertEqual(
            [model["id"] for model in config["providers"]["ollama"]["models"]],
            ["llama3.2", "qwen3:8b"],
        )

    def test_missing_base_url_raises_actionable_error(self):
        with (
            patch.dict(os.environ, {"HOLON_LOCAL_LLM": "1"}, clear=True),
            self.assertRaises(local_llm.LocalLLMConfigError) as ctx,
        ):
            local_llm.build_container_config(None)
        self.assertIn("HOLON_LOCAL_BASE_URL", str(ctx.exception))

    def test_missing_model_ids_raises_actionable_error(self):
        env = {"HOLON_LOCAL_BASE_URL": "http://localhost:11434/v1"}
        with (
            patch.dict(os.environ, env, clear=True),
            self.assertRaises(local_llm.LocalLLMConfigError) as ctx,
        ):
            local_llm.build_container_config(None)
        self.assertIn("HOLON_LOCAL_MODELS", str(ctx.exception))

    def test_empty_providers_fall_back_to_synthesis(self):
        env = {"HOLON_LOCAL_BASE_URL": "http://127.0.0.1:8081/v1", "HOLON_LOCAL_MODELS": "m1"}
        with patch.dict(os.environ, env, clear=True):
            config = local_llm.build_container_config({"providers": {}})
        self.assertEqual(config["providers"]["local"]["baseUrl"], "http://host.docker.internal:8081/v1")


class TestPrepareAgentDir(unittest.TestCase):
    def test_writes_rewritten_models_json_with_owner_only_permissions(self):
        host_config = {"providers": {"vmlx": {"baseUrl": "http://localhost:8081/v1", "models": [{"id": "m"}]}}}
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "agent")
            config = local_llm.prepare_agent_dir(dest, host_config=host_config)
            path = os.path.join(dest, "models.json")
            with open(path) as handle:
                on_disk = json.load(handle)
            self.assertEqual(on_disk, config)
            self.assertEqual(on_disk["providers"]["vmlx"]["baseUrl"], "http://host.docker.internal:8081/v1")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_container_mount_and_env_point_at_the_same_directory(self):
        mounts = local_llm.container_mount_args("/host/tmp/agent")
        self.assertEqual(mounts, ["-v", f"/host/tmp/agent:{local_llm.CONTAINER_AGENT_DIR}:rw"])
        self.assertEqual(
            local_llm.container_env("/host/tmp/agent"),
            {local_llm.ENV_PI_AGENT_DIR: local_llm.CONTAINER_AGENT_DIR},
        )

    def test_rewritten_local_hosts_reports_only_gateway_authorities(self):
        config = {
            "providers": {
                "local": {"baseUrl": "http://host.docker.internal:8081/v1"},
                "cloud": {"baseUrl": "https://api.example.com/v1"},
                "other": {"baseUrl": "http://host.docker.internal/v1"},
            },
        }
        self.assertEqual(
            local_llm.rewritten_local_hosts(config),
            ["host.docker.internal", "host.docker.internal:8081"],
        )

    def test_host_models_json_reads_preferred_layout_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            new_dir = os.path.join(tmp, ".pi", "agent")
            os.makedirs(new_dir)
            with open(os.path.join(new_dir, "models.json"), "w") as handle:
                json.dump({"providers": {"vmlx": {"baseUrl": "http://localhost:8081/v1"}}}, handle)
            with patch.object(local_llm, "pi_agent_dirs", lambda: [new_dir, os.path.join(tmp, "missing")]):
                self.assertEqual(
                    local_llm.host_models_json()["providers"]["vmlx"]["baseUrl"], "http://localhost:8081/v1"
                )

    def test_host_models_json_invalid_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "models.json"), "w") as handle:
                handle.write("{not json")
            with patch.object(local_llm, "pi_agent_dirs", lambda: [tmp]):
                self.assertIsNone(local_llm.host_models_json())


class TestLauncherIntegration(unittest.TestCase):
    def _run(self, env, mounts=None, tr_envs=None, mkdtemp_dir=None):
        with (
            patch("shutil.which", return_value="/usr/bin/docker"),
            patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
            patch.object(cli, "get_agent_session_mounts", side_effect=mounts or (lambda agent: [])),
            patch.object(
                cli,
                "get_token_reduction_mounts_and_envs",
                return_value=([], dict(tr_envs or {})),
            ),
            patch.object(cli.tempfile, "mkdtemp", return_value=mkdtemp_dir or "/tmp/holon-pi-agent-test"),
            patch.object(cli.shutil, "rmtree") as mock_rmtree,
            patch.dict(os.environ, env, clear=True),
        ):
            code = cli.run_docker_container("executor", "holon/agent-pi", ["branch", "pi-agent", "m"])
        return code, mock_run.call_args[0][0], mock_rmtree

    def test_gateway_host_mapping_is_added_to_every_run(self):
        code, args, _ = self._run({})
        self.assertEqual(code, 0)
        # Emitted on Linux; on Docker Desktop the name already resolves and the flag is a no-op.
        if cli.sys.platform not in ("darwin", "win32"):
            self.assertIn("--add-host", args)
            self.assertIn("host.docker.internal:host-gateway", args)

    def test_local_mode_mounts_generated_dir_and_sets_agent_dir(self):
        env = {
            "HOLON_LOCAL_LLM": "1",
            "HOLON_LOCAL_BASE_URL": "http://localhost:8081/v1",
            "HOLON_LOCAL_MODELS": "qwen3:test",
        }
        with patch.object(local_llm, "prepare_agent_dir", wraps=local_llm.prepare_agent_dir) as prep:
            code, args, rmtree = self._run(env, mkdtemp_dir="/tmp/generated-agent")
        self.assertEqual(code, 0)
        prep.assert_called_once()
        self.assertIn("-v", args)
        self.assertIn(f"/tmp/generated-agent:{local_llm.CONTAINER_AGENT_DIR}:rw", args)
        self.assertIn(f"PI_CODING_AGENT_DIR={local_llm.CONTAINER_AGENT_DIR}", args)
        rmtree.assert_called_once_with("/tmp/generated-agent", ignore_errors=True)

    def test_local_mode_extends_no_proxy_for_local_endpoint(self):
        env = {
            "HOLON_LOCAL_LLM": "1",
            "HOLON_LOCAL_BASE_URL": "http://localhost:8081/v1",
            "HOLON_LOCAL_MODELS": "qwen3:test",
        }
        tr_envs = {"NO_PROXY": "localhost,127.0.0.1", "HTTPS_PROXY": "http://host.docker.internal:8080"}
        code, args, _ = self._run(env, tr_envs=tr_envs)
        self.assertEqual(code, 0)
        self.assertIn("NO_PROXY=localhost,127.0.0.1,host.docker.internal:8081", args)

    def test_unsatisfiable_local_mode_fails_loudly_and_cleans_up(self):
        with patch.object(local_llm, "host_models_json", return_value=None):
            code, _, rmtree = self._run({"HOLON_LOCAL_LLM": "1"}, mkdtemp_dir="/tmp/generated-agent")
        self.assertEqual(code, 1)
        rmtree.assert_called_once_with("/tmp/generated-agent", ignore_errors=True)

    def test_host_pi_agent_dir_is_not_mounted_in_local_mode(self):
        with tempfile.TemporaryDirectory() as home:
            agent_dir = os.path.join(home, ".pi", "agent")
            os.makedirs(agent_dir)
            env = {"HOME": home, "HOLON_LOCAL_LLM": "1"}
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(cli.get_agent_session_mounts("pi"), [])

    def test_host_pi_agent_dir_is_mounted_read_only_without_local_mode(self):
        with tempfile.TemporaryDirectory() as home:
            agent_dir = os.path.join(home, ".pi", "agent")
            os.makedirs(agent_dir)
            with patch.dict(os.environ, {"HOME": home}, clear=True):
                mounts = cli.get_agent_session_mounts("pi")
            self.assertIn(f"{agent_dir}:/home/holon/.pi/agent:ro", mounts)
            self.assertEqual(mounts.count("-v"), 1)


class TestRunnerValidation(unittest.TestCase):
    def test_pi_validates_without_key_when_local_endpoint_is_configured(self):
        env = {"HOLON_LOCAL_LLM": "1", "PI_CODING_AGENT_DIR": "/home/holon/.holon-pi-agent"}
        with patch.dict(os.environ, env, clear=True), patch("os.path.exists", return_value=False):
            get_runner("pi-agent").validate()

    def test_pi_still_requires_a_key_without_local_mode(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("os.path.exists", return_value=False),
            self.assertRaises(SystemExit),
        ):
            get_runner("pi-agent").validate()

    def test_stray_opt_in_without_endpoint_does_not_mask_missing_key(self):
        env = {"HOLON_LOCAL_LLM": "1"}
        with (
            patch.dict(os.environ, env, clear=True),
            patch("os.path.exists", return_value=False),
            self.assertRaises(SystemExit),
        ):
            get_runner("pi-agent").validate()


if __name__ == "__main__":
    unittest.main()
