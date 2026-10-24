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
            "http://127.0.0.2:8081/v1": "http://host.docker.internal:8081/v1",
            "http://127.1.2.3:8081/v1": "http://host.docker.internal:8081/v1",
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

    def test_port_qualified_authority_in_allow_list(self):
        self.assertEqual(
            local_llm.rewrite_authority("http://192.168.2.13:8081/v1", {"192.168.2.13:8081"}),
            "http://host.docker.internal:8081/v1",
        )
        self.assertEqual(
            local_llm.rewrite_authority("http://192.168.2.13:9999/v1", {"192.168.2.13:8081"}),
            "http://192.168.2.13:9999/v1",
        )

    def test_rewrite_authority_preserves_fragment(self):
        self.assertEqual(
            local_llm.rewrite_authority("http://127.0.0.1:8080/v1#section"),
            "http://host.docker.internal:8080/v1#section",
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

    def test_allow_list_parsing_strips_paths(self):
        with patch.dict(os.environ, {"HOLON_HOST_LOCAL_HOSTS": " 192.168.1.50:8081/v1, 10.0.0.1/ "}, clear=True):
            self.assertEqual(local_llm.host_local_allow_list(), {"192.168.1.50:8081", "10.0.0.1"})


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
        # Cloud providers are stripped to prevent host credentials crossing the sandbox boundary
        self.assertNotIn("cloud", providers)

    def test_cloud_providers_with_api_keys_are_pruned(self):
        host_config = {
            "providers": {
                "openai": {
                    "baseUrl": "https://api.openai.com/v1",
                    "apiKey": "sk-secret-token",
                    "models": [{"id": "gpt-4o"}],
                },
                "anthropic": {
                    "baseUrl": "https://api.anthropic.com/v1",
                    "apiKey": "sk-ant-secret",
                    "models": [{"id": "claude-3-5-sonnet"}],
                },
                "local_ollama": {
                    "baseUrl": "http://127.0.0.1:11434/v1",
                    "models": [{"id": "llama3.2"}],
                },
            },
        }
        config = local_llm.build_container_config(host_config)
        self.assertIn("local_ollama", config["providers"])
        self.assertNotIn("openai", config["providers"])
        self.assertNotIn("anthropic", config["providers"])
        self.assertEqual(
            config["providers"]["local_ollama"]["baseUrl"],
            "http://host.docker.internal:11434/v1",
        )
        self.assertEqual(config["providers"]["local_ollama"]["apiKey"], "holon-local")

    def test_local_provider_preserves_existing_api_key(self):
        host_config = {
            "providers": {
                "local_vllm": {
                    "baseUrl": "http://127.0.0.1:8000/v1",
                    "apiKey": "custom-secret-key",
                    "models": [{"id": "llama3"}],
                },
            },
        }
        config = local_llm.build_container_config(host_config)
        self.assertEqual(config["providers"]["local_vllm"]["apiKey"], "custom-secret-key")

    def test_only_cloud_providers_falls_back_to_synthesis(self):
        host_config = {
            "providers": {
                "openai": {
                    "baseUrl": "https://api.openai.com/v1",
                    "apiKey": "sk-secret",
                }
            }
        }
        env = {
            "HOLON_LOCAL_BASE_URL": "http://localhost:8081/v1",
            "HOLON_LOCAL_MODELS": "qwen3:8b",
        }
        with patch.dict(os.environ, env, clear=True):
            config = local_llm.build_container_config(host_config)
        self.assertIn("local", config["providers"])
        self.assertNotIn("openai", config["providers"])
        self.assertEqual(config["providers"]["local"]["baseUrl"], "http://host.docker.internal:8081/v1")

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

    def test_synthesized_config_unrewritable_endpoint_raises(self):
        env = {
            "HOLON_LOCAL_BASE_URL": "http://192.168.2.13:8081/v1",
            "HOLON_LOCAL_MODELS": "m1",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            self.assertRaises(local_llm.LocalLLMConfigError) as ctx,
        ):
            local_llm.build_container_config(None)
        self.assertIn("HOLON_HOST_LOCAL_HOSTS", str(ctx.exception))


class TestPrepareAgentDir(unittest.TestCase):
    def test_writes_rewritten_models_json_with_safe_container_permissions(self):
        old_umask = os.umask(0o077)
        try:
            host_config = {
                "providers": {
                    "vmlx": {"baseUrl": "http://localhost:8081/v1", "models": [{"id": "m", "contextWindow": 32768}]}
                }
            }
            with tempfile.TemporaryDirectory() as tmp:
                dest = os.path.join(tmp, "agent")
                config = local_llm.prepare_agent_dir(dest, host_config=host_config)
                path = os.path.join(dest, "models.json")
                with open(path) as handle:
                    on_disk = json.load(handle)
                self.assertEqual(on_disk, config)
                self.assertEqual(on_disk["providers"]["vmlx"]["baseUrl"], "http://host.docker.internal:8081/v1")
                # 0777 directory and 0644 file allow non-root container user (uid=1000) to access them on Linux
                self.assertEqual(stat.S_IMODE(os.stat(dest).st_mode), 0o777)
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o644)
        finally:
            os.umask(old_umask)

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

    def test_pi_agent_dirs_respects_env_var(self):
        with patch.dict(os.environ, {local_llm.ENV_PI_AGENT_DIR: "/custom/pi/dir"}, clear=True):
            dirs = local_llm.pi_agent_dirs()
            self.assertEqual(dirs[0], "/custom/pi/dir")


class TestLauncherIntegration(unittest.TestCase):
    def _run(self, env, mounts=None, tr_envs=None, mkdtemp_dir=None, agent_id="pi", token_reduce=False):
        # Synthesised model entries declare no contextWindow; the envelope needs one (Bean 0140).
        merged_env = {
            "GITHUB_TOKEN": "mock-token",
            "HOLON_REPO_URL": "git@github.com:dummy/repo.git",
            "HOLON_LOCAL_CONTEXT_WINDOW": "32768",
            **env,
        }
        with (
            patch("shutil.which", side_effect=lambda name: "/usr/bin/docker" if name == "docker" else None),
            patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
            patch.object(cli, "get_agent_session_mounts", side_effect=mounts or (lambda agent: [])),
            patch.object(
                cli,
                "get_token_reduction_mounts_and_envs",
                return_value=([], dict(tr_envs or {})),
            ),
            patch.object(cli.tempfile, "mkdtemp", return_value=mkdtemp_dir or "/tmp/holon-pi-agent-test"),
            patch.object(cli.shutil, "rmtree") as mock_rmtree,
            patch.dict(os.environ, merged_env, clear=True),
        ):
            code = cli.run_docker_container(
                "executor",
                f"holon/agent-{agent_id}",
                ["branch", f"{agent_id}-agent", "m"],
                agent_id=agent_id,
                token_reduce=token_reduce,
            )
        args = mock_run.call_args[0][0] if mock_run.called else []
        return code, args, mock_rmtree

    def test_gateway_host_mapping_is_added_to_every_run(self):
        code, args, _ = self._run({})
        self.assertEqual(code, 0)
        # Emitted on Linux; on Docker Desktop the name already resolves and the flag is a no-op.
        if cli.sys.platform not in ("darwin", "win32"):
            self.assertIn("--add-host", args)
            self.assertIn("host.docker.internal:host-gateway", args)

    def test_gateway_host_mapping_not_duplicated_with_token_reduce(self):
        with patch.object(cli.sys, "platform", "linux"):
            code, args, _ = self._run({}, token_reduce=True)
            self.assertEqual(code, 0)
            self.assertEqual(args.count("host.docker.internal:host-gateway"), 1)
            self.assertEqual(args.count("--add-host"), 1 + len(cli.BLOCKED_METADATA_HOSTS))

    def test_setup_token_reduction_proxy_excludes_gateway_host_args(self):
        with (
            patch.object(cli, "generate_root_ca", return_value=("/tmp/mock-ca.crt", "/tmp/mock-ca.key")),
            patch.object(cli, "_mitm_proxy_ca_paths", return_value=("/tmp/mock-ca.pem", "/tmp/mock-ca-cert.pem")),
            patch.object(cli.os.path, "isfile", lambda path: True),
            patch.object(cli, "_wait_for_proxy", lambda *args, **kwargs: True),
            patch("subprocess.run") as mock_subproc,
        ):
            mock_subproc.return_value = MagicMock(returncode=0, stdout="8080\n")
            try:
                mounts, _ = cli.setup_token_reduction_proxy()
                self.assertNotIn("--add-host", mounts)
                self.assertNotIn("host.docker.internal:host-gateway", mounts)
            finally:
                cli.teardown_token_reduction_proxy()

    def test_local_provider_forwarded_to_agent_provider(self):
        env = {
            "HOLON_LOCAL_LLM": "1",
            "HOLON_LOCAL_BASE_URL": "http://localhost:8081/v1",
            "HOLON_LOCAL_MODELS": "qwen3:test",
            "HOLON_LOCAL_PROVIDER": "vmlx",
        }
        code, args, _ = self._run(env)
        self.assertEqual(code, 0)
        self.assertIn("-e", args)
        self.assertIn("HOLON_AGENT_PROVIDER=vmlx", args)

    def test_local_provider_defaults_to_local_when_unset(self):
        env = {
            "HOLON_LOCAL_LLM": "1",
            "HOLON_LOCAL_BASE_URL": "http://localhost:8081/v1",
            "HOLON_LOCAL_MODELS": "qwen3:test",
        }
        with patch.object(local_llm, "host_models_json", return_value=None):
            code, args, _ = self._run(env)
        self.assertEqual(code, 0)
        self.assertIn("-e", args)
        self.assertIn("HOLON_AGENT_PROVIDER=local", args)

    def test_host_models_json_named_provider_aligns_agent_provider(self):
        host_config = {
            "providers": {
                "vmlx": {
                    "baseUrl": "http://localhost:8081/v1",
                    "models": [{"id": "local-model"}],
                }
            }
        }
        with patch.object(local_llm, "host_models_json", return_value=host_config):
            code, args, _ = self._run({"HOLON_LOCAL_LLM": "1"})
        self.assertEqual(code, 0)
        self.assertEqual(args.count("HOLON_AGENT_PROVIDER=vmlx"), 1)
        self.assertNotIn("HOLON_AGENT_PROVIDER=local", args)

    def test_multiple_local_providers_without_local_or_env_fails(self):
        host_config = {
            "providers": {
                "vmlx": {"baseUrl": "http://localhost:8081/v1", "models": [{"id": "m1"}]},
                "ollama": {"baseUrl": "http://localhost:11434/v1", "models": [{"id": "m2"}]},
            }
        }
        with patch.object(local_llm, "host_models_json", return_value=host_config):
            code, _, rmtree = self._run({"HOLON_LOCAL_LLM": "1"}, mkdtemp_dir="/tmp/generated-agent")
        self.assertEqual(code, 1)
        rmtree.assert_called_once_with("/tmp/generated-agent", ignore_errors=True)

    def test_multiple_local_providers_with_local_named_provider_defaults_to_local(self):
        host_config = {
            "providers": {
                "local": {"baseUrl": "http://localhost:8081/v1", "models": [{"id": "m1"}]},
                "ollama": {"baseUrl": "http://localhost:11434/v1", "models": [{"id": "m2"}]},
            }
        }
        with patch.object(local_llm, "host_models_json", return_value=host_config):
            code, args, _ = self._run({"HOLON_LOCAL_LLM": "1"})
        self.assertEqual(code, 0)
        self.assertEqual(args.count("HOLON_AGENT_PROVIDER=local"), 1)

    def test_multiple_local_providers_with_env_provider_disambiguates(self):
        host_config = {
            "providers": {
                "vmlx": {"baseUrl": "http://localhost:8081/v1", "models": [{"id": "m1"}]},
                "ollama": {"baseUrl": "http://localhost:11434/v1", "models": [{"id": "m2"}]},
            }
        }
        with patch.object(local_llm, "host_models_json", return_value=host_config):
            code, args, _ = self._run({"HOLON_LOCAL_LLM": "1", "HOLON_LOCAL_PROVIDER": "ollama"})
        self.assertEqual(code, 0)
        self.assertEqual(args.count("HOLON_AGENT_PROVIDER=ollama"), 1)

    def test_agent_id_normalization_accepts_agent_pi(self):
        env = {
            "HOLON_LOCAL_LLM": "1",
            "HOLON_LOCAL_BASE_URL": "http://localhost:8081/v1",
            "HOLON_LOCAL_MODELS": "qwen3:test",
        }
        with patch.object(local_llm, "prepare_agent_dir", wraps=local_llm.prepare_agent_dir) as prep:
            code, args, _ = self._run(env, agent_id="agent-pi")
        self.assertEqual(code, 0)
        prep.assert_called_once()
        self.assertIn(f"PI_CODING_AGENT_DIR={local_llm.CONTAINER_AGENT_DIR}", args)

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
        code, args, _ = self._run(env, tr_envs=tr_envs, token_reduce=False)
        self.assertEqual(code, 0)
        self.assertIn("NO_PROXY=localhost,127.0.0.1,host.docker.internal:8081", args)

        # With token reduction active, bare gateway host is also added for domain-only NO_PROXY matchers
        code, args, _ = self._run(env, tr_envs=tr_envs, token_reduce=True)
        self.assertEqual(code, 0)
        self.assertIn("NO_PROXY=localhost,127.0.0.1,host.docker.internal,host.docker.internal:8081", args)

    def test_unsatisfiable_local_mode_fails_loudly_and_cleans_up(self):
        with patch.object(local_llm, "host_models_json", return_value=None):
            code, _, rmtree = self._run({"HOLON_LOCAL_LLM": "1"}, mkdtemp_dir="/tmp/generated-agent")
        self.assertEqual(code, 1)
        rmtree.assert_called_once_with("/tmp/generated-agent", ignore_errors=True)

    def test_host_pi_agent_dir_is_not_mounted_in_local_mode(self):
        with tempfile.TemporaryDirectory() as home:
            agent_dir = os.path.join(home, ".pi", "agent")
            os.makedirs(agent_dir)
            models_file = os.path.join(agent_dir, "models.json")
            with open(models_file, "w") as handle:
                handle.write("{}")
            env = {"HOME": home, "HOLON_LOCAL_LLM": "1"}
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(cli.get_agent_session_mounts("pi"), [])

    def test_host_pi_agent_dir_is_mounted_read_only_without_local_mode(self):
        with tempfile.TemporaryDirectory() as home:
            agent_dir = os.path.join(home, ".pi", "agent")
            os.makedirs(agent_dir)
            models_file = os.path.join(agent_dir, "models.json")
            with open(models_file, "w") as handle:
                handle.write("{}")
            # Sensitive files (auth.json, sessions/) must never be mounted
            with open(os.path.join(agent_dir, "auth.json"), "w") as handle:
                handle.write("{}")
            os.makedirs(os.path.join(agent_dir, "sessions"))
            with patch.dict(os.environ, {"HOME": home}, clear=True):
                mounts = cli.get_agent_session_mounts("pi")
            self.assertIn(f"{models_file}:/home/holon/.pi/agent/models.json:ro", mounts)
            self.assertNotIn(f"{agent_dir}:/home/holon/.pi/agent:ro", mounts)
            self.assertEqual(mounts.count("-v"), 1)

    def test_non_pi_runner_warns_and_ignores_local_mode(self):
        env = {
            "HOLON_LOCAL_LLM": "1",
            "HOLON_LOCAL_BASE_URL": "http://localhost:8081/v1",
            "HOLON_LOCAL_MODELS": "qwen3:test",
        }
        with patch.object(local_llm, "prepare_agent_dir") as mock_prep:
            code, args, _ = self._run(env, agent_id="claude")
        self.assertEqual(code, 0)
        mock_prep.assert_not_called()
        self.assertNotIn(f"PI_CODING_AGENT_DIR={local_llm.CONTAINER_AGENT_DIR}", args)


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

    def test_non_pi_runners_still_require_keys_in_local_mode(self):
        """Host-local model mode is currently pi-only; claude and opencode must not bypass validation."""
        env = {
            "HOLON_LOCAL_LLM": "1",
            "HOLON_LOCAL_BASE_URL": "http://localhost:8081/v1",
            "PI_CODING_AGENT_DIR": "/home/holon/.holon-pi-agent",
        }
        for agent_name in ("claude-agent", "opencode-agent"):
            with (
                self.subTest(agent=agent_name),
                patch.dict(os.environ, env, clear=True),
                patch("os.path.exists", return_value=False),
                self.assertRaises(SystemExit),
            ):
                get_runner(agent_name).validate()

    def test_pi_build_cmd_defaults_provider_from_local_provider(self):
        env = {
            "HOLON_LOCAL_LLM": "1",
            "HOLON_LOCAL_PROVIDER": "vmlx",
            "PI_CODING_AGENT_DIR": "/home/holon/.holon-pi-agent",
        }
        with patch.dict(os.environ, env, clear=True), patch("os.path.exists", return_value=False):
            cmd = get_runner("pi-agent").build_cmd("test-model", "prompt.txt", "intent.json", "Hello")
            self.assertIn("--provider", cmd)
            self.assertIn("vmlx", cmd)

    def test_pi_build_cmd_aligns_single_provider_from_models_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            models_file = os.path.join(tmp, "models.json")
            with open(models_file, "w") as handle:
                json.dump({"providers": {"vmlx": {"baseUrl": "http://localhost:8081/v1"}}}, handle)
            env = {"HOLON_LOCAL_LLM": "1", "PI_CODING_AGENT_DIR": tmp}
            with patch.dict(os.environ, env, clear=True), patch("os.path.exists", return_value=False):
                cmd = get_runner("pi-agent").build_cmd("test-model", "prompt.txt", "intent.json", "Hello")
                self.assertIn("--provider", cmd)
                self.assertIn("vmlx", cmd)

    def test_pi_build_cmd_aligns_local_named_provider_from_models_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            models_file = os.path.join(tmp, "models.json")
            with open(models_file, "w") as handle:
                json.dump(
                    {
                        "providers": {
                            "local": {"baseUrl": "http://localhost:8081/v1"},
                            "ollama": {"baseUrl": "http://localhost:11434/v1"},
                        }
                    },
                    handle,
                )
            env = {"HOLON_LOCAL_LLM": "1", "PI_CODING_AGENT_DIR": tmp}
            with patch.dict(os.environ, env, clear=True), patch("os.path.exists", return_value=False):
                cmd = get_runner("pi-agent").build_cmd("test-model", "prompt.txt", "intent.json", "Hello")
                self.assertIn("--provider", cmd)
                self.assertIn("local", cmd)

    def test_pi_build_cmd_multiple_providers_without_local_or_env_does_not_set_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            models_file = os.path.join(tmp, "models.json")
            with open(models_file, "w") as handle:
                json.dump(
                    {
                        "providers": {
                            "vmlx": {"baseUrl": "http://localhost:8081/v1"},
                            "ollama": {"baseUrl": "http://localhost:11434/v1"},
                        }
                    },
                    handle,
                )
            env = {"HOLON_LOCAL_LLM": "1", "PI_CODING_AGENT_DIR": tmp}
            with patch.dict(os.environ, env, clear=True), patch("os.path.exists", return_value=False):
                cmd = get_runner("pi-agent").build_cmd("test-model", "prompt.txt", "intent.json", "Hello")
                self.assertNotIn("--provider", cmd)

    def test_normalize_agent_id(self):
        self.assertEqual(local_llm.normalize_agent_id("pi-agent"), "pi")
        self.assertEqual(local_llm.normalize_agent_id("agent-pi"), "pi")
        self.assertEqual(local_llm.normalize_agent_id("Pi"), "pi")
        self.assertEqual(local_llm.normalize_agent_id("claude-agent"), "claude")


def _config(*models, base_url="http://localhost:8081/v1"):
    return {"providers": {"vmlx": {"baseUrl": base_url, "apiKey": "holon-local", "models": list(models)}}}


class TestModelEnvelope(unittest.TestCase):
    """Bean 0140: explicit maxTokens and reasoning mode on every generated model entry."""

    def apply(self, config, env=None):
        return local_llm.apply_model_envelope(config, env or {})

    def test_synthesises_quarter_of_context_window(self):
        config = _config({"id": "small", "contextWindow": 32768})
        with self.assertLogs("sandbox_executor.local_llm", level="INFO") as logs:
            (envelope,) = self.apply(config)
        entry = config["providers"]["vmlx"]["models"][0]
        self.assertEqual(entry["maxTokens"], 8192)
        self.assertEqual((envelope.max_tokens, envelope.max_tokens_source), (8192, "synthesised"))
        self.assertTrue(
            any("'small'" in line and "8192" in line and "absent in host config" in line for line in logs.output)
        )

    def test_synthesis_is_capped(self):
        config = _config({"id": "big", "contextWindow": 999424})
        (envelope,) = self.apply(config)
        self.assertEqual(envelope.max_tokens, 16384)
        self.assertEqual(config["providers"]["vmlx"]["models"][0]["maxTokens"], 16384)

    def test_valid_host_value_passes_through(self):
        config = _config({"id": "m", "contextWindow": 32768, "maxTokens": 4000})
        (envelope,) = self.apply(config)
        self.assertEqual(config["providers"]["vmlx"]["models"][0]["maxTokens"], 4000)
        self.assertEqual(envelope.envelope_line(), "model=m context=32768 max_tokens=4000 source=host")

    def test_rejects_invalid_max_tokens(self):
        for bad in (0, -1, 1.5, 4096.0, "4096", True, None, 32768 - 4096 + 1):
            with self.subTest(bad=bad), self.assertRaises(local_llm.LocalLLMConfigError) as ctx:
                self.apply(_config({"id": "m", "contextWindow": 32768, "maxTokens": bad}))
            self.assertIn("'m'", str(ctx.exception))

    def test_reserve_is_configurable(self):
        config = _config({"id": "m", "contextWindow": 32768, "maxTokens": 30000})
        (envelope,) = self.apply(config, {"HOLON_CONTEXT_RESERVE_TOKENS": "1024"})
        self.assertEqual(envelope.max_tokens, 30000)

    def test_missing_context_window_uses_env_or_fails(self):
        config = _config({"id": "m"})
        (envelope,) = self.apply(config, {"HOLON_LOCAL_CONTEXT_WINDOW": "65536"})
        self.assertEqual((envelope.context_window, envelope.max_tokens), (65536, 16384))
        self.assertEqual(config["providers"]["vmlx"]["models"][0]["contextWindow"], 65536)
        with self.assertRaises(local_llm.LocalLLMConfigError) as ctx:
            self.apply(_config({"id": "m"}))
        self.assertIn("HOLON_LOCAL_CONTEXT_WINDOW", str(ctx.exception))

    def test_invalid_context_window(self):
        for bad in (0, -5, "32768", 3.5, False):
            with self.subTest(bad=bad), self.assertRaises(local_llm.LocalLLMConfigError):
                self.apply(_config({"id": "m", "contextWindow": bad}))

    def test_reasoning_mode_from_env_host_and_default(self):
        cases = [
            ({"HOLON_REASONING_MODE": "LOW"}, {"reasoning": True}, ("low", "env"), True),
            ({}, {"reasoning": "medium"}, ("medium", "host"), True),
            ({}, {"reasoning": False}, ("off", "host"), False),
            ({}, {"reasoning": True}, ("off", "default"), False),
            ({}, {}, ("off", "default"), False),
        ]
        for env, extra, expected, flag in cases:
            with self.subTest(env=env, extra=extra):
                config = _config({"id": "m", "contextWindow": 32768, **extra})
                (envelope,) = self.apply(config, env)
                self.assertEqual((envelope.reasoning, envelope.reasoning_source), expected)
                self.assertIs(config["providers"]["vmlx"]["models"][0]["reasoning"], flag)
                self.assertEqual(envelope.reasoning_line(), f"reasoning={expected[0]} source={expected[1]}")

    def test_invalid_reasoning_mode_raises(self):
        with self.assertRaises(local_llm.LocalLLMConfigError):
            self.apply(_config({"id": "m", "contextWindow": 32768}), {"HOLON_REASONING_MODE": "turbo"})
        with self.assertRaises(local_llm.LocalLLMConfigError):
            self.apply(_config({"id": "m", "contextWindow": 32768, "reasoning": "max"}))

    def test_prepare_agent_dir_writes_envelope_and_thinking_level(self):
        host_config = _config({"id": "m", "contextWindow": 32768})
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {"HOLON_REASONING_MODE": "minimal"}, clear=True),
        ):
            dest = os.path.join(tmp, "agent")
            config = local_llm.prepare_agent_dir(dest, host_config=host_config)
            with open(os.path.join(dest, "models.json")) as f:
                on_disk = json.load(f)
            with open(os.path.join(dest, "settings.json")) as f:
                settings = json.load(f)
            # Rewrite behaviour is unchanged; the envelope is added on top.
            self.assertEqual(on_disk["providers"]["vmlx"]["baseUrl"], "http://host.docker.internal:8081/v1")
            self.assertEqual(on_disk, config)
            entry = on_disk["providers"]["vmlx"]["models"][0]
            self.assertIsInstance(entry["maxTokens"], int)
            self.assertIs(entry["reasoning"], True)
            self.assertEqual(settings, {"defaultThinkingLevel": "minimal"})
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(dest, "settings.json")).st_mode), 0o644)
            self.assertEqual(local_llm.container_env(dest), {"PI_CODING_AGENT_DIR": local_llm.CONTAINER_AGENT_DIR})


# Frozen health fixtures: shapes for parser coverage only, not performance claims.
HEALTH_OFF = {"status": "ok", "prefill_cache": {"enabled": False}, "effective_defaults": {"max_output_tokens": 8192}}
HEALTH_COLD = {
    "status": "ok",
    "prefill_cache": {"enabled": True, "entries": 0, "cached_tokens": 0, "memory_bytes": 0},
    "memory": {"used_bytes": 1048576, "limit_bytes": 8589934592},
    "effective_defaults": {"max_output_tokens": 8192},
}
HEALTH_WARM = {
    "status": "ok",
    "prefill_cache": {"enabled": True, "entries": 3, "cached_tokens": 41234, "memory_bytes": 536870912},
    "memory": {"used_bytes": 2147483648, "limit_bytes": 8589934592},
    "effective_defaults": {"max_output_tokens": 4096},
}
HEALTH_SMALL_PROMPT = {
    "status": "ok",
    "prefill_cache": {"enabled": True, "entries": 0},
    "effective_defaults": {"max_output_tokens": 16384, "max_prompt_tokens": 32768},
}
HEALTH_MALFORMED = "<html>not json</html>"


def _fetcher(payload, calls=None):
    def fetch(url, timeout):
        if calls is not None:
            calls.append((url, timeout))
        return payload if isinstance(payload, str) else json.dumps(payload).encode()

    return fetch


class TestGatewayHealth(unittest.TestCase):
    def test_off_cold_warm_classification(self):
        for payload, expected in ((HEALTH_OFF, "off"), (HEALTH_COLD, "cold"), (HEALTH_WARM, "warm")):
            with self.subTest(expected=expected):
                health = local_llm.probe_gateway_health("http://localhost:8081/v1", fetch=_fetcher(payload))
                self.assertEqual(health.prefill_cache, expected)
                self.assertTrue(health.cache_line().startswith(f"prefill_cache={expected}"))

    def test_memory_figures_and_output_default(self):
        health = local_llm.probe_gateway_health("http://localhost:8081/v1", fetch=_fetcher(HEALTH_WARM))
        self.assertEqual(health.max_output_tokens, 4096)
        self.assertEqual(health.memory["memory_used_bytes"], 2147483648)
        self.assertEqual(health.memory["cache_memory_bytes"], 536870912)
        self.assertIn("memory_limit_bytes=8589934592", health.cache_line())

    def test_malformed_response_is_unknown(self):
        health = local_llm.probe_gateway_health("http://localhost:8081/v1", fetch=_fetcher(HEALTH_MALFORMED))
        self.assertEqual(health.prefill_cache, "unknown")
        self.assertEqual(local_llm.parse_gateway_health("u", ["list"]).prefill_cache, "unknown")
        self.assertEqual(local_llm.parse_gateway_health("u", {"status": "ok"}).prefill_cache, "unknown")

    def test_network_failure_is_unknown(self):
        def failing(url, timeout):
            raise OSError("connection refused")

        health = local_llm.probe_gateway_health("http://127.0.0.1:8081/v1", fetch=failing)
        self.assertEqual(health.prefill_cache, "unknown")
        self.assertIn("connection refused", health.cache_line())

    def test_malformed_http_response_is_unknown(self):
        import http.client

        for exc in (http.client.BadStatusLine("garbage"), http.client.IncompleteRead(b"{")):
            with self.subTest(exc=type(exc).__name__):

                def failing(url, timeout, exc=exc):
                    raise exc

                health = local_llm.probe_gateway_health("http://127.0.0.1:8081/v1", fetch=failing)
                self.assertEqual(health.prefill_cache, "unknown")
                self.assertIn("health probe failed", health.cache_line())

    def test_probe_targets_health_path_with_short_timeout(self):
        calls = []
        local_llm.probe_gateway_health("http://localhost:8081/v1", fetch=_fetcher(HEALTH_OFF, calls))
        self.assertEqual(calls, [("http://localhost:8081/health", 2.0)])

    def test_never_probes_remote_or_gateway_hosts(self):
        calls = []
        for url in ("https://api.example.com/v1", "http://192.168.1.20:8081/v1", "http://host.docker.internal:8081/v1"):
            with self.subTest(url=url):
                self.assertIsNone(local_llm.probe_gateway_health(url, fetch=_fetcher(HEALTH_OFF, calls)))
        self.assertEqual(calls, [])
        allowed = local_llm.probe_gateway_health(
            "http://192.168.1.20:8081/v1", fetch=_fetcher(HEALTH_OFF, calls), allow_list={"192.168.1.20"}
        )
        self.assertEqual(allowed.prefill_cache, "off")

    def test_probe_url_drops_userinfo(self):
        calls = []
        url = "http://user:s3cret@localhost:8081/v1"
        health = local_llm.probe_gateway_health(url, fetch=_fetcher(HEALTH_OFF, calls))
        self.assertEqual(calls, [("http://localhost:8081/health", 2.0)])
        self.assertNotIn("s3cret", health.url)
        self.assertEqual(local_llm.health_url("http://[::1]:8081/v1"), "http://[::1]:8081/health")

    def test_default_fetch_refuses_redirects(self):
        import http.server
        import threading

        class Redirector(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "http://198.51.100.7/health")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Redirector)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        health = local_llm.probe_gateway_health(f"http://127.0.0.1:{server.server_port}/v1")
        self.assertEqual(health.prefill_cache, "unknown")
        self.assertIn("redirect refused", health.error)

    def test_max_prompt_tokens_parsed_from_defaults_or_top_level(self):
        nested = {"effective_defaults": {"max_output_tokens": 16384, "max_prompt_tokens": 262144}}
        self.assertEqual(local_llm.parse_gateway_health("u", nested).max_prompt_tokens, 262144)
        top = {"effective_defaults": {"max_output_tokens": 16384}, "max_prompt_tokens": 32768}
        self.assertEqual(local_llm.parse_gateway_health("u", top).max_prompt_tokens, 32768)
        self.assertIsNone(local_llm.parse_gateway_health("u", HEALTH_OFF).max_prompt_tokens)
