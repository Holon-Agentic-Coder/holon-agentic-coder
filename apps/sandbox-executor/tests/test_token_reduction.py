"""Unit tests for AI Agent Token Reduction Architecture - Phase 1."""

import importlib.util
import json
import logging
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

_AB_RUNNER_PATH = Path(__file__).resolve().parents[3] / "scripts" / "ab_measure_all_methods.py"


def _load_ab_runner() -> ModuleType:
    """Load the A/B benchmark runner script from ``scripts/`` as a module."""
    spec = importlib.util.spec_from_file_location("ab_measure_all_methods", _AB_RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _default_wire_log_dir_in_tmp(tmp_path, monkeypatch):
    """Point the addon's default wire-log directory (relative to the cwd, the repository root) at this test."""
    from sandbox_executor.token_reduction import mitm_addon

    monkeypatch.setattr(mitm_addon, "WIRE_LOG_DIR", str(tmp_path / "default-wire-logs"))


def test_hybrid_cache(tmp_path):
    from sandbox_executor.token_reduction.hybrid_cache import HybridCacheStore

    cache = HybridCacheStore(cache_dir=str(tmp_path), similarity_threshold=0.8)

    req_1 = {
        "system": "You are a helpful assistant.",
        "messages": [{"role": "user", "content": "Fix bug in 2026-08-28T18:00:00Z task-1234"}],
    }
    resp_1 = {"result": "Fixed bug in task-1234"}

    cache.put(req_1, resp_1, provider="anthropic")

    # Exact lookup after normalization (timestamp & task ID stripped)
    req_2 = {
        "system": "You are a helpful assistant.",
        "messages": [{"role": "user", "content": "Fix bug in 2026-08-28T19:30:00Z task-5678"}],
    }
    cached_resp = cache.get(req_2, provider="anthropic")
    assert cached_resp is not None
    assert cached_resp["result"] == "Fixed bug in task-1234"


def test_hybrid_cache_dissimilar_user_prompts(tmp_path):
    from sandbox_executor.token_reduction.hybrid_cache import HybridCacheStore

    cache = HybridCacheStore(cache_dir=str(tmp_path), similarity_threshold=0.85)
    sys_prompt = "You are a helpful coding assistant."
    req_1 = {"system": sys_prompt, "messages": [{"role": "user", "content": "Fix bug in authentication module"}]}
    req_2 = {"system": sys_prompt, "messages": [{"role": "user", "content": "Delete production database records"}]}
    cache.put(req_1, {"result": "Bug fixed"}, provider="anthropic")
    assert cache.get(req_2, provider="anthropic") is None


def test_mitm_interceptor(tmp_path):
    from sandbox_executor.token_reduction.mitm_addon import MITMProxyInterceptor

    interceptor = MITMProxyInterceptor(cache_dir=str(tmp_path), enable_caching=True)
    endpoint = "https://api.anthropic.com/v1/messages"

    req_json = {
        "system": "System prompt",
        "messages": [{"role": "user", "content": "Hello LLM"}],
    }

    # Initial request -> Cache miss
    cleaned_req, cached_resp = interceptor.intercept_request(endpoint, req_json)
    assert cached_resp is None
    assert isinstance(cleaned_req["system"], list)

    # Store response
    fake_resp = {"id": "msg_123", "content": [{"type": "text", "text": "Hello human"}]}
    interceptor.intercept_response(endpoint, cleaned_req, fake_resp)

    # Subsequent identical request -> Cache hit
    _, cached_hit = interceptor.intercept_request(endpoint, req_json)
    assert cached_hit is not None
    assert cached_hit["id"] == "msg_123"


def test_mitm_interceptor_caching_disabled(tmp_path):
    from sandbox_executor.token_reduction.mitm_addon import MITMProxyInterceptor

    cache_dir = tmp_path / "should_not_be_created"
    interceptor = MITMProxyInterceptor(cache_dir=str(cache_dir), enable_caching=False)
    assert interceptor._cache_store is None
    assert not cache_dir.exists()

    endpoint = "https://api.anthropic.com/v1/messages"
    req_json = {"system": "System prompt", "messages": [{"role": "user", "content": "Hello LLM"}]}
    _cleaned_req, cached_resp = interceptor.intercept_request(endpoint, req_json)
    assert cached_resp is None
    assert interceptor._cache_store is None
    assert not cache_dir.exists()


def test_mitm_addon_lifecycle_and_hit_count(tmp_path):
    import sqlite3

    from sandbox_executor.token_reduction.mitm_addon import MitmproxyAddon

    cache_dir = tmp_path / "cache"
    addon = MitmproxyAddon()
    addon.interceptor.cache_dir = str(cache_dir)

    class FakeRequest:
        def __init__(self, url, text):
            self.pretty_url = url
            self._text = text

        def get_text(self):
            return self._text

        def set_text(self, text):
            self._text = text

    class FakeResponse:
        def __init__(self, status_code=200, text=""):
            self.status_code = status_code
            self._text = text

        def get_text(self):
            return self._text

        @classmethod
        def make(cls, status_code, content, headers=None):
            return cls(status_code=status_code, text=content.decode("utf-8") if isinstance(content, bytes) else content)

    class FakeFlow:
        def __init__(self, request, response=None):
            self.request = request
            self.response = response
            self.Response = FakeResponse

    url = "https://api.anthropic.com/v1/messages"
    req_body = json.dumps({"system": "System prompt", "messages": [{"role": "user", "content": "Test mitm flow"}]})

    # Flow 1: Initial request (cache miss) -> backend response -> response callback stores in cache
    flow1 = FakeFlow(FakeRequest(url, req_body), FakeResponse(200, json.dumps({"id": "resp_1"})))
    addon.request(flow1)
    assert getattr(flow1, "is_cached", False) is False

    addon.response(flow1)

    # Check cache table hit_count (should be 0)
    db_path = cache_dir / "llm_cache.db"
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT hit_count FROM prompt_cache").fetchone()
        assert row[0] == 0

    # Flow 2: Subsequent request (cache hit)
    flow2 = FakeFlow(FakeRequest(url, req_body))
    addon.request(flow2)
    assert getattr(flow2, "is_cached", False) is True
    assert flow2.response is not None
    assert json.loads(flow2.response.get_text()) == {"id": "resp_1"}

    # Call response callback on cached flow -> should not re-put or reset hit_count to 0
    addon.response(flow2)

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT hit_count FROM prompt_cache").fetchone()
        assert row[0] == 1


def test_hybrid_cache_atomic_hit_count_and_system_prompt_normalization(tmp_path):
    import sqlite3

    from sandbox_executor.token_reduction.hybrid_cache import HybridCacheStore

    cache = HybridCacheStore(cache_dir=str(tmp_path), similarity_threshold=0.8)

    req_1 = {
        "system": "You are assistant at 2026-08-30T00:00:00Z with task-100",
        "messages": [
            {
                "role": "user",
                "content": "word1 word2 word3 word4 word5 word6 word7 word8 word9 test",
            }
        ],
    }
    resp_1 = {"output": "Refactored auth"}
    cache.put(req_1, resp_1, provider="anthropic")

    # 1. Exact match (with normalized timestamps/task IDs)
    req_exact = {
        "system": "You are assistant at 2026-08-30T01:00:00Z with task-200",
        "messages": [
            {
                "role": "user",
                "content": "word1 word2 word3 word4 word5 word6 word7 word8 word9 test",
            }
        ],
    }
    resp = cache.get(req_exact, provider="anthropic")
    assert resp == resp_1

    # 2. Semantic match with transient tokens in system prompt (9 shared out of 11 union = 0.818 >= 0.8)
    req_semantic = {
        "system": "You are assistant at 2026-08-30T02:00:00Z with task-300",
        "messages": [
            {
                "role": "user",
                "content": "word1 word2 word3 word4 word5 word6 word7 word8 word9 check",
            }
        ],
    }
    resp_sem = cache.get(req_semantic, provider="anthropic")
    assert resp_sem == resp_1

    # Verify hit_count incremented to 2 (1 exact hit + 1 semantic hit)
    db_path = tmp_path / "llm_cache.db"
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT hit_count FROM prompt_cache").fetchone()
        assert row[0] == 2


def test_hybrid_cache_openai_system_prompt_isolation(tmp_path):
    from sandbox_executor.token_reduction.hybrid_cache import HybridCacheStore

    cache = HybridCacheStore(cache_dir=str(tmp_path), similarity_threshold=0.8)

    stored_openai_req = {
        "messages": [
            {"role": "system", "content": "You are a specialized code review bot."},
            {"role": "user", "content": "Analyze performance and optimization of database queries in repository"},
        ]
    }
    stored_resp = {"choices": [{"message": {"content": "DB queries look optimized."}}]}

    cache.put(stored_openai_req, stored_resp, provider="openai")

    # 1. Query with different OpenAI system prompt but identical user message -> Cache miss
    diff_sys_req = {
        "messages": [
            {"role": "system", "content": "You are a code formatter bot."},
            {"role": "user", "content": "Analyze performance and optimization of database queries in repository"},
        ]
    }
    assert cache.get(diff_sys_req, provider="openai") is None

    # 2. Query with same OpenAI system prompt and high token overlap user message -> Cache hit
    same_sys_req = {
        "messages": [
            {"role": "system", "content": "You are a specialized code review bot."},
            {"role": "user", "content": "Analyze performance and efficiency of database queries in repository"},
        ]
    }
    hit_resp = cache.get(same_sys_req, provider="openai")
    assert hit_resp is not None
    assert hit_resp["choices"][0]["message"]["content"] == "DB queries look optimized."


def test_mitm_addon_null_response_flow():
    from sandbox_executor.token_reduction.mitm_addon import MitmproxyAddon

    addon = MitmproxyAddon()

    class FakeFlowNullResponse:
        def __init__(self):
            self.request = MagicMock(pretty_url="https://api.openai.com/v1/chat/completions")
            self.response = None
            self.is_cached = False

    flow = FakeFlowNullResponse()
    # Call response on flow with response=None should return cleanly without raising AttributeError
    addon.response(flow)


def test_hybrid_cache_cross_provider_exact_match_isolation(tmp_path):
    from sandbox_executor.token_reduction.hybrid_cache import HybridCacheStore

    cache = HybridCacheStore(cache_dir=str(tmp_path), similarity_threshold=0.8)

    payload = {
        "system": "You are a coding assistant.",
        "messages": [{"role": "user", "content": "Write a python function to compute fibonacci numbers."}],
    }
    response = {"content": "def fib(n): return n if n <= 1 else fib(n-1) + fib(n-2)"}

    # Store payload under anthropic provider
    cache.put(payload, response, provider="anthropic")

    # Exact lookup for anthropic returns the cached response
    assert cache.get(payload, provider="anthropic") == response

    # Exact lookup for openai with identical payload returns None due to provider isolation
    assert cache.get(payload, provider="openai") is None


def test_mitm_addon_null_request_flow():
    from sandbox_executor.token_reduction.mitm_addon import MitmproxyAddon

    addon = MitmproxyAddon()

    class FakeFlowNullRequest:
        def __init__(self):
            self.request = None
            self.response = None
            self.is_cached = False

    flow = FakeFlowNullRequest()
    # Call request and response callbacks on flow with request=None should return cleanly
    addon.request(flow)
    addon.response(flow)


def test_mitm_addon_error_response_caching_bypass(tmp_path, caplog):
    from sandbox_executor.token_reduction.mitm_addon import MitmproxyAddon, MITMProxyInterceptor

    cache_dir = tmp_path / "cache"
    interceptor = MITMProxyInterceptor(cache_dir=str(cache_dir), enable_caching=True)
    endpoint = "https://api.anthropic.com/v1/messages"
    req_json = {"system": "System prompt", "messages": [{"role": "user", "content": "Trigger error test"}]}

    # 1. Non-200 HTTP status code should NOT be stored by intercept_response
    err_resp_500 = {"error": {"type": "api_error", "message": "Internal Server Error"}}
    interceptor.intercept_response(endpoint, req_json, err_resp_500, status_code=500)
    _, cached_500 = interceptor.intercept_request(endpoint, req_json)
    assert cached_500 is None

    # 2. HTTP 200 with top-level "error" payload should NOT be stored
    err_resp_200 = {"error": {"type": "rate_limit_error", "message": "Rate limit exceeded"}}
    interceptor.intercept_response(endpoint, req_json, err_resp_200, status_code=200)
    _, cached_200 = interceptor.intercept_request(endpoint, req_json)
    assert cached_200 is None

    # 3. HTTP 200 with type == "error" payload should NOT be stored
    type_err_resp = {"type": "error", "error": {"type": "invalid_request_error", "message": "Bad request"}}
    interceptor.intercept_response(endpoint, req_json, type_err_resp, status_code=200)
    _, cached_type_err = interceptor.intercept_request(endpoint, req_json)
    assert cached_type_err is None

    # 4. Test MitmproxyAddon callback skips non-200 flow
    addon = MitmproxyAddon()
    addon.interceptor.cache_dir = str(cache_dir)

    class FakeRequest:
        pretty_url = endpoint

        def get_text(self):
            return json.dumps(req_json)

        def set_text(self, text):
            pass

    class FakeResponse:
        status_code = 429

        def get_text(self):
            return json.dumps({"error": "Rate limit exceeded"})

    class FakeFlow:
        request = FakeRequest()
        response = FakeResponse()

    flow = FakeFlow()
    with caplog.at_level(logging.INFO):
        addon.request(flow)
        addon.response(flow)
    _, cached_flow = interceptor.intercept_request(endpoint, req_json)
    assert cached_flow is None

    telemetry_logs = [record.message for record in caplog.records if "⚠️ [TELEMETRY]" in record.message]
    assert len(telemetry_logs) == 1
    assert "Provider: ANTHROPIC" in telemetry_logs[0]
    assert "Status: 429" in telemetry_logs[0]


def test_hybrid_cache_anthropic_tool_result_content_block_extraction(tmp_path):
    from sandbox_executor.token_reduction.hybrid_cache import HybridCacheStore

    cache = HybridCacheStore(cache_dir=str(tmp_path), similarity_threshold=0.8)
    sys_prompt = "You are a coding assistant with tool support."

    req_1 = {
        "system": sys_prompt,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",
                        "content": [
                            {
                                "type": "text",
                                "text": ("Successfully compiled binary target main without any compilation warnings"),
                            }
                        ],
                    }
                ],
            }
        ],
    }
    resp_1 = {"content": [{"type": "text", "text": "Compilation completed successfully."}]}
    cache.put(req_1, resp_1, provider="anthropic")

    # Query with semantically similar tool_result text content
    req_2 = {
        "system": sys_prompt,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_456",
                        "content": "Successfully compiled binary target main without any build warnings",
                    }
                ],
            }
        ],
    }
    cached = cache.get(req_2, provider="anthropic")
    assert cached is not None
    assert cached["content"][0]["text"] == "Compilation completed successfully."


def test_hybrid_cache_multi_turn_recent_instruction_semantic_matching(tmp_path):
    from sandbox_executor.token_reduction.hybrid_cache import HybridCacheStore

    cache = HybridCacheStore(cache_dir=str(tmp_path), similarity_threshold=0.85)
    sys_prompt = "You are a coding agent working on repo refactoring."

    # Turn 9 in a long conversation history (turns 1..8 share setup words)
    long_history_turn9 = [
        {"role": "user", "content": "Turn 1: Initialize project setup and environment verification"},
        {"role": "assistant", "content": "Turn 1 done"},
        {"role": "user", "content": "Turn 2: Read directory structure and source files"},
        {"role": "assistant", "content": "Turn 2 done"},
        {"role": "user", "content": "Turn 3: Run preliminary linter and static check"},
        {"role": "assistant", "content": "Turn 3 done"},
        {"role": "user", "content": "Turn 9: Refactor authentication middleware in auth.py"},
    ]
    req_turn9 = {"system": sys_prompt, "messages": long_history_turn9}
    resp_turn9 = {"content": [{"type": "text", "text": "Auth middleware refactored."}]}
    cache.put(req_turn9, resp_turn9, provider="anthropic")

    # Turn 10 with long history (turns 1..9) but a completely DIFFERENT instruction in turn 10:
    long_history_turn10 = [
        *long_history_turn9,
        {"role": "assistant", "content": "Turn 9 done"},
        {"role": "user", "content": "Turn 10: Delete temporary log files from output build directory"},
    ]
    req_turn10_diff = {"system": sys_prompt, "messages": long_history_turn10}

    # Should NOT hit Turn 9 cache entry because Turn 10 instruction is different!
    assert cache.get(req_turn10_diff, provider="anthropic") is None

    # Query Turn 10 with semantically SIMILAR instruction for turn 10:
    long_history_turn10_similar = [
        *long_history_turn9,
        {"role": "assistant", "content": "Turn 9 done"},
        {"role": "user", "content": "Turn 10: Delete temporary log files from build output folder"},
    ]
    req_turn10_sim = {"system": sys_prompt, "messages": long_history_turn10_similar}

    # Put Turn 10 response into cache
    resp_turn10 = {"content": [{"type": "text", "text": "Log files deleted."}]}
    cache.put(req_turn10_diff, resp_turn10, provider="anthropic")

    # Querying with semantically similar turn 10 should hit Turn 10 cache
    cached_turn10 = cache.get(req_turn10_sim, provider="anthropic")
    assert cached_turn10 is not None
    assert cached_turn10["content"][0]["text"] == "Log files deleted."


def test_mitm_addon_response_make_import_fallback(monkeypatch):
    from sandbox_executor.token_reduction import mitm_addon
    from sandbox_executor.token_reduction.mitm_addon import MitmproxyAddon

    addon = MitmproxyAddon()

    url = "https://api.anthropic.com/v1/messages"
    req_payload = {"system": "Sys", "messages": [{"role": "user", "content": "Hi"}]}
    cached_payload = {"id": "cached_resp_123"}
    monkeypatch.setattr(addon.interceptor, "intercept_request", lambda u, d: (d, cached_payload))

    class MockHttpResponse:
        @classmethod
        def make(cls, status_code, content, headers):
            return {
                "mock_http": True,
                "status_code": status_code,
                "content": json.loads(content.decode("utf-8")),
                "headers": headers,
            }

    class MockHttpModule:
        Response = MockHttpResponse

    monkeypatch.setattr(mitm_addon, "http", MockHttpModule)

    class FakeRequest:
        pretty_url = url

        def get_text(self):
            return json.dumps(req_payload)

        def set_text(self, text):
            pass

    class FakeFlowWithoutResponseAttr:
        request = FakeRequest()
        response = None
        is_cached = False

    flow_a = FakeFlowWithoutResponseAttr()
    addon.request(flow_a)
    assert getattr(flow_a, "is_cached", False) is True
    assert flow_a.response == {
        "mock_http": True,
        "status_code": 200,
        "content": cached_payload,
        "headers": {"Content-Type": "application/json"},
    }

    monkeypatch.setattr(mitm_addon, "http", None)

    class FakeFlowWithResponseAttr:
        request = FakeRequest()
        response = None
        is_cached = False

        class Response:
            @classmethod
            def make(cls, status_code, content, headers):
                return {
                    "mock_flow_response": True,
                    "status_code": status_code,
                    "content": json.loads(content.decode("utf-8")),
                }

    flow_b = FakeFlowWithResponseAttr()
    addon.request(flow_b)
    assert getattr(flow_b, "is_cached", False) is True
    assert flow_b.response == {
        "mock_flow_response": True,
        "status_code": 200,
        "content": cached_payload,
    }


def test_hybrid_cache_put_upsert_preserves_hit_count(tmp_path):
    import sqlite3

    from sandbox_executor.token_reduction.hybrid_cache import HybridCacheStore

    cache = HybridCacheStore(cache_dir=str(tmp_path))
    req = {
        "system": "System prompt",
        "messages": [{"role": "user", "content": "Test hit count preservation"}],
    }
    resp1 = {"output": "Initial response"}
    cache.put(req, resp1, provider="anthropic")

    cache.get(req, provider="anthropic")
    cache.get(req, provider="anthropic")

    db_path = tmp_path / "llm_cache.db"
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT hit_count, response_json FROM prompt_cache").fetchone()
        assert row[0] == 2
        assert "Initial response" in row[1]

    resp2 = {"output": "Updated response"}
    cache.put(req, resp2, provider="anthropic")

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT hit_count, response_json FROM prompt_cache").fetchone()
        assert row[0] == 2
        assert "Updated response" in row[1]


def test_rag_indexer(tmp_path):
    from sandbox_executor.token_reduction.rag_indexer import RAGCodebaseIndexer

    temp_dir = str(tmp_path)
    py_file = os.path.join(temp_dir, "sample.py")
    with open(py_file, "w") as f:
        f.write("class DatabaseConnection:\n    def connect(self):\n        pass\n")

    indexer = RAGCodebaseIndexer(root_dir=temp_dir)

    # Test AST symbol lookup
    syms = indexer.graph_find_symbol("DatabaseConnection")
    assert len(syms) == 1
    assert syms[0]["file"] == "sample.py"
    assert syms[0]["type"] == "class"

    # Test Keyword Search
    results = indexer.semantic_search("connect")
    assert len(results) >= 1
    assert results[0]["file"] == "sample.py"

    # Test Context Bootstrap
    bootstrap = indexer.build_context_bootstrap(query="connect")
    assert "DatabaseConnection" in bootstrap


def test_openbrain_memory(tmp_path):
    from sandbox_executor.token_reduction.openbrain_memory import OpenBrainMemory

    temp_dir = str(tmp_path)
    ob = OpenBrainMemory(db_dir=temp_dir)

    mem_id = ob.store_memory(
        topic="pytest",
        content="Use -m 'not integration_test' for fast unit testing",
        category="lesson_learned",
    )
    assert mem_id > 0

    memories = ob.fetch_memories(topic="pytest")
    assert len(memories) == 1
    assert memories[0]["topic"] == "pytest"

    formatted = ob.format_memory_context(topic="pytest")
    assert "OpenBrain Episodic Memories" in formatted
    assert "not integration_test" in formatted


def test_ringer_orchestrator():
    from sandbox_executor.token_reduction.ringer_orchestrator import RingerOrchestrator

    orchestrator = RingerOrchestrator(architect_model="claude-3-5-sonnet", executor_model="gemini-3.5-flash")

    subtask = orchestrator.plan_subtask(task_id="t1", description="Run linting", commands=["ruff check ."])
    assert subtask["assigned_model"] == "gemini-3.5-flash"

    outcome = orchestrator.record_execution_outcome(
        task_id="t1",
        success=True,
        raw_output="All checks passed!\nLine 2\nLine 3\nLine 4\nLine 5\nDone.",
    )
    assert outcome.success is True
    assert "[Subtask t1 SUCCESS" in outcome.summary

    summary = orchestrator.build_architect_summary()
    assert "Ringer Executor Subtask Results Summary" in summary
    assert "t1" in summary


def test_mitm_addon_telemetry_headers(tmp_path, monkeypatch, caplog):
    from sandbox_executor.token_reduction.mitm_addon import MitmproxyAddon, time

    addon = MitmproxyAddon()
    addon.interceptor.cache_dir = str(tmp_path / "cache")

    class FakeRequest:
        def __init__(self, url, text):
            self.pretty_url = url
            self._text = text

        def get_text(self):
            return self._text

        def set_text(self, text):
            self._text = text

    class FakeResponse:
        def __init__(self, status_code=200, text=""):
            self.status_code = status_code
            self._text = text
            self.headers = {}

        def get_text(self):
            return self._text

        @classmethod
        def make(cls, status_code, content, headers=None):
            resp = cls(status_code=status_code, text=content.decode("utf-8") if isinstance(content, bytes) else content)
            resp.headers = dict(headers) if headers else {}
            return resp

    class FakeFlow:
        def __init__(self, request, response=None):
            self.request = request
            self.response = response
            self.Response = FakeResponse
            self.is_cached = False

    # Mock time.perf_counter to return deterministic timestamps
    timestamps = [10.0, 12.5, 15.0]
    ts_iter = iter(timestamps)
    monkeypatch.setattr(time, "perf_counter", lambda: next(ts_iter))

    # Let's test Anthropic cache miss
    url = "https://api.anthropic.com/v1/messages"
    req_body = {"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "Hi LLM"}]}
    resp_body = {
        "content": [{"type": "text", "text": "Hello human"}],
        "usage": {"input_tokens": 100, "cache_read_input_tokens": 60, "output_tokens": 50},
    }

    with caplog.at_level(logging.INFO):
        flow = FakeFlow(FakeRequest(url, json.dumps(req_body)), FakeResponse(200, json.dumps(resp_body)))

        addon.request(flow)
        addon.responseheaders(flow)
        addon.response(flow)

        assert flow.response.headers["X-Holon-Cache-Hit-Rate"] == "0.0000"
        assert flow.response.headers["X-Holon-TTFT-Ms"] == "2500.00"
        assert flow.response.headers["X-Holon-Prefill-TPS"] == "64.0000"  # (100 + 60) tokens / 2.5s = 64.0
        assert flow.response.headers["X-Holon-Tail-Prefill-TPS"] == "40.0000"  # 100 uncached tokens / 2.5s = 40.0
        assert flow.response.headers["X-Holon-Decode-Time-Sec"] == "2.500"
        assert flow.response.headers["X-Holon-Output-TPS"] == "20.0000"  # 50 tokens / 2.5s = 20.0
        assert flow.response.headers["X-Holon-Total-Time-Ms"] == "5000.00"

        # Now let's test a Cache Hit flow (which will be the 2nd request)
        ts_iter = iter([20.0, 25.0])

        flow_hit = FakeFlow(FakeRequest(url, json.dumps(req_body)))
        addon.request(flow_hit)

    assert flow_hit.is_cached is True
    assert flow_hit.response.headers["X-Holon-Cache-Hit-Rate"] == "0.5000"  # 1 hit / 2 requests
    assert flow_hit.response.headers["X-Holon-TTFT-Ms"] == "0.00"
    assert flow_hit.response.headers["X-Holon-Prefill-TPS"] == "0.0000"
    assert flow_hit.response.headers["X-Holon-Tail-Prefill-TPS"] == "0.0000"
    assert flow_hit.response.headers["X-Holon-Decode-Time-Sec"] == "0.000"
    assert flow_hit.response.headers["X-Holon-Output-TPS"] == "0.0000"
    assert flow_hit.response.headers["X-Holon-Total-Time-Ms"] == "0.00"

    hit_logs = [record.message for record in caplog.records if "Cache: HIT" in record.message]
    assert len(hit_logs) == 1
    assert "Provider: ANTHROPIC" in hit_logs[0]
    assert "Cache: HIT (Hit Rate: 50.0%)" in hit_logs[0]


def test_mitm_addon_telemetry_providers_and_fallback(tmp_path, monkeypatch):
    from sandbox_executor.token_reduction.mitm_addon import MitmproxyAddon, time

    addon = MitmproxyAddon()
    addon.interceptor.cache_dir = str(tmp_path / "cache")

    class FakeRequest:
        def __init__(self, url, text):
            self.pretty_url = url
            self._text = text

        def get_text(self):
            return self._text

    class FakeResponse:
        def __init__(self, text):
            self.status_code = 200
            self._text = text
            self.headers = {}

        def get_text(self):
            return self._text

    class FakeFlow:
        def __init__(self, request, response):
            self.request = request
            self.response = response
            self.is_cached = False

    # 1. OpenAI provider check
    ts_iter = iter([10.0, 12.0, 14.0])
    monkeypatch.setattr(time, "perf_counter", lambda: next(ts_iter))

    flow_openai = FakeFlow(
        FakeRequest("https://api.openai.com/v1/chat/completions", json.dumps({"messages": []})),
        FakeResponse(json.dumps({"usage": {"prompt_tokens": 8, "completion_tokens": 6}})),
    )
    addon.request(flow_openai)
    addon.responseheaders(flow_openai)
    addon.response(flow_openai)

    assert flow_openai.response.headers["X-Holon-Prefill-TPS"] == "4.0000"
    assert flow_openai.response.headers["X-Holon-Output-TPS"] == "3.0000"

    # 2. Gemini provider check
    ts_iter = iter([10.0, 12.0, 14.0])
    flow_gemini = FakeFlow(
        FakeRequest("https://generativelanguage.googleapis.com/v1beta/models/gemini", json.dumps({"contents": []})),
        FakeResponse(json.dumps({"usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 8}})),
    )
    addon.request(flow_gemini)
    addon.responseheaders(flow_gemini)
    addon.response(flow_gemini)

    assert flow_gemini.response.headers["X-Holon-Prefill-TPS"] == "5.0000"
    assert flow_gemini.response.headers["X-Holon-Output-TPS"] == "4.0000"

    # 3. Fallback character-based estimation check
    ts_iter = iter([10.0, 12.0, 14.0])
    req_body = {"messages": [{"content": "A" * 40}]}
    resp_body = {"choices": [{"message": {"content": "B" * 24}}]}

    flow_fallback = FakeFlow(
        FakeRequest("https://api.openai.com/v1/chat/completions", json.dumps(req_body)),
        FakeResponse(json.dumps(resp_body)),
    )
    addon.request(flow_fallback)
    addon.responseheaders(flow_fallback)
    addon.response(flow_fallback)

    assert flow_fallback.response.headers["X-Holon-Prefill-TPS"] == "5.0000"
    assert flow_fallback.response.headers["X-Holon-Output-TPS"] == "3.0000"


def test_estimate_chars_system_and_prompt_keys():
    from sandbox_executor.token_reduction.mitm_addon import estimate_chars

    # Test system key present along with non-target fields
    data_system = {"system": "System instructions here", "custom_meta": "extra metadata"}
    assert estimate_chars(data_system) == len("System instructions here")

    # Test prompt key
    data_prompt = {"prompt": "Hello world prompt"}
    assert estimate_chars(data_prompt) == len("Hello world prompt")

    # Test max_depth guard
    nested_data = {"a": {"b": {"c": "deep string"}}}
    assert estimate_chars(nested_data, max_depth=3) == 0
    assert estimate_chars(nested_data, max_depth=4) == len("deep string")


def test_extract_token_counts_anthropic_prompt_caching():
    from sandbox_executor.token_reduction.mitm_addon import extract_token_counts

    req_data = {"messages": [{"role": "user", "content": "hello"}]}
    resp_data = {
        "usage": {
            "input_tokens": 100,
            "cache_read_input_tokens": 500,
            "cache_creation_input_tokens": 200,
            "output_tokens": 50,
        }
    }
    input_tokens, output_tokens, cache_read_tokens = extract_token_counts(req_data, resp_data, provider="anthropic")
    assert input_tokens == 800
    assert output_tokens == 50
    assert cache_read_tokens == 500


def test_mitm_interceptor_generic_googleapis_unaffected():
    from sandbox_executor.token_reduction.mitm_addon import MITMProxyInterceptor
    from sandbox_executor.token_reduction.payload_cleaner import JSONContextCleaner

    interceptor = MITMProxyInterceptor(enable_caching=False)

    # 1. Verify generic Google API URLs return "unknown"
    urls_to_test = [
        "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
        "https://daily-cloudcode-pa.googleapis.com/v1internal:setUserSettings",
        "https://daily-cloudcode-pa.googleapis.com/v1internal:listExperiments",
        "https://www.googleapis.com/oauth2/v2/userinfo",
        "https://play.googleapis.com/log",
    ]
    sample_payload = {"project": "test-proj", "setting": True}

    for url in urls_to_test:
        assert interceptor.detect_provider(url, sample_payload) == "unknown"
        cleaned_req, cached = interceptor.intercept_request(url, sample_payload)
        assert cached is None
        assert cleaned_req == sample_payload
        assert "contents" not in cleaned_req

    # 2. Verify _clean_gemini does not inject "contents": [] when missing
    cleaner = JSONContextCleaner()
    cleaned = cleaner.process_payload(sample_payload, provider="gemini")
    assert "contents" not in cleaned
    assert cleaned == sample_payload

    # 3. Verify payload with contents set to None returns payload unchanged
    none_payload = {"contents": None}
    assert cleaner.process_payload(none_payload, provider="gemini") == none_payload


def test_detect_provider_schemeless_urls():
    from sandbox_executor.token_reduction.mitm_addon import MITMProxyInterceptor

    interceptor = MITMProxyInterceptor(enable_caching=False)

    # Verify scheme-less URLs correctly detect provider by parsing domain/path without leading http(s)://
    assert (
        interceptor.detect_provider("generativelanguage.googleapis.com/v1beta/models/custom-model:predict") == "gemini"
    )
    assert interceptor.detect_provider("api.anthropic.com/v1/messages") == "anthropic"
    assert interceptor.detect_provider("api.openai.com/v1/chat/completions") == "openai"
    assert interceptor.detect_provider("daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist") == "unknown"


def test_mitm_addon_sse_telemetry(tmp_path, monkeypatch, caplog):  # noqa: C901
    from sandbox_executor.token_reduction.mitm_addon import MitmproxyAddon, time

    addon = MitmproxyAddon()
    addon.interceptor.cache_dir = str(tmp_path / "cache")

    class FakeHeaders(dict):
        def get(self, key, default=None):
            for k, v in self.items():
                if k.lower() == key.lower():
                    return v
            return default

    class FakeRequest:
        def __init__(self, url, text):
            self.pretty_url = url
            self._text = text

        def get_text(self):
            return self._text

        def set_text(self, text):
            self._text = text

    class FakeResponse:
        def __init__(self, status_code=200, headers=None):
            self.status_code = status_code
            self.headers = FakeHeaders(headers or {})
            self.stream = None

        def get_text(self):
            return ""

    class FakeFlow:
        def __init__(self, request, response=None):
            self.request = request
            self.response = response
            self.is_cached = False

    timestamps = [10.0, 11.5, 11.5, 13.0]
    ts_iter = iter(timestamps)
    monkeypatch.setattr(time, "perf_counter", lambda: next(ts_iter))

    url = "https://api.anthropic.com/v1/messages"
    req_body = {"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "Hello"}]}

    sse_events = [
        (
            b'data: {"type": "message_start", "message": {"usage": '
            b'{"input_tokens": 80, "cache_read_input_tokens": 30, "cache_creation_input_tokens": 10}}}\n'
        ),
        b'data: {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}\n',
        b'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}}\n',
        b'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": " world!"}}\n',
        b'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 25}}\n',
    ]

    flow = FakeFlow(FakeRequest(url, json.dumps(req_body)), FakeResponse(200, {"Content-Type": "text/event-stream"}))

    with caplog.at_level(logging.INFO):
        addon.request(flow)
        addon.responseheaders(flow)
        assert flow.response.stream is not None

        for event in sse_events:
            flow.response.stream(event)

        assert len(flow.sse_chunks) == len(sse_events)
        addon.response(flow)

    assert flow.response.headers["X-Holon-Cache-Hit-Rate"] == "0.0000"
    assert flow.response.headers["X-Holon-TTFT-Ms"] == "1500.00"
    assert flow.response.headers["X-Holon-Prefill-TPS"] == "80.0000"
    assert flow.response.headers["X-Holon-Tail-Prefill-TPS"] == "60.0000"
    assert flow.response.headers["X-Holon-Decode-Time-Sec"] == "1.500"
    assert flow.response.headers["X-Holon-Output-TPS"] == "16.6667"
    assert flow.response.headers["X-Holon-Total-Time-Ms"] == "3000.00"

    telemetry_logs = [record.message for record in caplog.records if "[TELEMETRY]" in record.message]
    assert len(telemetry_logs) == 1
    assert "Provider: ANTHROPIC" in telemetry_logs[0]
    assert "TTFT: 1500.0ms" in telemetry_logs[0]
    assert "Prefill: 80.00 t/s (120 tok)" in telemetry_logs[0]
    assert "Output: 16.67 t/s (25 tok in 1.50s)" in telemetry_logs[0]
    assert "Total: 3000.0ms" in telemetry_logs[0]


def test_mitm_addon_openai_sse_telemetry(tmp_path, monkeypatch, caplog):  # noqa: C901
    from sandbox_executor.token_reduction.mitm_addon import MitmproxyAddon, time

    addon = MitmproxyAddon()
    addon.interceptor.cache_dir = str(tmp_path / "cache")

    class FakeHeaders(dict):
        def get(self, key, default=None):
            for k, v in self.items():
                if k.lower() == key.lower():
                    return v
            return default

    class FakeRequest:
        def __init__(self, url, text):
            self.pretty_url = url
            self._text = text

        def get_text(self):
            return self._text

        def set_text(self, text):
            self._text = text

    class FakeResponse:
        def __init__(self, status_code=200, headers=None):
            self.status_code = status_code
            self.headers = FakeHeaders(headers or {})
            self.stream = None

        def get_text(self):
            return ""

    class FakeFlow:
        def __init__(self, request, response=None):
            self.request = request
            self.response = response
            self.is_cached = False

    timestamps = [20.0, 22.0, 22.0, 26.0]
    ts_iter = iter(timestamps)
    monkeypatch.setattr(time, "perf_counter", lambda: next(ts_iter))

    url = "https://api.openai.com/v1/chat/completions"
    req_body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}

    sse_events = [
        b'data: {"choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}]}\n',
        b'data: {"choices": [{"index": 0, "delta": {"content": "Hello"}}]}\n',
        b'data: {"choices": [{"index": 0, "delta": {"content": " world!"}}]}\n',
        b'data: {"choices": [], "usage": {"prompt_tokens": 15, "completion_tokens": 12, "total_tokens": 27}}\n',
        b"data: [DONE]\n",
    ]

    flow = FakeFlow(FakeRequest(url, json.dumps(req_body)), FakeResponse(200, {"Content-Type": "text/event-stream"}))

    with caplog.at_level(logging.INFO):
        addon.request(flow)
        addon.responseheaders(flow)
        assert flow.response.stream is not None

        for event in sse_events:
            flow.response.stream(event)

        addon.response(flow)

    assert flow.response.headers["X-Holon-Cache-Hit-Rate"] == "0.0000"
    assert flow.response.headers["X-Holon-TTFT-Ms"] == "2000.00"
    assert flow.response.headers["X-Holon-Prefill-TPS"] == "7.5000"
    assert flow.response.headers["X-Holon-Tail-Prefill-TPS"] == "7.5000"
    assert flow.response.headers["X-Holon-Decode-Time-Sec"] == "4.000"
    assert flow.response.headers["X-Holon-Output-TPS"] == "3.0000"
    assert flow.response.headers["X-Holon-Total-Time-Ms"] == "6000.00"

    telemetry_logs = [record.message for record in caplog.records if "[TELEMETRY]" in record.message]
    assert len(telemetry_logs) == 1
    assert "Provider: OPENAI" in telemetry_logs[0]
    assert "TTFT: 2000.0ms" in telemetry_logs[0]
    assert "Prefill: 7.50 t/s (15 tok)" in telemetry_logs[0]
    assert "Output: 3.00 t/s (12 tok in 4.00s)" in telemetry_logs[0]
    assert "Total: 6000.0ms" in telemetry_logs[0]


def test_mitm_addon_openai_sse_telemetry_estimation_fallback(tmp_path, monkeypatch, caplog):  # noqa: C901
    from sandbox_executor.token_reduction.mitm_addon import MitmproxyAddon, time

    addon = MitmproxyAddon()
    addon.interceptor.cache_dir = str(tmp_path / "cache")

    class FakeHeaders(dict):
        def get(self, key, default=None):
            for k, v in self.items():
                if k.lower() == key.lower():
                    return v
            return default

    class FakeRequest:
        def __init__(self, url, text):
            self.pretty_url = url
            self._text = text

        def get_text(self):
            return self._text

        def set_text(self, text):
            self._text = text

    class FakeResponse:
        def __init__(self, status_code=200, headers=None):
            self.status_code = status_code
            self.headers = FakeHeaders(headers or {})
            self.stream = None

        def get_text(self):
            return ""

    class FakeFlow:
        def __init__(self, request, response=None):
            self.request = request
            self.response = response
            self.is_cached = False

    timestamps = [20.0, 22.0, 22.0, 26.0]
    ts_iter = iter(timestamps)
    monkeypatch.setattr(time, "perf_counter", lambda: next(ts_iter))

    url = "https://api.openai.com/v1/chat/completions"
    req_body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}

    sse_events = [
        b'data: {"choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}]}\n',
        b'data: {"choices": [{"index": 0, "delta": {"content": "Hello"}}]}\n',
        b'data: {"choices": [{"index": 0, "delta": {"content": " world! 12345"}}]}\n',
        b"data: [DONE]\n",
    ]

    flow = FakeFlow(FakeRequest(url, json.dumps(req_body)), FakeResponse(200, {"Content-Type": "text/event-stream"}))

    with caplog.at_level(logging.INFO):
        addon.request(flow)
        addon.responseheaders(flow)
        assert flow.response.stream is not None

        for event in sse_events:
            flow.response.stream(event)

        addon.response(flow)

    assert flow.response.headers["X-Holon-Cache-Hit-Rate"] == "0.0000"
    assert flow.response.headers["X-Holon-TTFT-Ms"] == "2000.00"
    assert flow.response.headers["X-Holon-Prefill-TPS"] == "0.5000"
    assert flow.response.headers["X-Holon-Tail-Prefill-TPS"] == "0.5000"
    assert flow.response.headers["X-Holon-Decode-Time-Sec"] == "4.000"
    assert flow.response.headers["X-Holon-Output-TPS"] == "1.0000"
    assert flow.response.headers["X-Holon-Total-Time-Ms"] == "6000.00"

    telemetry_logs = [record.message for record in caplog.records if "[TELEMETRY]" in record.message]
    assert len(telemetry_logs) == 1
    assert "Provider: OPENAI" in telemetry_logs[0]
    assert "TTFT: 2000.0ms" in telemetry_logs[0]
    assert "Prefill: 0.50 t/s (1 tok)" in telemetry_logs[0]
    assert "Output: 1.00 t/s (4 tok in 4.00s)" in telemetry_logs[0]
    assert "Total: 6000.0ms" in telemetry_logs[0]


def test_find_nested_key_and_cloudcode_pa_sse_parsing():
    from sandbox_executor.token_reduction.mitm_addon import extract_sse_token_counts, find_nested_key

    data = {
        "response": {
            "result": {
                "candidates": [{"content": {"parts": [{"text": "Sample code from Cloud Code PA"}]}}],
                "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 40},
            }
        }
    }
    assert find_nested_key(data, ("usageMetadata", "usage")) == {"promptTokenCount": 120, "candidatesTokenCount": 40}
    expected_cand = [{"content": {"parts": [{"text": "Sample code from Cloud Code PA"}]}}]
    assert find_nested_key(data, ("candidates", "choices")) == expected_cand

    nested_sse = (
        'data: {"response": {"candidates": [{"content": {"parts": [{"text": "Hello world"}]}}], '
        '"usageMetadata": {"promptTokenCount": 150, "candidatesTokenCount": 45}}}\n\n'
    )
    in_tok, out_tok, cache_tok = extract_sse_token_counts(nested_sse, {}, provider="gemini")
    assert in_tok == 150
    assert out_tok == 45
    assert cache_tok == 0


def test_anthropic_sse_cache_read_token_extraction():
    from sandbox_executor.token_reduction.mitm_addon import extract_sse_cache_read_tokens, extract_sse_token_counts

    sse_text = (
        "event: message_start\n"
        'data: {"type": "message_start", "message": {"id": "msg_123", '
        '"usage": {"input_tokens": 100, "cache_read_input_tokens": 400, "cache_creation_input_tokens": 50}}}\n\n'
        "event: message_delta\n"
        'data: {"type": "message_delta", "usage": {"output_tokens": 80}}\n\n'
    )
    in_tok, out_tok, cache_tok = extract_sse_token_counts(sse_text, {}, provider="anthropic")
    assert in_tok == 550
    assert out_tok == 80
    assert cache_tok == 400
    # Direct test assertion for standalone extract_sse_cache_read_tokens helper
    assert extract_sse_cache_read_tokens(sse_text) == 400


def test_response_stream_preserves_existing_callback(tmp_path):
    from sandbox_executor.token_reduction.mitm_addon import MitmproxyAddon

    addon = MitmproxyAddon()

    class FakeHeaders(dict):
        def get(self, key, default=None):
            for k, v in self.items():
                if k.lower() == key.lower():
                    return v
            return default

    existing_calls = []

    def upstream_stream_fn(chunk: bytes) -> bytes:
        existing_calls.append(chunk)
        return chunk + b"_transformed"

    class FakeResponse:
        def __init__(self):
            self.status_code = 200
            self.headers = FakeHeaders({"Content-Type": "text/event-stream"})
            self.stream = upstream_stream_fn

    class FakeFlow:
        def __init__(self):
            self.provider = "anthropic"
            self.response = FakeResponse()
            self.sse_chunks = None

    flow = FakeFlow()
    addon.responseheaders(flow)

    assert flow.response.stream is not None
    assert callable(flow.response.stream)

    test_chunk = b'data: {"test": 1}\n\n'
    res = flow.response.stream(test_chunk)

    # Chunks are tracked in flow.sse_chunks
    assert flow.sse_chunks == [test_chunk]
    # Upstream stream function was invoked
    assert existing_calls == [test_chunk]
    # Returned result is from upstream_stream_fn
    assert res == test_chunk + b"_transformed"


def test_extract_sse_token_counts_filters_non_data_lines():
    from sandbox_executor.token_reduction.mitm_addon import extract_sse_token_counts

    sse_text = (
        ": comment line here\n"
        "id: 42\n"
        "event: message_start\n"
        "retry: 2000\n"
        'data: {"type": "message_start", "message": {"usage": {"input_tokens": 50, "cache_read_input_tokens": 10}}}\n'
        ": another comment\n"
        "event: message_delta\n"
        'data: {"type": "message_delta", "usage": {"output_tokens": 25}}\n'
    )
    in_tok, out_tok, cache_tok = extract_sse_token_counts(sse_text, {}, provider="anthropic")
    assert in_tok == 60
    assert out_tok == 25
    assert cache_tok == 10


def test_intercept_request_bypasses_cache_for_streaming(tmp_path):
    from sandbox_executor.token_reduction.mitm_addon import MITMProxyInterceptor

    cache_dir = tmp_path / "cache"
    interceptor = MITMProxyInterceptor(cache_dir=str(cache_dir), enable_caching=True)
    endpoint = "https://api.openai.com/v1/chat/completions"
    req_json = {"messages": [{"role": "user", "content": "Hello"}], "stream": False}
    resp_json = {"choices": [{"message": {"content": "Hi"}}]}

    # Populate cache
    interceptor.intercept_response(endpoint, req_json, resp_json, status_code=200)
    _, cached = interceptor.intercept_request(endpoint, req_json)
    assert cached is not None

    # Request with stream=True must bypass cache lookup
    streaming_req = {"messages": [{"role": "user", "content": "Hello"}], "stream": True}
    _, cached_stream = interceptor.intercept_request(endpoint, streaming_req)
    assert cached_stream is None


def test_extract_sse_token_counts_tool_calls_fallback():
    from sandbox_executor.token_reduction.mitm_addon import extract_sse_token_counts

    # 1. OpenAI tool call chunk without usage metadata
    openai_tool_sse = (
        'data: {"choices": [{"index": 0, "delta": {"role": "assistant"}}]}\n'
        'data: {"choices": [{"index": 0, "delta": {"tool_calls": ['
        '{"index": 0, "id": "call_1", "function": {"name": "run_cmd", "arguments": "{\\"cmd\\": \\"ls\\"}"}}'
        "]}}]}\n"
        "data: [DONE]\n"
    )
    in_tok, out_tok, cache_tok = extract_sse_token_counts(openai_tool_sse, {"prompt": "Run ls"}, provider="openai")
    assert in_tok > 0
    expected_openai_len = len("run_cmd") + len('{"cmd": "ls"}')
    assert out_tok == max(1, expected_openai_len // 4)
    assert cache_tok == 0

    # 2. Gemini functionCall chunk without usage metadata
    gemini_tool_sse = (
        'data: {"candidates": [{"content": {"parts": ['
        '{"functionCall": {"name": "read_file", "args": {"path": "/tmp/foo"}}}'
        "]}}]}\n"
    )
    in_tok_g, out_tok_g, cache_tok_g = extract_sse_token_counts(
        gemini_tool_sse, {"prompt": "Read file"}, provider="gemini"
    )
    assert in_tok_g > 0
    expected_args_len = len("read_file") + len(json.dumps({"path": "/tmp/foo"}))
    assert out_tok_g == max(1, expected_args_len // 4)
    assert cache_tok_g == 0


def test_extract_sse_token_counts_openai_multiple_choices_fallback():
    from sandbox_executor.token_reduction.mitm_addon import extract_sse_token_counts

    # Multiple choices in a single SSE chunk without usage metadata
    openai_multi_choice_sse = (
        'data: {"choices": ['
        '{"index": 0, "delta": {"content": "First choice content"}},'
        '{"index": 1, "delta": {"content": "Second choice content"}}'
        "]}\n"
        "data: [DONE]\n"
    )
    in_tok, out_tok, cache_tok = extract_sse_token_counts(
        openai_multi_choice_sse, {"prompt": "Two alternatives"}, provider="openai"
    )
    assert in_tok > 0
    expected_len = len("First choice content") + len("Second choice content")
    assert out_tok == max(1, expected_len // 4)
    assert cache_tok == 0


def test_gemini_stream_generate_content_bypasses_cache(tmp_path):
    from sandbox_executor.token_reduction.mitm_addon import MITMProxyInterceptor

    cache_dir = str(tmp_path / "cache")
    interceptor = MITMProxyInterceptor(cache_dir=cache_dir, enable_caching=True)

    endpoint_non_stream = "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent"
    endpoint_stream = "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:streamGenerateContent"
    req_json = {"contents": [{"role": "user", "parts": [{"text": "Hello Gemini"}]}]}
    resp_json = {"candidates": [{"content": {"parts": [{"text": "Hi human"}]}}]}

    # Populate cache using non-streaming endpoint
    interceptor.intercept_response(endpoint_non_stream, req_json, resp_json, status_code=200)

    # Non-streaming request should hit cache
    _, cached = interceptor.intercept_request(endpoint_non_stream, req_json)
    assert cached is not None

    # Streaming request with :streamGenerateContent should bypass cache
    _, cached_stream = interceptor.intercept_request(endpoint_stream, req_json)
    assert cached_stream is None

    # Attempting to put response for streaming endpoint should be ignored
    interceptor.intercept_response(endpoint_stream, req_json, resp_json, status_code=200)


def test_responseheaders_case_insensitive_content_type():
    from sandbox_executor.token_reduction.mitm_addon import MitmproxyAddon

    addon = MitmproxyAddon()

    class FakeHeaders(dict):
        def get(self, key, default=None):
            for k, v in self.items():
                if k.lower() == key.lower():
                    return v
            return default

    class FakeResponse:
        def __init__(self):
            self.status_code = 200
            self.headers = FakeHeaders({"Content-Type": "Text/Event-Stream; charset=utf-8"})
            self.stream = None

    class FakeFlow:
        def __init__(self):
            self.provider = "openai"
            self.response = FakeResponse()
            self.sse_chunks = None

    flow = FakeFlow()
    addon.responseheaders(flow)
    assert flow.response.stream is not None
    assert flow.sse_chunks == []


def test_mitm_addon_ttft_vs_ttfb_timing(tmp_path, monkeypatch, caplog):
    from sandbox_executor.token_reduction.mitm_addon import MitmproxyAddon, time

    addon = MitmproxyAddon()
    addon.interceptor.cache_dir = str(tmp_path / "cache")

    class FakeHeaders(dict):
        def get(self, key, default=None):
            for k, v in self.items():
                if k.lower() == key.lower():
                    return v
            return default

    class FakeRequest:
        def __init__(self, url, text):
            self.pretty_url = url
            self._text = text

        def get_text(self):
            return self._text

    class FakeResponse:
        def __init__(self, status_code=200, headers=None):
            self.status_code = status_code
            self.headers = FakeHeaders(headers or {})
            self.stream = None

        def get_text(self):
            return ""

    class FakeFlow:
        def __init__(self, request, response=None):
            self.request = request
            self.response = response
            self.is_cached = False

    # Timestamps:
    # 1. req_start = 100.0
    # 2. responseheaders (TTFB) = 101.0 (TTFB = 1.0s / 1000ms)
    # 3. first chunk (TTFT) = 101.5 (TTFT = 1.5s / 1500ms)
    # 4. response (now) = 104.0 (total = 4.0s / 4000ms, decode = 2.5s)
    timestamps = [100.0, 101.0, 101.5, 104.0]
    ts_iter = iter(timestamps)
    monkeypatch.setattr(time, "perf_counter", lambda: next(ts_iter))

    url = "https://api.openai.com/v1/chat/completions"
    req_body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]}
    sse_events = [
        b'data: {"choices": [{"index": 0, "delta": {"content": "Hello"}}]}\n',
        b'data: {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}\n',
        b"data: [DONE]\n",
    ]

    flow = FakeFlow(FakeRequest(url, json.dumps(req_body)), FakeResponse(200, {"Content-Type": "text/event-stream"}))

    with caplog.at_level(logging.INFO):
        addon.request(flow)
        addon.responseheaders(flow)
        assert flow.first_chunk_time is None

        for event in sse_events:
            flow.response.stream(event)

        assert flow.first_chunk_time == 101.5
        addon.response(flow)

    assert flow.response.headers["X-Holon-TTFT-Ms"] == "1500.00"
    assert flow.response.headers["X-Holon-Decode-Time-Sec"] == "2.500"
    assert flow.response.headers["X-Holon-Total-Time-Ms"] == "4000.00"

    telemetry_logs = [record.message for record in caplog.records if "[TELEMETRY]" in record.message]
    assert "TTFT: 1500.0ms" in telemetry_logs[0]


def test_prompt_cache_token_extraction_gemini_and_openai():
    from sandbox_executor.token_reduction.mitm_addon import extract_sse_token_counts, extract_token_counts

    # 1. Gemini SSE with cachedContentTokenCount
    gemini_sse = (
        'data: {"candidates": [{"content": {"parts": [{"text": "Hello"}]}}], '
        '"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20, "cachedContentTokenCount": 60}}\n\n'
    )
    in_tok, out_tok, cache_tok = extract_sse_token_counts(gemini_sse, {}, provider="gemini")
    assert in_tok == 100
    assert out_tok == 20
    assert cache_tok == 60

    # 2. Gemini non-SSE with cachedContentTokenCount
    gemini_resp = {
        "candidates": [{"content": {"parts": [{"text": "Hello"}]}}],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20, "cachedContentTokenCount": 60},
    }
    in_tok, out_tok, cache_tok = extract_token_counts({}, gemini_resp, provider="gemini")
    assert in_tok == 100
    assert out_tok == 20
    assert cache_tok == 60

    # 3. OpenAI SSE with prompt_tokens_details.cached_tokens
    openai_sse = (
        'data: {"choices": [{"delta": {"content": "Hello"}}], '
        '"usage": {"prompt_tokens": 120, "completion_tokens": 30, '
        '"prompt_tokens_details": {"cached_tokens": 80}}}\n\n'
    )
    in_tok, out_tok, cache_tok = extract_sse_token_counts(openai_sse, {}, provider="openai")
    assert in_tok == 120
    assert out_tok == 30
    assert cache_tok == 80

    # 4. OpenAI non-SSE with prompt_tokens_details.cached_tokens
    openai_resp = {
        "choices": [{"message": {"content": "Hello"}}],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "prompt_tokens_details": {"cached_tokens": 80},
        },
    }
    in_tok, out_tok, cache_tok = extract_token_counts({}, openai_resp, provider="openai")
    assert in_tok == 120
    assert out_tok == 30
    assert cache_tok == 80


def test_anthropic_sse_usage_non_dict_guard():
    from sandbox_executor.token_reduction.mitm_addon import extract_sse_token_counts

    # Malformed SSE with non-dict usage
    malformed_sse = (
        'data: {"type": "message_start", "message": {"usage": 12345}}\n'
        'data: {"type": "message_delta", "usage": "invalid"}\n'
        'data: {"type": "content_block_delta", "delta": {"text": "hello"}}\n'
    )
    in_tok, out_tok, cache_tok = extract_sse_token_counts(malformed_sse, {"prompt": "test"}, provider="anthropic")
    # Should not raise exception, falls back to char estimation
    assert in_tok >= 0
    assert out_tok >= 0
    assert cache_tok == 0


def test_sse_stream_wrapper_bounded_buffer_ceiling(monkeypatch):
    import sandbox_executor.token_reduction.mitm_addon as mitm_module
    from sandbox_executor.token_reduction.mitm_addon import MitmproxyAddon

    # Set ceiling to 30 bytes for testing
    monkeypatch.setattr(mitm_module, "_MAX_SSE_BUFFER_BYTES", 30)

    addon = MitmproxyAddon()

    existing_stream_chunks = []

    def dummy_stream(chunk: bytes) -> bytes:
        existing_stream_chunks.append(chunk)
        return chunk

    class FakeResponse:
        def __init__(self):
            self.status_code = 200
            self.headers = {"content-type": "text/event-stream"}
            self.stream = dummy_stream

    class FakeFlow:
        def __init__(self):
            self.provider = "anthropic"
            self.response = FakeResponse()
            self.sse_chunks = None

    flow = FakeFlow()
    addon.responseheaders(flow)
    assert flow.sse_chunks == []
    assert flow.sse_bytes == 0

    chunk1 = b"1234567890"  # 10 bytes -> total 10 <= 30
    chunk2 = b"1234567890"  # 10 bytes -> total 20 <= 30
    chunk3 = b"1234567890"  # 10 bytes -> total 30 <= 30
    chunk4 = b"1234567890"  # 10 bytes -> total 40 > 30 (exceeded)

    ret1 = flow.response.stream(chunk1)
    ret2 = flow.response.stream(chunk2)
    ret3 = flow.response.stream(chunk3)
    ret4 = flow.response.stream(chunk4)

    # Transparent pass-through
    assert ret1 == chunk1
    assert ret2 == chunk2
    assert ret3 == chunk3
    assert ret4 == chunk4
    assert existing_stream_chunks == [chunk1, chunk2, chunk3, chunk4]

    # Accumulated chunks should only hold first 3
    assert flow.sse_chunks == [chunk1, chunk2, chunk3]
    assert flow.sse_bytes == 40


def test_is_streaming_alt_sse_parameter(tmp_path):
    from sandbox_executor.token_reduction.mitm_addon import MITMProxyInterceptor

    interceptor = MITMProxyInterceptor(cache_dir=str(tmp_path / "cache"), enable_caching=True)
    endpoint_standard = "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent"
    endpoint_sse = "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?alt=sse"
    req_json = {"contents": [{"role": "user", "parts": [{"text": "Hello"}]}]}
    resp_json = {"candidates": [{"content": {"parts": [{"text": "World"}]}}]}

    # Pre-populate cache using standard endpoint
    interceptor.intercept_response(endpoint_standard, req_json, resp_json, status_code=200)

    # Standard endpoint should hit cache
    _, cached = interceptor.intercept_request(endpoint_standard, req_json)
    assert cached is not None

    # alt=sse endpoint should bypass cache lookup
    _, cached_sse = interceptor.intercept_request(endpoint_sse, req_json)
    assert cached_sse is None

    # alt=sse endpoint should bypass cache store
    interceptor_fresh = MITMProxyInterceptor(cache_dir=str(tmp_path / "cache_fresh"), enable_caching=True)
    interceptor_fresh.intercept_response(endpoint_sse, req_json, resp_json, status_code=200)
    _, cached_lookup = interceptor_fresh.intercept_request(endpoint_standard, req_json)
    assert cached_lookup is None


def test_anthropic_sse_thinking_delta_fallback():
    from sandbox_executor.token_reduction.mitm_addon import extract_sse_token_counts

    sse_data = (
        'data: {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}}\n'
        'data: {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "1234567890123456"}}\n'
        'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "12345678"}}\n'
        'data: {"type": "message_stop"}\n'
    )
    # Total chars = 16 (thinking) + 8 (text) = 24 chars -> output_tokens = 24 // 4 = 6
    _, out_tokens, _ = extract_sse_token_counts(sse_data, {"prompt": "think"}, provider="anthropic")
    assert out_tokens == 6


def test_find_nested_key_type_guard():
    from sandbox_executor.token_reduction.mitm_addon import find_nested_key

    assert find_nested_key(12345, "key") is None
    assert find_nested_key("string_value", "key") is None
    assert find_nested_key(None, "key") is None
    assert find_nested_key(3.1415, "key") is None
    assert find_nested_key(True, "key") is None
    assert find_nested_key(object(), "key") is None
    # Valid dict and list should still work
    assert find_nested_key({"target": "found"}, "target") == "found"
    assert find_nested_key([{"target": "found"}], "target") == "found"


def test_log_telemetry_ctx_handling(monkeypatch, caplog):
    import sandbox_executor.token_reduction.mitm_addon as mitm_module
    from sandbox_executor.token_reduction.mitm_addon import log_telemetry

    # 1. Test when ctx is available and has log.info
    fake_ctx_log = MagicMock()
    fake_ctx = SimpleNamespace(log=SimpleNamespace(info=fake_ctx_log))
    monkeypatch.setattr(mitm_module, "ctx", fake_ctx)

    log_telemetry("test ctx message")
    fake_ctx_log.assert_called_once_with("test ctx message")

    # 2. Test when ctx is None, falls back to logger.info
    monkeypatch.setattr(mitm_module, "ctx", None)
    with caplog.at_level(logging.INFO):
        log_telemetry("test fallback message")
    assert any("test fallback message" in r.message for r in caplog.records)


def test_payload_cleaner_cleaning_result_metrics():
    from sandbox_executor.token_reduction.payload_cleaner import CleaningResult, JSONContextCleaner

    cleaner = JSONContextCleaner(enable_deduplication=True, enable_prompt_caching=True, max_turns=10)

    # 1. Deduplication and cache control injection
    dup_tool_output = "Line " + "x" * 2000
    payload = {
        "system": "You are a helpful assistant.",
        "tools": [{"name": "read_file", "description": "Reads a file"}],
        "messages": [
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": dup_tool_output}],
            },
            {"role": "assistant", "content": "Got it."},
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "call_2", "content": dup_tool_output}],
            },
            {"role": "assistant", "content": "Checking."},
            {"role": "user", "content": "Latest query."},
        ],
    }

    result = cleaner.process_payload_with_stats(payload, provider="anthropic")
    assert isinstance(result, CleaningResult)
    assert result.tool_outputs_omitted >= 1
    assert result.cache_control_injected >= 1
    assert result.chars_saved > 0
    assert "[Omitted:" in json.dumps(result.payload)

    # 2. History summarisation
    many_turns_payload = {
        "messages": [
            {"role": "user", "content": f"Turn {i} " + "content " * 10}
            if i % 2 == 0
            else {"role": "assistant", "content": f"Reply {i}"}
            for i in range(15)
        ]
    }
    sum_res = cleaner.process_payload_with_stats(many_turns_payload, provider="anthropic")
    assert sum_res.turns_summarized > 0
    assert any("[Summary of omitted" in json.dumps(m) for m in sum_res.payload["messages"])


def test_secret_scrubbing():
    from sandbox_executor.token_reduction.mitm_addon import (
        scrub_headers,
        scrub_payload,
        scrub_string,
    )

    # 1. URL query parameters
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash?key=AIzaSyA1234567890123456789012345678901&token=secret-token-value"
    scrubbed_url = scrub_string(url)
    assert "AIza" not in scrubbed_url
    assert "secret-token-value" not in scrubbed_url
    assert "key=[REDACTED]" in scrubbed_url
    assert "token=[REDACTED]" in scrubbed_url

    # 2. Secret regex patterns in text
    secrets = [
        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz012345",
        "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789",
        "sk-admin-abcdefghijklmnopqrstuvwxyz0123456789",
        "sk-svcacct-abcdefghijklmnopqrstuvwxyz0123456789",
        "AIzaSyDabcdefghijklmnopqrstuvwxyz012345",
        "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789",
        "gho_" + "abcdefghijklmnopqrstuvwxyz0123456789",
        "github_pat_" + "11AAAAAAA0123456789abcdefghijklmnopqrstuvwxyz",
        "AKIAIOSFODNN7EXAMPLE",
        "ASIAIOSFODNN7EXAMPLE",
        "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "hf_abcdefghijklmnopqrstuvwxyz012345678",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0...\n-----END RSA PRIVATE KEY-----",
    ]
    for sec in secrets:
        scrubbed = scrub_string(f"Here is a secret: {sec} in code")
        assert sec not in scrubbed
        assert "[REDACTED_SECRET]" in scrubbed

    # 3. Case-insensitive header names
    headers = {
        "AUTHORIZATION": "Bearer my-secret-token",
        "X-Api-Key": "secret-api-key",
        "api-key": "secret-api-key-2",
        "X-Goog-Api-Key": "goog-key",
        "Holon-Agent-Key": "agent-key",
        "Proxy-Authorization": "Basic dXNlcjpwYXNz",
        "X-Amz-Security-Token": "amz-token",
        "Cookie": "session=xyz123; user=admin",
        "Set-Cookie": "auth=secrettoken",
        "X-Auth-Token": "my-x-auth-token",
        "Content-Type": "application/json",
    }
    scrubbed_h = scrub_headers(headers)
    for k in [
        "authorization",
        "x-api-key",
        "api-key",
        "x-goog-api-key",
        "holon-agent-key",
        "proxy-authorization",
        "x-amz-security-token",
        "cookie",
        "set-cookie",
        "x-auth-token",
    ]:
        assert scrubbed_h[k] == "[REDACTED]"
    assert scrubbed_h["content-type"] == "application/json"

    # 4. Recursive payload scrubbing
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "user",
                "content": "Secret is sk-ant-api03-abcdefghijklmnopqrstuvwxyz012345 and key=secret123",
            }
        ],
        "metadata": {"token": "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789"},
        "credentials": {
            "password": "super-secret-password",
            "api_key": "custom-unmatched-secret-value",
            "access_token": "my-custom-access-token",
        },
    }
    scrubbed_p = scrub_payload(payload)
    assert "sk-ant-" not in json.dumps(scrubbed_p)
    assert "ghp_" not in json.dumps(scrubbed_p)
    assert "[REDACTED_SECRET]" in scrubbed_p["messages"][0]["content"]
    assert "[REDACTED_SECRET]" in scrubbed_p["metadata"]["token"]
    assert scrubbed_p["credentials"]["password"] == "[REDACTED]"
    assert scrubbed_p["credentials"]["api_key"] == "[REDACTED]"
    assert scrubbed_p["credentials"]["access_token"] == "[REDACTED]"


def test_derive_turn_id_precedence():
    from sandbox_executor.token_reduction.mitm_addon import derive_turn_id

    # Tier 1: Explicit X-Holon-Turn-Id header
    headers = {"X-Holon-Turn-Id": "7"}
    payload = {"messages": [{"role": "assistant"}, {"role": "assistant"}]}
    assert derive_turn_id(headers, payload, fallback_id=1) == 7

    # Tier 2: Assistant completion count in messages (+ 1)
    no_header = {}
    payload_messages = {
        "messages": [
            {"role": "user"},
            {"role": "assistant"},
            {"role": "user"},
            {"role": "assistant"},
        ]
    }
    assert derive_turn_id(no_header, payload_messages, fallback_id=1) == 3

    # Tier 2 (Gemini contents): Model count in contents (+ 1)
    payload_gemini = {
        "contents": [
            {"role": "user"},
            {"role": "model"},
            {"role": "user"},
        ]
    }
    assert derive_turn_id(no_header, payload_gemini, fallback_id=1) == 2

    # Tier 3: Fallback sequential counter
    assert derive_turn_id({}, {}, fallback_id=42) == 42


def test_dump_wire_transaction_and_flush(tmp_path):
    from sandbox_executor.token_reduction.mitm_addon import dump_wire_transaction, flush_wire_logs

    wire_dir = str(tmp_path / "mitm_logs")
    record = {
        "turn_id": 3,
        "flow_id": "test_flow_xyz",
        "agent_id": "subagent_01",
        "agent_role": "executor",
        "timestamp": "2026-09-08T21:30:00.000Z",
        "provider": "anthropic",
        "endpoint": "https://api.anthropic.com/v1/messages",
        "headers": {"content-type": "application/json", "x-api-key": "[REDACTED]"},
        "raw_request": {"messages": [{"role": "user", "content": "hi"}]},
        "cleaned_request": {"messages": [{"role": "user", "content": "hi"}]},
        "delta": {
            "raw_chars": 50,
            "cleaned_chars": 50,
            "chars_saved": 0,
            "tool_outputs_omitted": 0,
            "turns_summarized": 0,
            "cache_control_injected": 0,
        },
        "cache_action": "MISS",
        "response": {
            "status": 200,
            "usage": {
                "input_tokens": 100,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 50,
                "output_tokens": 20,
                "reasoning_tokens": 5,
            },
            "content": "Hello there!",
        },
        "timing": {"ttft_ms": 150.0, "total_ms": 800.0},
    }

    dump_wire_transaction(record, wire_log_dir=wire_dir)
    flush_wire_logs()

    # Verify per-turn JSON dump
    turn_file = tmp_path / "mitm_logs" / "turn_3_test_flow_xyz.json"
    assert turn_file.is_file()
    with open(turn_file) as f:
        loaded_turn = json.load(f)
    assert loaded_turn["turn_id"] == 3
    assert loaded_turn["response"]["usage"]["reasoning_tokens"] == 5

    # Verify transactions.jsonl append
    jsonl_file = tmp_path / "mitm_logs" / "transactions.jsonl"
    assert jsonl_file.is_file()
    with open(jsonl_file) as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert len(lines) == 1
    assert lines[0]["flow_id"] == "test_flow_xyz"


def test_extract_detailed_token_counts_sse_streams():
    from sandbox_executor.token_reduction.mitm_addon import extract_detailed_token_counts

    # Anthropic SSE stream with message_start (cache_read, cache_creation, input) and message_delta (output, thinking)
    anthropic_sse = (
        "event: message_start\n"
        'data: {"type": "message_start", "message": {"id": "msg_123", '
        '"usage": {"input_tokens": 100, "cache_read_input_tokens": 400, "cache_creation_input_tokens": 50}}}\n\n'
        "event: message_delta\n"
        'data: {"type": "message_delta", "usage": {"output_tokens": 80, "thinking_tokens": 25}}\n\n'
    )
    counts_ant = extract_detailed_token_counts({}, anthropic_sse, "anthropic")
    assert counts_ant["input_tokens"] == 550
    assert counts_ant["output_tokens"] == 80
    assert counts_ant["cache_read_input_tokens"] == 400
    assert counts_ant["cache_creation_input_tokens"] == 50
    assert counts_ant["reasoning_tokens"] == 25

    # OpenAI SSE stream with cached_tokens and reasoning_tokens
    openai_sse = (
        'data: {"choices": [{"delta": {"content": "Hello"}}], '
        '"usage": {"prompt_tokens": 120, "completion_tokens": 30, '
        '"prompt_tokens_details": {"cached_tokens": 80}, '
        '"completion_tokens_details": {"reasoning_tokens": 15}}}\n\n'
    )
    counts_oai = extract_detailed_token_counts({}, openai_sse, "openai")
    assert counts_oai["input_tokens"] == 120
    assert counts_oai["output_tokens"] == 30
    assert counts_oai["cache_read_input_tokens"] == 80
    assert counts_oai["cache_creation_input_tokens"] == 0
    assert counts_oai["reasoning_tokens"] == 15

    # Google Gemini SSE stream with usageMetadata, cachedContentTokenCount, candidatesTokenCount, reasoningTokenCount
    gemini_sse = (
        'data: {"candidates": [{"content": {"parts": [{"text": "Hello world"}]}}], '
        '"usageMetadata": {"promptTokenCount": 95, "candidatesTokenCount": 42, '
        '"cachedContentTokenCount": 30, "reasoningTokenCount": 18}}\n\n'
    )
    counts_gem = extract_detailed_token_counts({}, gemini_sse, "gemini")
    assert counts_gem["input_tokens"] == 95
    assert counts_gem["output_tokens"] == 42
    assert counts_gem["cache_read_input_tokens"] == 30
    assert counts_gem["cache_creation_input_tokens"] == 0
    assert counts_gem["reasoning_tokens"] == 18


def test_turn_id_sanitization_prevents_traversal(tmp_path):
    from sandbox_executor.token_reduction.mitm_addon import _write_transaction_sync

    target_dir = tmp_path / "wire_logs"
    record = {
        "turn_id": "../../etc/malicious",
        "flow_id": "../../escape",
        "timestamp": "2026-09-10T00:00:00Z",
    }
    _write_transaction_sync(record, str(target_dir))

    # All generated files must reside strictly within target_dir
    files = list(target_dir.iterdir())
    assert len(files) == 2  # turn_*.json and transactions.jsonl
    turn_file = next(f for f in files if f.name.startswith("turn_"))
    assert ".." not in turn_file.name
    assert "/" not in turn_file.name
    assert turn_file.name == "turn_______etc_malicious_______escape.json"


def test_enable_wire_logging_toggle(tmp_path, monkeypatch):
    from sandbox_executor.token_reduction.mitm_addon import dump_wire_transaction

    target_dir = tmp_path / "disabled_logs"

    monkeypatch.setenv("ENABLE_WIRE_LOGGING", "0")
    record = {"turn_id": 1, "flow_id": "f1"}
    future = dump_wire_transaction(record, str(target_dir))
    future.result()

    assert not target_dir.exists()


def test_extract_sse_content_anthropic_tools():
    from sandbox_executor.token_reduction.mitm_addon import extract_sse_content

    anthropic_tool_sse = (
        'data: {"type": "content_block_start", "index": 0, '
        '"content_block": {"type": "tool_use", "id": "t1", "name": "run_command"}}\n\n'
        'data: {"type": "content_block_delta", "index": 0, '
        '"delta": {"type": "input_json_delta", "partial_json": "{\\"command\\": "}}\n\n'
        'data: {"type": "content_block_delta", "index": 0, '
        '"delta": {"type": "input_json_delta", "partial_json": "\\"ls\\"}"}}\n\n'
        'data: {"type": "content_block_stop", "index": 0}\n\n'
    )
    extracted = extract_sse_content(anthropic_tool_sse, "anthropic")
    assert 'run_command({"command": "ls"})' in extracted


def test_extract_sse_content_openai_tools():
    from sandbox_executor.token_reduction.mitm_addon import extract_sse_content

    openai_tool_sse = (
        'data: {"choices": [{"index": 0, "delta": {"tool_calls": ['
        '{"index": 0, "function": {"name": "run_command", "arguments": ""}}]}}]}\n\n'
        'data: {"choices": [{"index": 0, "delta": {"tool_calls": ['
        '{"index": 0, "function": {"arguments": "{\\"command\\": "}}]}}]}\n\n'
        'data: {"choices": [{"index": 0, "delta": {"tool_calls": ['
        '{"index": 0, "function": {"arguments": "\\"ls\\"}"}}]}}]}\n\n'
        'data: {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}\n\n'
        "data: [DONE]\n\n"
    )
    extracted = extract_sse_content(openai_tool_sse, "openai")
    assert 'run_command({"command": "ls"})' in extracted


def test_dump_flow_transaction_case_insensitive_agent_headers(tmp_path):
    from sandbox_executor.token_reduction.mitm_addon import MitmproxyAddon

    wire_dir = tmp_path / "addon_logs"
    addon = MitmproxyAddon(cache_dir=str(tmp_path / "cache"), wire_log_dir=str(wire_dir))

    flow = SimpleNamespace(
        id="flow_headers_case",
        provider="anthropic",
        is_cached=False,
        request=SimpleNamespace(
            pretty_url="https://api.anthropic.com/v1/messages",
            headers={
                "X-Holon-Agent-Id": "custom_architect",
                "X-Holon-Agent-Role": "planner",
                "X-Holon-Turn-Id": "7",
            },
            get_text=lambda: '{"model": "claude-3-5-sonnet", "messages": []}',
        ),
        response=SimpleNamespace(
            status_code=200,
            headers={"content-type": "application/json"},
            get_text=lambda: '{"usage": {"input_tokens": 10, "output_tokens": 5}}',
        ),
        request_start_time=1.0,
    )

    addon.response(flow)
    addon.done()

    jsonl = wire_dir / "transactions.jsonl"
    assert jsonl.is_file()
    with open(jsonl) as f:
        data = json.loads(f.readline())
    assert data["agent_id"] == "custom_architect"
    assert data["agent_role"] == "planner"
    assert data["turn_id"] == 7


def test_mitm_addon_done_and_non_200_logging(tmp_path):
    import time

    from sandbox_executor.token_reduction.mitm_addon import MitmproxyAddon

    wire_dir = tmp_path / "addon_logs"
    addon = MitmproxyAddon(wire_log_dir=str(wire_dir))

    flow = SimpleNamespace(
        id="flow_400",
        provider="anthropic",
        is_cached=False,
        request=SimpleNamespace(
            pretty_url="https://api.anthropic.com/v1/messages",
            headers={"x-holon-agent-id": "test_agent"},
            get_text=lambda: '{"model": "claude-3-5-sonnet", "messages": []}',
        ),
        response=SimpleNamespace(
            status_code=400,
            headers={"content-type": "application/json"},
            get_text=lambda: '{"error": {"type": "invalid_request_error", "message": "Bad request"}}',
        ),
        request_start_time=time.perf_counter() - 0.1,
    )

    addon.response(flow)
    addon.done()

    # Verify transaction was dumped
    jsonl = wire_dir / "transactions.jsonl"
    assert jsonl.is_file()
    with open(jsonl) as f:
        data = json.loads(f.readline())
    assert data["response"]["status"] == 400
    assert "error" in data["response"]["content"]
    assert data["raw_request"]["model"] == "claude-3-5-sonnet"


def test_ab_measure_all_methods_endpoint_fallback(tmp_path):
    ab_module = _load_ab_runner()

    # Test endpoint model resolution fallback when model is absent from request payload
    tx = {
        "turn_id": 1,
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:streamGenerateContent",
        "raw_request": {},
        "cleaned_request": {},
        "response": {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
            }
        },
    }
    wire_dir = tmp_path / "endpoint_wire_logs"
    wire_dir.mkdir(parents=True, exist_ok=True)
    with open(wire_dir / "transactions.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps(tx) + "\n")

    res = ab_module.parse_wire_logs_into_result(wire_dir, 1, 0)
    assert res.tier2_tokens == 120
    assert res.tier1_tokens == 0


def test_write_transaction_sync_resilient_serialization(tmp_path):
    import datetime

    from sandbox_executor.token_reduction.mitm_addon import _write_transaction_sync

    target_dir = tmp_path / "resilient_logs"
    record = {
        "turn_id": 1,
        "flow_id": "f_non_primitive",
        "timestamp": datetime.datetime(2026, 9, 10, 12, 0, 0, tzinfo=datetime.UTC),
        "raw_request": {
            "bytes_val": b"binary_data",
            "set_val": {1, 2, 3},
        },
        "response": {"status": 200, "content": "ok"},
    }

    # Should not raise TypeError
    _write_transaction_sync(record, str(target_dir))

    jsonl = target_dir / "transactions.jsonl"
    assert jsonl.is_file()
    with open(jsonl) as f:
        loaded = json.loads(f.readline())
    assert loaded["turn_id"] == 1
    assert "2026-09-10" in loaded["timestamp"]
    assert "binary_data" in loaded["raw_request"]["bytes_val"]


def test_mitm_addon_intercept_request_with_stats_no_state_leak(tmp_path):
    from sandbox_executor.token_reduction.mitm_addon import MITMProxyInterceptor

    interceptor = MITMProxyInterceptor(cache_dir=str(tmp_path))
    assert not hasattr(interceptor, "last_cleaning_result")

    url = "https://api.anthropic.com/v1/messages"
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Hello"}],
    }
    cleaned_req, cached_resp, stats = interceptor.intercept_request_with_stats(url, payload)
    assert cleaned_req is not None
    assert cached_resp is None
    assert stats is not None
    assert hasattr(stats, "chars_saved")
    assert not hasattr(interceptor, "last_cleaning_result")


def test_mitm_addon_secret_headers_extended():
    from sandbox_executor.token_reduction.mitm_addon import scrub_headers

    headers = {
        "authorization": "Bearer secret1",
        "openai-api-key": "sk-123456789012345678901234",
        "x-session-token": "sess-token-abc-xyz",
        "content-type": "application/json",
    }
    scrubbed = scrub_headers(headers)
    assert scrubbed["authorization"] == "[REDACTED]"
    assert scrubbed["openai-api-key"] == "[REDACTED]"
    assert scrubbed["x-session-token"] == "[REDACTED]"
    assert scrubbed["content-type"] == "application/json"


def test_ab_measure_pricing_and_cache_hit_gate(tmp_path):
    ab_module = _load_ab_runner()

    # Test frontier model pricing
    for m in [
        "claude-3-7-sonnet",
        "claude-3-5-sonnet",
        "claude-3-5-haiku",
        "gpt-4o",
        "gpt-4o-mini",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
    ]:
        pricing = ab_module.get_model_pricing(m)
        assert pricing is not None
        assert "input" in pricing
        assert "output" in pricing

    # Test local cache hit gate on cache_read_tokens
    tx_hit = {
        "turn_id": 1,
        "endpoint": "https://api.anthropic.com/v1/messages",
        "cache_action": "HIT",
        "response": {
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 200,
                "cache_read_input_tokens": 1000,
            }
        },
    }
    wire_dir = tmp_path / "hit_logs"
    wire_dir.mkdir(parents=True, exist_ok=True)
    with open(wire_dir / "transactions.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps(tx_hit) + "\n")

    res = ab_module.parse_wire_logs_into_result(wire_dir, 1, 0)
    assert res.local_cache_hits == 1
    # cache_read_tokens must NOT be incremented for local cache HIT
    assert res.cache_read_tokens == 0
    assert res.monetary_cost == 0.0

    # Test pre_clean_environment isolates custom cache
    custom_cache = tmp_path / "custom_cache"
    custom_cache.mkdir()
    (custom_cache / "test.db").write_text("data")
    ab_module.pre_clean_environment(custom_cache, wire_dir, clean_global_cache=False)
    assert not (custom_cache / "test.db").exists()


def test_write_transaction_sync_atomic_swapping(tmp_path):
    """Verifies that _write_transaction_sync writes atomically and leaves no lingering tmp files."""
    from sandbox_executor.token_reduction.mitm_addon import _write_transaction_sync

    target_dir = tmp_path / "atomic_wire_logs"
    record = {
        "turn_id": 42,
        "flow_id": "atomic_test",
        "raw_request": {"messages": [{"role": "user", "content": "hello"}]},
        "response": {"choices": [{"message": {"role": "assistant", "content": "hi"}}]},
    }
    _write_transaction_sync(record, str(target_dir))

    target_file = target_dir / "turn_42_atomic_test.json"
    assert target_file.exists()
    loaded = json.loads(target_file.read_text(encoding="utf-8"))
    assert loaded["turn_id"] == 42
    # Ensure no temporary files (.tmp) remain in the directory
    tmp_files = list(target_dir.glob("*.tmp"))
    assert len(tmp_files) == 0


def test_scrub_payload_recursion_depth_guard():
    """Verifies that scrub_payload respects max_depth and does not overflow on deep structures."""
    from sandbox_executor.token_reduction.mitm_addon import scrub_payload

    # Construct nested structure 60 levels deep
    nested = "deep_value"
    for _ in range(60):
        nested = {"level": nested}

    # Should safely return without RecursionError
    scrubbed = scrub_payload(nested, max_depth=10)
    assert scrubbed is not None


def test_secret_headers_anthropic_api_key():
    """Verifies x-anthropic-api-key is scrubbed."""
    from sandbox_executor.token_reduction.mitm_addon import _SECRET_HEADER_NAMES

    assert "x-anthropic-api-key" in _SECRET_HEADER_NAMES


def test_mitm_addon_passive_monitoring_mode(monkeypatch, tmp_path):
    """Verifies that MitmproxyAddon in passive monitoring mode preserves request bodies and skips caching."""
    from sandbox_executor.token_reduction.mitm_addon import MitmproxyAddon

    monkeypatch.setenv("HOLON_PASSIVE_MONITORING", "1")
    addon = MitmproxyAddon(cache_dir=str(tmp_path / "cache"), wire_log_dir=str(tmp_path / "logs"))

    # Construct mock flow
    request_data = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {"role": "user", "content": "test tool output repeated repeated repeated"},
        ],
    }

    class MockRequest:
        def __init__(self):
            self.pretty_url = "https://api.anthropic.com/v1/messages"
            self.headers = {"content-type": "application/json"}
            self._text = ""

        def get_text(self):
            return json.dumps(request_data)

        def set_text(self, text):
            self._text = text

    flow = SimpleNamespace(
        request=MockRequest(),
        response=None,
        is_cached=False,
    )

    addon.request(flow)
    # In passive mode, cleaner_result must be None and request body must NOT be altered
    assert flow.cleaner_result is None
    assert flow.req_data == request_data
    assert not flow.is_cached


def test_scorecard_dynamic_guardrail_status():
    """Verifies that format_scorecard dynamically reflects guardrail status when pass rate < 100%."""
    ab = _load_ab_runner()

    # Case 1: 100% pass rate
    b1 = ab.BenchmarkSummary(
        name="baseline",
        iterations=[
            ab.IterationResult(
                iteration=1,
                success=True,
                prompt_tokens=1000,
                output_tokens=200,
                cache_read_tokens=0,
                cache_write_tokens=0,
                turn_0_prompt_tokens=500,
                pruned_bytes=0,
                duplicate_tools_omitted=0,
                local_cache_hits=0,
                total_calls=1,
                tier1_tokens=1000,
                tier2_tokens=0,
                turns_saved_memory=0,
                monetary_cost=0.01,
                wall_clock_s=5.0,
            )
        ],
    )
    o1 = ab.BenchmarkSummary(
        name="optimized",
        iterations=[
            ab.IterationResult(
                iteration=1,
                success=True,
                prompt_tokens=500,
                output_tokens=200,
                cache_read_tokens=100,
                cache_write_tokens=0,
                turn_0_prompt_tokens=200,
                pruned_bytes=500,
                duplicate_tools_omitted=2,
                local_cache_hits=1,
                total_calls=1,
                tier1_tokens=300,
                tier2_tokens=200,
                turns_saved_memory=3,
                monetary_cost=0.005,
                wall_clock_s=3.0,
            )
        ],
    )
    sc1 = ab.format_scorecard(b1, o1)
    assert "**100% (Functional correctness guardrail met)**" in sc1

    # Case 2: 0% pass rate (test failed)
    o2 = ab.BenchmarkSummary(
        name="optimized",
        iterations=[
            ab.IterationResult(
                iteration=1,
                success=False,
                prompt_tokens=500,
                output_tokens=200,
                cache_read_tokens=0,
                cache_write_tokens=0,
                turn_0_prompt_tokens=200,
                pruned_bytes=0,
                duplicate_tools_omitted=0,
                local_cache_hits=0,
                total_calls=1,
                tier1_tokens=500,
                tier2_tokens=0,
                turns_saved_memory=0,
                monetary_cost=0.005,
                wall_clock_s=3.0,
            )
        ],
    )
    sc2 = ab.format_scorecard(b1, o2)
    assert "**0% (Functional correctness guardrail NOT MET)**" in sc2


def test_write_transaction_sync_orphaned_tmp_cleanup(tmp_path, monkeypatch):
    """Verifies that _write_transaction_sync unlinks .tmp file if atomic replace fails."""
    import os

    from sandbox_executor.token_reduction import mitm_addon

    target_dir = tmp_path / "tmp_cleanup_logs"
    record = {"turn_id": 99, "flow_id": "fail_replace"}

    # Mock os.replace to raise an exception midway
    def failing_replace(src, dst):
        assert os.path.exists(src)
        raise OSError("Disk failure during replace")

    monkeypatch.setattr(mitm_addon.os, "replace", failing_replace)

    mitm_addon._write_transaction_sync(record, str(target_dir))

    # Assert no .tmp files exist in target_dir
    tmp_files = list(target_dir.glob("*.tmp"))
    assert len(tmp_files) == 0


def test_scrub_headers_defense_in_depth():
    """Verifies that custom token/secret headers are redacted by defense-in-depth."""
    from sandbox_executor.token_reduction.mitm_addon import scrub_headers

    headers = {
        "x-custom-token": "opaque_custom_token_123",
        "client_secret": "my-top-secret",
        "session-token": "sess-xyz",
        "content-type": "application/json",
        "accept": "*/*",
    }
    cleaned = scrub_headers(headers)
    assert cleaned["x-custom-token"] == "[REDACTED]"
    assert cleaned["client_secret"] == "[REDACTED]"
    assert cleaned["session-token"] == "[REDACTED]"
    assert cleaned["content-type"] == "application/json"
    assert cleaned["accept"] == "*/*"


def test_mitm_addon_subclass_override_detection(tmp_path):
    """Verifies that a subclass overriding intercept_request is respected by MitmproxyAddon."""
    from sandbox_executor.token_reduction.mitm_addon import MitmproxyAddon, MITMProxyInterceptor

    calls = []

    class CustomInterceptor(MITMProxyInterceptor):
        def intercept_request(self, endpoint, request_json):
            calls.append(endpoint)
            return request_json, None

    addon = MitmproxyAddon(cache_dir=str(tmp_path / "cache"), wire_log_dir=str(tmp_path / "logs"))
    addon.interceptor = CustomInterceptor(cache_dir=str(tmp_path / "cache"))

    class MockRequest:
        def __init__(self):
            self.pretty_url = "https://api.anthropic.com/v1/messages"
            self.headers = {"content-type": "application/json"}
            self._text = json.dumps({"model": "claude-3-5-sonnet", "messages": []})

        def get_text(self):
            return self._text

        def set_text(self, text):
            self._text = text

    flow = SimpleNamespace(
        request=MockRequest(),
        response=None,
        is_cached=False,
    )
    addon.request(flow)
    assert len(calls) == 1
    assert calls[0] == "https://api.anthropic.com/v1/messages"


@pytest.mark.parametrize("status_code", [429, 503])
def test_non_200_transaction_logging_429_503(tmp_path, status_code):
    """Verifies that HTTP 429 and 503 responses are properly logged into transactions.jsonl."""
    from sandbox_executor.token_reduction.mitm_addon import MitmproxyAddon

    wire_dir = tmp_path / "error_wire_logs"
    addon = MitmproxyAddon(cache_dir=str(tmp_path / "cache"), wire_log_dir=str(wire_dir))

    class MockResponse:
        def __init__(self):
            self.status_code = status_code
            self.headers = {"content-type": "application/json"}

        def get_text(self):
            return json.dumps({"error": {"message": f"HTTP {status_code} Error"}})

    flow = SimpleNamespace(
        id=f"flow_{status_code}",
        provider="openai",
        request_start_time=100.0,
        response_headers_time=100.2,
        first_chunk_time=None,
        request=SimpleNamespace(
            pretty_url="https://api.openai.com/v1/chat/completions",
            headers={"content-type": "application/json"},
        ),
        response=MockResponse(),
        raw_request_data={"model": "gpt-4o", "messages": []},
        req_data={"model": "gpt-4o", "messages": []},
        cleaner_result=None,
        is_cached=False,
    )

    addon.response(flow)
    addon.done()

    jsonl_file = wire_dir / "transactions.jsonl"
    assert jsonl_file.exists()
    lines = [json.loads(line) for line in jsonl_file.read_text(encoding="utf-8").strip().split("\n")]
    assert any(rec.get("response", {}).get("status") == status_code for rec in lines)


def test_ab_measure_non_dict_raw_request(tmp_path):
    """Verifies parse_wire_logs_into_result gracefully handles non-dict raw_request bodies."""
    ab = _load_ab_runner()

    wire_dir = tmp_path / "str_req_logs"
    wire_dir.mkdir(parents=True, exist_ok=True)
    tx = {
        "turn_id": 1,
        "flow_id": "f_str",
        "provider": "openai",
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "status_code": 400,
        "raw_request": "unparseable string body",
        "cleaned_request": "unparseable string body",
        "response": {"error": "bad request"},
        "token_counts": {"input_tokens": 0, "output_tokens": 0},
    }
    with open(wire_dir / "transactions.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps(tx) + "\n")

    # Should not raise AttributeError: 'str' object has no attribute 'get'
    res = ab.parse_wire_logs_into_result(wire_dir, 1, 0)
    assert res is not None


def test_shutdown_wire_logs(tmp_path):
    from sandbox_executor.token_reduction.mitm_addon import (
        _get_wire_log_executor,
        dump_wire_transaction,
        shutdown_wire_logs,
    )

    os.environ["HOLON_WIRE_LOGS"] = "1"
    wire_dir = tmp_path / "shutdown_logs"
    wire_dir.mkdir(parents=True, exist_ok=True)
    fut = dump_wire_transaction({"flow_id": "shut1", "turn_id": 1}, str(wire_dir))
    shutdown_wire_logs(wait=True)
    assert fut.done()
    # Re-calling _get_wire_log_executor creates a fresh one
    fresh_executor = _get_wire_log_executor()
    assert fresh_executor is not None
    assert not getattr(fresh_executor, "_shutdown", False)
    shutdown_wire_logs(wait=True)


def test_secret_dict_key_pattern_extended():
    from sandbox_executor.token_reduction.mitm_addon import _SECRET_DICT_KEY_PATTERN

    assert _SECRET_DICT_KEY_PATTERN.match("api-key")
    assert _SECRET_DICT_KEY_PATTERN.match("api_key")
    assert _SECRET_DICT_KEY_PATTERN.match("apikey")
    assert _SECRET_DICT_KEY_PATTERN.match("api-token")
    assert _SECRET_DICT_KEY_PATTERN.match("api_token")
    assert _SECRET_DICT_KEY_PATTERN.match("auth-token")
    assert _SECRET_DICT_KEY_PATTERN.match("auth_token")
    assert _SECRET_DICT_KEY_PATTERN.match("access-token")
    assert _SECRET_DICT_KEY_PATTERN.match("access_token")
    assert _SECRET_DICT_KEY_PATTERN.match("client_secret")
    assert _SECRET_DICT_KEY_PATTERN.match("client-secret")
    assert _SECRET_DICT_KEY_PATTERN.match("session_token")
    assert _SECRET_DICT_KEY_PATTERN.match("session-token")
    assert not _SECRET_DICT_KEY_PATTERN.match("tokens")
    assert not _SECRET_DICT_KEY_PATTERN.match("max_tokens")


def test_interceptor_subclass_override_backward_compatibility():
    from sandbox_executor.token_reduction.mitm_addon import MITMProxyInterceptor

    class CustomOldInterceptor(MITMProxyInterceptor):
        def intercept_request(self, endpoint, request_json):
            modified = dict(request_json)
            modified["custom_key"] = "injected"
            return modified, None

    custom = CustomOldInterceptor(enable_caching=False)
    cleaned, cached, clean_res = custom.intercept_request_with_stats(
        "https://api.openai.com/v1/chat/completions", {"model": "gpt-4o"}
    )
    assert cleaned.get("custom_key") == "injected"
    assert cached is None
    assert clean_res is None


def test_ab_measure_local_cache_hit_token_accounting(tmp_path):
    ab = _load_ab_runner()

    wire_dir = tmp_path / "cache_hit_logs"
    wire_dir.mkdir(parents=True, exist_ok=True)
    tx = {
        "turn_id": 1,
        "flow_id": "f_hit",
        "provider": "openai",
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "status_code": 200,
        "cache": {"action": "HIT"},
        "raw_request": {"model": "gpt-4o", "messages": []},
        "cleaned_request": {"model": "gpt-4o", "messages": []},
        "response": {"usage": {"input_tokens": 500, "output_tokens": 100}},
        "token_counts": {"input_tokens": 500, "output_tokens": 100},
    }
    with open(wire_dir / "transactions.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps(tx) + "\n")

    res = ab.parse_wire_logs_into_result(wire_dir, 1, 0)
    assert res.local_cache_hits == 1
    # On local cache HIT, upstream billed tokens must be 0
    assert res.prompt_tokens == 0
    assert res.output_tokens == 0


def test_secret_dict_key_pattern_oauth_and_id_token():
    from sandbox_executor.token_reduction.mitm_addon import _SECRET_DICT_KEY_PATTERN

    assert _SECRET_DICT_KEY_PATTERN.match("refresh-token")
    assert _SECRET_DICT_KEY_PATTERN.match("refresh_token")
    assert _SECRET_DICT_KEY_PATTERN.match("id-token")
    assert _SECRET_DICT_KEY_PATTERN.match("id_token")


def test_token_counts_unpacking_and_attributes():
    from sandbox_executor.token_reduction.mitm_addon import TokenCounts

    tc = TokenCounts(100, 50, 20, cache_creation_tokens=10, reasoning_tokens=5)
    inp, out, read = tc
    assert inp == 100
    assert out == 50
    assert read == 20
    assert tc.input_tokens == 100
    assert tc.output_tokens == 50
    assert tc.cache_read_tokens == 20
    assert tc.cache_creation_tokens == 10
    assert tc.reasoning_tokens == 5


def test_scrub_payload_tuple_support():
    from sandbox_executor.token_reduction.mitm_addon import scrub_payload

    data_tuple = ({"api_key": "secret123"}, "normal_string")
    scrubbed = scrub_payload(data_tuple)
    assert isinstance(scrubbed, tuple)
    assert scrubbed[0]["api_key"] == "[REDACTED]"
    assert scrubbed[1] == "normal_string"


def test_ab_measure_tier_token_accounting_on_cache_hit(tmp_path):
    ab = _load_ab_runner()

    wire_dir = tmp_path / "cache_hit_tier_logs"
    wire_dir.mkdir(parents=True, exist_ok=True)
    tx = {
        "turn_id": 1,
        "flow_id": "f_hit_tier",
        "provider": "anthropic",
        "endpoint": "https://api.anthropic.com/v1/messages",
        "status_code": 200,
        "cache": {"action": "HIT"},
        "raw_request": {"model": "claude-3-5-sonnet-20241022", "messages": []},
        "cleaned_request": {"model": "claude-3-5-sonnet-20241022", "messages": []},
        "response": {"usage": {"input_tokens": 800, "output_tokens": 150}},
        "token_counts": {"input_tokens": 800, "output_tokens": 150},
    }
    with open(wire_dir / "transactions.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps(tx) + "\n")

    res = ab.parse_wire_logs_into_result(wire_dir, 1, 0)
    assert res.local_cache_hits == 1
    # On local cache HIT, both prompt/output tokens and tier tokens must be 0
    assert res.prompt_tokens == 0
    assert res.output_tokens == 0
    assert res.tier1_tokens == 0
    assert res.tier2_tokens == 0


def test_check_wire_log_disk_usage(tmp_path, capsys):
    ab = _load_ab_runner()

    wire_dir = tmp_path / "quota_logs"
    wire_dir.mkdir(parents=True, exist_ok=True)
    dummy_file = wire_dir / "big.log"
    dummy_file.write_bytes(b"x" * 200)

    ab.check_wire_log_disk_usage(wire_dir, max_mb=0.0001)
    captured = capsys.readouterr()
    assert "Warning: Wire log directory and archives consume" in captured.err
