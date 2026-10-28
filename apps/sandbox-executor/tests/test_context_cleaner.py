"""Unit tests for JSONContextCleaner and MITM proxy interceptor (Phase 2 token reduction)."""

from unittest.mock import MagicMock

from sandbox_executor.token_reduction.mitm_addon import MitmproxyAddon, MITMProxyInterceptor
from sandbox_executor.token_reduction.payload_cleaner import JSONContextCleaner


def test_cleaner_init_defaults() -> None:
    cleaner = JSONContextCleaner()
    assert cleaner.enable_deduplication is True
    assert cleaner.enable_prompt_caching is True
    assert cleaner.max_turns == 30

    custom = JSONContextCleaner(enable_deduplication=False, enable_prompt_caching=False, max_turns=10)
    assert custom.enable_deduplication is False
    assert custom.enable_prompt_caching is False
    assert custom.max_turns == 10


def test_process_payload_unknown_provider() -> None:
    cleaner = JSONContextCleaner()
    payload = {"messages": [{"role": "user", "content": "Hello"}]}
    res = cleaner.process_payload(payload, provider="unknown")
    assert res == payload
    assert res is not payload  # deep copy verification


def test_anthropic_deduplication_tool_results() -> None:
    cleaner = JSONContextCleaner(enable_prompt_caching=False)
    large_output = "A" * 150
    short_output = "B" * 50

    messages = [
        # Turn 0: older turn with tool result
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": large_output},
                {"type": "tool_result", "tool_use_id": "call_2", "content": short_output},
            ],
        },
        # Turn 1: older turn repeating tool results
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_3", "content": large_output},
                {"type": "tool_result", "tool_use_id": "call_4", "content": short_output},
            ],
        },
        # Turn 2: history turn
        {"role": "assistant", "content": "Working..."},
        # Turn 3: current turn (recent 2 turns protected from deduplication)
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_5", "content": large_output},
            ],
        },
    ]

    payload = {"messages": messages}
    cleaned = cleaner.process_payload(payload, provider="anthropic")
    cleaned_msgs = cleaned["messages"]

    # Turn 0: retained original large output
    assert cleaned_msgs[0]["content"][0]["content"] == large_output
    assert cleaned_msgs[0]["content"][1]["content"] == short_output

    # Turn 1: older turn repeating large output replaced with placeholder
    assert "[Omitted: Tool result content is identical to Turn 0 (call_1)]" in cleaned_msgs[1]["content"][0]["content"]
    # Short output (<= 100 chars) not omitted
    assert cleaned_msgs[1]["content"][1]["content"] == short_output

    # Turn 3: recent turn protected from deduplication
    assert cleaned_msgs[3]["content"][0]["content"] == large_output


def test_anthropic_deduplication_string_messages() -> None:
    cleaner = JSONContextCleaner(enable_prompt_caching=False)
    long_msg = "X" * 250
    short_msg = "Y" * 50

    messages = [
        {"role": "user", "content": long_msg},
        {"role": "assistant", "content": short_msg},
        {"role": "user", "content": long_msg},  # turn 2: repeating long_msg in older history (turn_count = 6)
        {"role": "assistant", "content": "Working..."},
        {"role": "user", "content": "Recent input"},
        {"role": "assistant", "content": "Done."},
    ]

    payload = {"messages": messages}
    cleaned = cleaner.process_payload(payload, provider="anthropic")
    cleaned_msgs = cleaned["messages"]

    assert cleaned_msgs[0]["content"] == long_msg
    assert "[Omitted: Message content is identical to Turn 0 (turn_0)]" in cleaned_msgs[2]["content"]


def test_anthropic_deduplication_disabled() -> None:
    cleaner = JSONContextCleaner(enable_deduplication=False, enable_prompt_caching=False)
    long_msg = "X" * 250
    messages = [
        {"role": "user", "content": long_msg},
        {"role": "assistant", "content": "Ack"},
        {"role": "user", "content": long_msg},
        {"role": "assistant", "content": "Working..."},
        {"role": "user", "content": "Recent input"},
        {"role": "assistant", "content": "Done"},
    ]

    cleaned = cleaner.process_payload({"messages": messages}, provider="anthropic")
    assert cleaned["messages"][2]["content"] == long_msg


def test_anthropic_cache_control_injection() -> None:
    cleaner = JSONContextCleaner(enable_deduplication=False)

    # 1. System prompt string wrapping and tools list injection
    payload1 = {
        "system": "System instructions here.",
        "tools": [{"name": "read_file", "description": "Read file"}, {"name": "run_command", "description": "Run"}],
        "messages": [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ],
    }

    cleaned1 = cleaner.process_payload(payload1, provider="anthropic")

    # System prompt wrapped to list with cache_control
    assert isinstance(cleaned1["system"], list)
    assert cleaned1["system"][0]["cache_control"] == {"type": "ephemeral"}

    # Tools list last item gets cache_control
    assert cleaned1["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in cleaned1["tools"][0]

    # Messages[-2] (Turn 0: user "Hello") gets cache_control wrapped
    assert isinstance(cleaned1["messages"][-2]["content"], list)
    assert cleaned1["messages"][-2]["content"][0]["cache_control"] == {"type": "ephemeral"}

    # 2. System prompt list and messages[-2] block list content
    payload2 = {
        "system": [{"type": "text", "text": "Base sys"}, {"type": "text", "text": "Extra sys"}],
        "messages": [
            {"role": "user", "content": "First"},
            {"role": "assistant", "content": [{"type": "text", "text": "Response block"}]},
            {"role": "user", "content": "Second"},
        ],
    }

    cleaned2 = cleaner.process_payload(payload2, provider="anthropic")
    assert cleaned2["system"][-1]["cache_control"] == {"type": "ephemeral"}
    # messages[-2] is assistant block list
    assert cleaned2["messages"][-2]["content"][-1]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_history_summarization_and_role_merging() -> None:
    cleaner = JSONContextCleaner(enable_deduplication=False, enable_prompt_caching=False, max_turns=8)

    # Build 12 messages (> max_turns 8 and > _RECENT_TURNS_TO_KEEP 6)
    messages = [
        {"role": "user", "content": "Turn 0: Initial prompt"},
        {"role": "assistant", "content": "Turn 1: Reading workspace files..."},
        {"role": "user", "content": "Turn 2: Continue work"},
        {"role": "assistant", "content": "Turn 3: Editing files..."},
        {"role": "user", "content": "Turn 4: Next step"},
        {"role": "assistant", "content": "Turn 5: Output generation..."},
        {"role": "user", "content": "Turn 6: Refine"},
        {"role": "assistant", "content": "Turn 7: Testing"},
        {"role": "user", "content": "Turn 8: Fix bugs"},
        {"role": "assistant", "content": "Turn 9: Re-test"},
        {"role": "user", "content": "Turn 10: Final check"},
        {"role": "assistant", "content": "Turn 11: Done"},
    ]

    cleaned = cleaner.process_payload({"messages": messages}, provider="anthropic")
    cleaned_msgs = cleaned["messages"]

    # Should summarise intermediate turns
    assert len(cleaned_msgs) < len(messages)
    # Check that summary text exists in user turn
    summary_found = any("[Summary of omitted" in str(msg.get("content")) for msg in cleaned_msgs)
    assert summary_found is True


def test_consecutive_role_merging() -> None:
    cleaner = JSONContextCleaner()

    # String + String consecutive user roles
    msgs_str = [
        {"role": "user", "content": "Part 1"},
        {"role": "user", "content": "Part 2"},
    ]
    merged_str = cleaner._merge_consecutive_roles(msgs_str)
    assert len(merged_str) == 1
    assert merged_str[0]["content"] == "Part 1\n\nPart 2"

    # List + String consecutive user roles
    msgs_mix = [
        {"role": "user", "content": [{"type": "text", "text": "Block 1"}]},
        {"role": "user", "content": "String 2"},
    ]
    merged_mix = cleaner._merge_consecutive_roles(msgs_mix)
    assert len(merged_mix) == 1
    assert isinstance(merged_mix[0]["content"], list)
    assert len(merged_mix[0]["content"]) == 2
    assert merged_mix[0]["content"][1] == {"type": "text", "text": "String 2"}

    # Empty list edge case
    assert cleaner._merge_consecutive_roles([]) == []


def test_openai_cleaning() -> None:
    cleaner = JSONContextCleaner(max_turns=6)
    long_text = "O" * 250

    # 10 messages (> max_turns 6 and > _RECENT_TURNS_TO_KEEP 6)
    payload = {
        "messages": [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": long_text},
            {"role": "assistant", "content": "Intermediate answer"},
            {"role": "user", "content": long_text},  # duplicate string in older turn
            {"role": "assistant", "content": "Second answer"},
            {"role": "user", "content": "Third prompt"},
            {"role": "assistant", "content": "Third answer"},
            {"role": "user", "content": "Fourth prompt"},
            {"role": "assistant", "content": "Fourth answer"},
            {"role": "user", "content": "Latest query"},
        ]
    }

    cleaned = cleaner.process_payload(payload, provider="openai")
    cleaned_msgs = cleaned["messages"]

    # Exceeded max_turns (10 > 6), history is summarised
    assert len(cleaned_msgs) < 10

    # Non-list messages payload returns unmodified
    invalid_payload = {"messages": "not a list"}
    assert cleaner._clean_openai(invalid_payload, {}) == invalid_payload


def test_gemini_cleaning() -> None:
    cleaner = JSONContextCleaner()
    long_text = "G" * 250

    payload = {
        "contents": [
            {"role": "user", "parts": [{"text": long_text}]},
            {"role": "model", "parts": [{"text": "Ack"}]},
            {"role": "user", "parts": [{"text": long_text}]},  # Turn 2: duplicate in older turn (turn_count = 6)
            {"role": "model", "parts": [{"text": "Processing..."}]},
            {"role": "user", "parts": [{"text": "Recent input"}]},
            {"role": "model", "parts": [{"text": "Done"}]},
        ]
    }

    cleaned = cleaner.process_payload(payload, provider="gemini")
    contents = cleaned["contents"]

    assert contents[0]["parts"][0]["text"] == long_text
    assert "[Omitted: Content is identical to Turn 0 (turn_0)]" in contents[2]["parts"][0]["text"]

    # Non-list contents payload returns unmodified
    invalid_payload = {"contents": "not a list"}
    assert cleaner._clean_gemini(invalid_payload, {}) == invalid_payload


def test_thread_safety_local_state() -> None:
    """Verify seen_content_hashes is not shared across calls (I-1 regression test)."""
    cleaner = JSONContextCleaner(enable_prompt_caching=False)
    long_text = "T" * 250

    payload1 = {
        "messages": [
            {"role": "user", "content": long_text},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": long_text},  # turn 2 duplicate in older turn (turn_count = 6)
            {"role": "assistant", "content": "A2"},
            {"role": "user", "content": "Recent prompt"},
            {"role": "assistant", "content": "A3"},
        ]
    }

    # First call deduplicates turn 2
    res1 = cleaner.process_payload(payload1, provider="openai")
    assert "[Omitted:" in res1["messages"][2]["content"]

    # Second call with new payload having long_text only once in turn 0 should NOT omit turn 0
    payload2 = {
        "messages": [
            {"role": "user", "content": long_text},
            {"role": "assistant", "content": "A1"},
        ]
    }
    res2 = cleaner.process_payload(payload2, provider="openai")
    assert res2["messages"][0]["content"] == long_text


def test_mitm_interceptor_and_addon(tmp_path) -> None:
    interceptor = MITMProxyInterceptor()

    # Provider detection
    assert interceptor.detect_provider("https://api.anthropic.com/v1/messages") == "anthropic"
    assert interceptor.detect_provider("https://api.openai.com/v1/chat/completions") == "openai"
    assert interceptor.detect_provider("https://generativelanguage.googleapis.com/v1/models/gemini") == "gemini"
    assert interceptor.detect_provider("https://example.com/api/v1/unknown") == "unknown"

    # Unknown endpoint bypass
    raw = {"data": "test"}
    assert interceptor.intercept_request("https://example.com/api", raw) == (raw, None)

    # Addon flow handling, with its cache and wire logs kept out of the real home and the repository
    addon = MitmproxyAddon(cache_dir=str(tmp_path / "cache"), wire_log_dir=str(tmp_path / "wire-logs"))

    flow_mock = MagicMock()
    flow_mock.request.pretty_url = "https://api.anthropic.com/v1/messages"
    flow_mock.request.get_text.return_value = '{"messages": [{"role": "user", "content": "Hello"}]}'

    addon.request(flow_mock)

    flow_mock.request.set_text.assert_called_once()


def test_anthropic_cache_control_limit_enforcement() -> None:
    cleaner = JSONContextCleaner(enable_deduplication=False, enable_prompt_caching=True)

    # 1. Payload with 3 pre-existing cache_control breakpoints
    payload = {
        "system": [
            {"type": "text", "text": "Sys1", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "Sys2", "cache_control": {"type": "ephemeral"}},
        ],
        "tools": [
            {"name": "tool1", "description": "t1", "cache_control": {"type": "ephemeral"}},
            {"name": "tool2", "description": "t2"},
        ],
        "messages": [
            {"role": "user", "content": "Turn 0"},
            {"role": "assistant", "content": "Turn 1"},
            {"role": "user", "content": "Turn 2"},
            {"role": "assistant", "content": "Turn 3"},
        ],
    }

    cleaned = cleaner.process_payload(payload, provider="anthropic")
    total_tags = cleaner._count_existing_cache_controls(cleaned)
    assert total_tags <= 4
    # Out of tool2 and messages[-2], only 1 should be added because initial count was 3
    assert total_tags == 4

    # 2. Payload with 4 pre-existing cache_control breakpoints
    payload4 = {
        "system": [
            {"type": "text", "text": "Sys1", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "Sys2", "cache_control": {"type": "ephemeral"}},
        ],
        "tools": [
            {"name": "tool1", "description": "t1", "cache_control": {"type": "ephemeral"}},
        ],
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Msg", "cache_control": {"type": "ephemeral"}}],
            },
            {"role": "assistant", "content": "Ans"},
        ],
    }

    cleaned4 = cleaner.process_payload(payload4, provider="anthropic")
    total_tags4 = cleaner._count_existing_cache_controls(cleaned4)
    assert total_tags4 == 4


def test_anthropic_history_boundary_preservation() -> None:
    cleaner = JSONContextCleaner(enable_deduplication=False, enable_prompt_caching=False, max_turns=6)

    messages = [
        {"role": "user", "content": "Turn 0: Initial query"},
        {"role": "assistant", "content": "Turn 1: Response 1"},
        {"role": "user", "content": "Turn 2: Followup 1"},
        {"role": "assistant", "content": "Turn 3: Response 2"},
        {"role": "user", "content": "Turn 4: Followup 2"},
        {"role": "assistant", "content": "Turn 5: Response 3"},
        {"role": "user", "content": "Turn 6: Recent user query"},
        {"role": "assistant", "content": "Turn 7: Recent assistant answer"},
        {"role": "user", "content": "Turn 8: Latest user prompt"},
        {"role": "assistant", "content": "Turn 9: Latest response"},
    ]

    cleaned = cleaner.process_payload({"messages": messages}, provider="anthropic")
    cleaned_msgs = cleaned["messages"]

    # Verify boundary preservation:
    # Index 0 is Turn 0 (initial user message)
    assert cleaned_msgs[0]["content"] == "Turn 0: Initial query"
    assert cleaned_msgs[0]["role"] == "user"

    # Index 1 is summary_msg with assistant role
    assert cleaned_msgs[1]["role"] == "assistant"
    assert "[Summary of omitted" in cleaned_msgs[1]["content"]

    # Index 2 is Turn 4 or Turn 6 user prompt (preserved recent turn), NOT merged into Turn 0
    assert cleaned_msgs[2]["role"] == "user"
    assert "Turn" in cleaned_msgs[2]["content"]
    assert "[Summary of omitted" not in cleaned_msgs[2]["content"]

    # Index 3 is assistant response
    assert cleaned_msgs[3]["role"] == "assistant"


def test_openai_role_merging_and_summarization() -> None:
    cleaner = JSONContextCleaner(enable_deduplication=False, max_turns=6)

    messages = [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "Turn 1"},
        {"role": "user", "content": "Turn 1 duplicate role"},
        {"role": "assistant", "content": "Turn 2"},
        {"role": "user", "content": "Turn 3"},
        {"role": "assistant", "content": "Turn 4"},
        {"role": "user", "content": "Turn 5"},
        {"role": "assistant", "content": "Turn 6"},
        {"role": "user", "content": "Turn 7"},
        {"role": "assistant", "content": "Turn 8"},
        {"role": "user", "content": "Turn 9"},
        {"role": "assistant", "content": "Turn 10"},
        {"role": "user", "content": "Recent user turn"},
    ]

    cleaned = cleaner.process_payload({"messages": messages}, provider="openai")
    cleaned_msgs = cleaned["messages"]

    assert len(cleaned_msgs) < len(messages)
    # Check consecutive role merging was executed
    roles = [m["role"] for m in cleaned_msgs]
    # No adjacent identical roles
    for i in range(len(roles) - 1):
        assert roles[i] != roles[i + 1]


def test_openai_turn0_preservation_during_summarization() -> None:
    cleaner = JSONContextCleaner(enable_deduplication=False, max_turns=4)

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Initial user task prompt (Turn 0)"},
        {"role": "assistant", "content": "Turn 1 response"},
        {"role": "user", "content": "Turn 2 query"},
        {"role": "assistant", "content": "Turn 2 response"},
        {"role": "user", "content": "Turn 3 query"},
        {"role": "assistant", "content": "Turn 3 response"},
        {"role": "user", "content": "Recent user prompt"},
    ]

    cleaned = cleaner.process_payload({"messages": messages}, provider="openai")
    cleaned_msgs = cleaned["messages"]

    # Verify initial system prompt (index 0) and initial user prompt (index 1) are preserved
    assert cleaned_msgs[0] == {"role": "system", "content": "You are a helpful assistant."}
    assert cleaned_msgs[1] == {"role": "user", "content": "Initial user task prompt (Turn 0)"}
    # Verify summary message inserted
    assert cleaned_msgs[2]["role"] == "assistant"
    assert "[Summary of omitted" in cleaned_msgs[2]["content"]
    # Verify suffix preserved
    assert cleaned_msgs[-1] == {"role": "user", "content": "Recent user prompt"}


def test_gemini_history_truncation_and_summarization() -> None:
    cleaner = JSONContextCleaner(enable_deduplication=False, max_turns=6)

    contents = [
        {"role": "user", "parts": [{"text": "Turn 0: Start"}]},
        {"role": "model", "parts": [{"text": "Turn 1: Ack"}]},
        {"role": "user", "parts": [{"text": "Turn 2: Query 1"}]},
        {"role": "model", "parts": [{"text": "Turn 3: Ans 1"}]},
        {"role": "user", "parts": [{"text": "Turn 4: Query 2"}]},
        {"role": "model", "parts": [{"text": "Turn 5: Ans 2"}]},
        {"role": "user", "parts": [{"text": "Turn 6: Query 3"}]},
        {"role": "model", "parts": [{"text": "Turn 7: Ans 3"}]},
        {"role": "user", "parts": [{"text": "Turn 8: Query 4"}]},
        {"role": "model", "parts": [{"text": "Turn 9: Ans 4"}]},
        {"role": "user", "parts": [{"text": "Turn 10: Recent user"}]},
        {"role": "model", "parts": [{"text": "Turn 11: Recent model"}]},
    ]

    cleaned = cleaner.process_payload({"contents": contents}, provider="gemini")
    cleaned_contents = cleaned["contents"]

    assert len(cleaned_contents) < len(contents)
    summary_found = any(
        "[Summary of omitted" in p.get("text", "") for turn in cleaned_contents for p in turn.get("parts", [])
    )
    assert summary_found is True


def test_anthropic_deduplicate_list_tool_outputs() -> None:
    cleaner = JSONContextCleaner(enable_prompt_caching=False)
    large_list_content = [{"type": "text", "text": "D" * 150}]

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_10", "content": large_list_content},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_11", "content": large_list_content},
            ],
        },
        {"role": "assistant", "content": "Thinking..."},
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_12", "content": "Recent"},
            ],
        },
    ]

    cleaned = cleaner.process_payload({"messages": messages}, provider="anthropic")
    cleaned_msgs = cleaned["messages"]
    assert "[Omitted: Tool result content is identical to Turn 0 (call_10)]" in cleaned_msgs[1]["content"][0]["content"]


def test_anthropic_deduplicate_list_text_blocks() -> None:
    cleaner = JSONContextCleaner(enable_prompt_caching=False)
    large_text = "T" * 150

    messages = [
        # Turn 0: older turn with list text content
        {
            "role": "user",
            "content": [{"type": "text", "text": large_text}],
        },
        # Turn 1: older turn repeating list text content
        {
            "role": "user",
            "content": [{"type": "text", "text": large_text}],
        },
        # Turn 2: assistant turn
        {"role": "assistant", "content": "Acknowledged."},
        # Turn 3: current turn (recent turn protected)
        {
            "role": "user",
            "content": [{"type": "text", "text": large_text}],
        },
    ]

    payload = {"messages": messages}
    cleaned = cleaner.process_payload(payload, provider="anthropic")
    cleaned_msgs = cleaned["messages"]

    # Turn 0: retained original large text
    assert cleaned_msgs[0]["content"][0]["text"] == large_text

    # Turn 1: older turn repeating large text replaced with placeholder
    assert "[Omitted: Message content is identical to Turn 0 (turn_0)]" in cleaned_msgs[1]["content"][0]["text"]

    # Turn 3: recent turn protected from deduplication
    assert cleaned_msgs[3]["content"][0]["text"] == large_text


def test_non_dict_payload_items_handling() -> None:
    cleaner = JSONContextCleaner()

    # Anthropic with non-dict elements in messages and content list
    anthropic_payload = {
        "messages": [
            "malformed_string_turn",
            12345,
            None,
            {
                "role": "user",
                "content": ["malformed_item", None, 999, {"type": "text", "text": "Valid text block"}],
            },
        ]
    }
    cleaned_anthropic = cleaner.process_payload(anthropic_payload, provider="anthropic")
    assert cleaned_anthropic["messages"][0] == "malformed_string_turn"
    assert cleaned_anthropic["messages"][1] == 12345
    assert cleaned_anthropic["messages"][2] is None
    assert cleaned_anthropic["messages"][3]["content"][0] == "malformed_item"

    # OpenAI with non-dict elements in messages
    openai_payload = {
        "messages": [
            "invalid_msg",
            None,
            {"role": "user", "content": "Valid query"},
        ]
    }
    cleaned_openai = cleaner.process_payload(openai_payload, provider="openai")
    assert cleaned_openai["messages"][0] == "invalid_msg"
    assert cleaned_openai["messages"][1] is None
    assert cleaned_openai["messages"][2]["content"] == "Valid query"

    # Gemini with non-dict elements in contents and parts
    gemini_payload = {
        "contents": [
            "invalid_turn",
            None,
            {
                "role": "user",
                "parts": ["invalid_part", None, {"text": "Valid text"}],
            },
        ]
    }
    cleaned_gemini = cleaner.process_payload(gemini_payload, provider="gemini")
    assert cleaned_gemini["contents"][0] == "invalid_turn"
    assert cleaned_gemini["contents"][1] is None
    assert cleaned_gemini["contents"][2]["parts"][0] == "invalid_part"
