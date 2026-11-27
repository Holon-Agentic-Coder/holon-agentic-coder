from __future__ import annotations

import argparse
import copy
import io
import json
import os
import unittest
from unittest.mock import patch

from sandbox_executor import cli, local_llm
from sandbox_executor.cli import doctor as cli_doctor

from tests.test_local_llm import HEALTH_COLD, HEALTH_OFF, HEALTH_SMALL_PROMPT, HEALTH_WARM, _config, _fetcher

_SHARED_SERVER_CONFIG = {
    "providers": {
        "a": {
            "baseUrl": "http://127.0.0.1:8000/v1",
            "models": [{"id": "big", "contextWindow": 65536, "maxTokens": 16384}],
        },
        "b": {
            "baseUrl": "http://127.0.0.1:8000/anthropic",
            "models": [{"id": "small", "contextWindow": 8192, "maxTokens": 2048}],
        },
        "c": {
            "baseUrl": "http://127.0.0.1:9000/v1",
            "models": [{"id": "other", "contextWindow": 65536, "maxTokens": 8192}],
        },
    }
}


def _strict_utf8_stream() -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict", write_through=True)


class TestDoctor(unittest.TestCase):
    def run_doctor(self, env, fetch, host_config=None):
        out = io.StringIO()
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(local_llm, "host_models_json", return_value=host_config),
        ):
            code = cli.report_budgets_and_envelope(fetch=fetch, out=out)
        return code, out.getvalue()

    def test_doctor_prints_envelope_and_cache_state(self):
        host_config = _config({"id": "qwen3", "contextWindow": 65536, "maxTokens": 8000})
        code, output = self.run_doctor({"HOLON_LOCAL_LLM": "1"}, _fetcher(HEALTH_WARM), host_config)
        self.assertEqual(code, 0)
        self.assertIn("model=qwen3 context=65536 max_tokens=8000 source=host", output)
        self.assertIn("reasoning=off source=default", output)
        self.assertIn("prefill_cache=warm", output)
        self.assertIn("WARNING: model=qwen3 max_tokens=8000 exceeds", output)
        self.assertIn("budgets turn=3600s stall=90s", output)

    def test_doctor_without_mismatch_has_no_warning(self):
        env = {
            "HOLON_LOCAL_LLM": "1",
            "HOLON_LOCAL_BASE_URL": "http://localhost:11434/v1",
            "HOLON_LOCAL_MODELS": "llama",
            "HOLON_LOCAL_CONTEXT_WINDOW": "16384",
            "HOLON_REASONING_MODE": "high",
        }
        code, output = self.run_doctor(env, _fetcher(HEALTH_COLD))
        self.assertEqual(code, 0)
        self.assertIn("model=llama context=16384 max_tokens=4096 source=synthesised", output)
        self.assertIn("reasoning=high source=env", output)
        self.assertIn("prefill_cache=cold", output)
        self.assertNotIn("WARNING", output)

    def test_doctor_warns_when_context_window_exceeds_max_prompt_tokens(self):
        host_config = _config({"id": "qwen3", "contextWindow": 65536, "maxTokens": 8000})
        code, output = self.run_doctor({"HOLON_LOCAL_LLM": "1"}, _fetcher(HEALTH_SMALL_PROMPT), host_config)
        self.assertEqual(code, 0)
        self.assertIn("WARNING: model=qwen3 context=65536 exceeds the server's max_prompt_tokens=32768", output)
        self.assertNotIn("max_output_tokens=16384", output)

    @staticmethod
    def two_server_fetch(urls):
        def fetch(url, timeout):
            urls.append(url)
            limits = (4096, 32768) if ":9000" in url else (16384, 65536)
            return json.dumps(
                {
                    "prefill_cache": {"enabled": True, "entries": 0},
                    "effective_defaults": {"max_output_tokens": limits[0], "max_prompt_tokens": limits[1]},
                }
            ).encode()

        return fetch

    def test_doctor_probes_each_server_once_and_scopes_warnings(self):
        urls = []
        code, output = self.run_doctor(
            {"HOLON_LOCAL_LLM": "1"}, self.two_server_fetch(urls), copy.deepcopy(_SHARED_SERVER_CONFIG)
        )
        self.assertEqual(code, 0)
        self.assertEqual(urls, ["http://127.0.0.1:8000/health", "http://127.0.0.1:9000/health"])
        gateway_lines = [line for line in output.splitlines() if line.startswith("gateway=")]
        self.assertEqual(
            gateway_lines,
            [
                "gateway=http://127.0.0.1:8000/health prefill_cache=cold",
                "gateway=http://127.0.0.1:9000/health prefill_cache=cold",
            ],
        )
        warnings = [line for line in output.splitlines() if line.startswith("WARNING")]
        self.assertEqual(
            warnings,
            [
                "WARNING: model=other max_tokens=8192 exceeds the server's effective_defaults.max_output_tokens=4096",
                "WARNING: model=other context=65536 exceeds the server's max_prompt_tokens=32768",
            ],
        )

    def test_unified_doctor_json_dedupes_servers_and_scopes_warnings(self):
        code, output = self.run_unified_doctor(
            {"HOLON_LOCAL_LLM": "1"},
            self.two_server_fetch([]),
            copy.deepcopy(_SHARED_SERVER_CONFIG),
            as_json=True,
        )
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(set(payload), {"agent", "model", "preflight", "environment", "configuration"})
        self.assertEqual(set(payload["environment"]), {"ok", "lines", "warnings"})
        lines = payload["environment"]["lines"]
        self.assertEqual(sum(line.startswith("gateway=http://127.0.0.1:8000/") for line in lines), 1)
        self.assertEqual(sum(line.startswith("gateway=http://127.0.0.1:9000/") for line in lines), 1)
        warnings = [line for line in lines if line.startswith("WARNING")]
        self.assertEqual(len(warnings), 2)
        self.assertTrue(all("model=other " in line for line in warnings))
        self.assertEqual(payload["environment"]["warnings"], warnings)

    def test_doctor_scopes_synthesised_provider_to_its_base_url(self):
        env = {
            "HOLON_LOCAL_LLM": "1",
            "HOLON_LOCAL_BASE_URL": "http://localhost:9000/v1",
            "HOLON_LOCAL_MODELS": "llama",
            "HOLON_LOCAL_CONTEXT_WINDOW": "65536",
        }
        urls = []
        code, output = self.run_doctor(env, self.two_server_fetch(urls))
        self.assertEqual(code, 0)
        self.assertEqual(urls, ["http://localhost:9000/health"])
        self.assertIn("WARNING: model=llama context=65536 exceeds the server's max_prompt_tokens=32768", output)

    def test_doctor_reports_config_errors(self):
        code, output = self.run_doctor({"HOLON_LOCAL_LLM": "1"}, _fetcher(HEALTH_OFF), _config({"id": "m"}))
        self.assertEqual(code, 1)
        self.assertIn("HOLON_LOCAL_CONTEXT_WINDOW", output)

    def test_doctor_with_local_mode_disabled(self):
        code, output = self.run_doctor({}, _fetcher(HEALTH_OFF))
        self.assertEqual(code, 0)
        self.assertIn("local_llm=disabled", output)

    def run_unified_doctor(self, env, fetch, host_config=None, as_json=False, status=None):
        from sandbox_executor.preflight import PreflightResult, PreflightStatus

        status = status or PreflightStatus.OK
        probe = PreflightResult(
            status=status,
            agent="antigravity-agent",
            model="m",
            image="holon/agent-antigravity",
            exit_code=0 if status is PreflightStatus.OK else 1,
            output_excerpt="",
            hint="",
        )
        args = argparse.Namespace(
            agent="antigravity-agent", model="gemini-3.8-flash-medium", json=as_json, timeout=None
        )
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(local_llm, "host_models_json", return_value=host_config),
            patch("sandbox_executor.cli.doctor.run_preflight_probe", return_value=probe),
            # why: a strict UTF-8 byte stream fails like a terminal on text that a StringIO would accept
            patch("sys.stdout", new_callable=_strict_utf8_stream) as mock_stdout,
        ):
            code = cli.run_doctor(args, fetch=fetch)
        return code, mock_stdout.buffer.getvalue().decode("utf-8")

    def test_unified_doctor_reports_resolution_probe_and_envelope(self):
        host_config = _config({"id": "qwen3", "contextWindow": 65536, "maxTokens": 8000})
        code, output = self.run_unified_doctor({"HOLON_LOCAL_LLM": "1"}, _fetcher(HEALTH_WARM), host_config)
        self.assertEqual(code, 0)
        self.assertIn("Agent runner", output)
        self.assertIn("Model", output)
        self.assertIn("budgets turn=3600s stall=90s", output)
        self.assertIn("model=qwen3 context=65536 max_tokens=8000 source=host", output)
        self.assertIn("prefill_cache=warm", output)

    def test_unified_doctor_json_carries_environment_section(self):
        code, output = self.run_unified_doctor({}, _fetcher(HEALTH_OFF), as_json=True)
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["preflight"]["status"], "ok")
        self.assertTrue(payload["environment"]["ok"])
        self.assertTrue(any(line.startswith("budgets turn=") for line in payload["environment"]["lines"]))
        self.assertIn(
            "local_llm=disabled (set HOLON_LOCAL_LLM=1 to inspect the model envelope)", payload["environment"]["lines"]
        )

    def test_unified_doctor_fails_on_envelope_config_error_even_when_probe_passes(self):
        code, output = self.run_unified_doctor({"HOLON_LOCAL_LLM": "1"}, _fetcher(HEALTH_OFF), _config({"id": "m"}))
        self.assertEqual(code, 1)
        self.assertIn("HOLON_LOCAL_CONTEXT_WINDOW", output)

    def test_doctor_never_prints_base_url_userinfo(self):
        # why: #96 shows only a host validated after removing the userinfo; otherwise only the variable is named
        # why: credentials are built at runtime, not literals; the bad hosts cover the "invalid port" warning
        sample = "pw-" + "z" * 8
        head = "hd-" + "q" * 5
        name = "some" + "one"
        hosts = ("gpu.example.com:8000/v1", "gpu.example.com:abc/v1", "h\u00f4st.example/v1")
        withheld = ("host withheld", "is not a valid URL")

        def assert_shown(text, host, sep):
            # why: a "/", "?" or "#" before the last "@" leaves no parseable authority, so the host is withheld
            if host == "gpu.example.com:8000/v1" and sep in ("", "@"):
                self.assertIn("(host gpu.example.com:8000)", text)
            else:
                self.assertTrue(any(marker in text for marker in withheld))
            self.assertNotIn(f"***@{host}", text)

        for sep, host in ((sep, host) for sep in ("", "/", "?", "#", "@") for host in hosts):
            secret = f"{head}{sep}{sample}" if sep else sample
            env = {
                "HOLON_LOCAL_LLM": "1",
                "HOLON_LOCAL_BASE_URL": f"http://{name}:{secret}@{host}?key={sample}",
                "HOLON_LOCAL_MODELS": "m",
            }
            with self.subTest(sep=sep, host=host):
                code, output = self.run_doctor(env, _fetcher(HEALTH_OFF))
                self.assertEqual(code, 1)
                assert_shown(output, host, sep)
                for fragment in (sample, head, name):
                    self.assertNotIn(fragment, output)
            for as_json in (False, True):
                with self.subTest(sep=sep, host=host, as_json=as_json):
                    code, output = self.run_unified_doctor(env, _fetcher(HEALTH_OFF), as_json=as_json)
                    self.assertEqual(code, 1)
                    for fragment in (sample, head, name):
                        self.assertNotIn(fragment, output)
                    if as_json:
                        assert_shown("\n".join(json.loads(output)["environment"]["lines"]), host, sep)

    def test_unified_doctor_rejects_non_utf8_model_ids(self):
        # why: a non-UTF-8 byte in HOLON_LOCAL_MODELS (surrogateescape) used to crash the text report
        env = {
            "HOLON_LOCAL_LLM": "1",
            "HOLON_LOCAL_BASE_URL": "http://127.0.0.1:1/v1",
            "HOLON_LOCAL_CONTEXT_WINDOW": "32768",
            "HOLON_LOCAL_MODELS": "m" + os.fsdecode(b"\xff"),
        }
        for as_json in (False, True):
            with self.subTest(as_json=as_json):
                code, output = self.run_unified_doctor(env, _fetcher(HEALTH_OFF), as_json=as_json)
                self.assertEqual(code, 1)
                self.assertIn("Error: HOLON_LOCAL_MODELS is not valid UTF-8 text", output)

    def test_unified_doctor_text_report_cannot_fail_to_encode(self):
        def report(fetch=None, out=None):
            print("model=bad" + chr(0xD800) + "id", file=out)
            return 0

        with patch.object(cli_doctor, "report_budgets_and_envelope", side_effect=report):
            code, output = self.run_unified_doctor({}, _fetcher(HEALTH_OFF))
        self.assertEqual(code, 0)
        self.assertIn("model=bad\\ud800id", output)

    def test_print_encodable_leaves_encodable_text_unchanged(self):
        stream = _strict_utf8_stream()
        cli.print_encodable("h\u00f4st ok", file=stream)
        self.assertEqual(stream.buffer.getvalue().decode("utf-8"), "h\u00f4st ok\n")
        ascii_stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii", errors="strict", write_through=True)
        cli.print_encodable("h\u00f4st", file=ascii_stream)
        self.assertEqual(ascii_stream.buffer.getvalue().decode("ascii"), "h\\xf4st\n")

    def test_unified_doctor_fails_when_probe_fails(self):
        from sandbox_executor.preflight import PreflightStatus

        code, output = self.run_unified_doctor({}, _fetcher(HEALTH_OFF), status=PreflightStatus.EXPIRED_KEY)
        self.assertEqual(code, 1)
        self.assertIn("budgets turn=", output)
