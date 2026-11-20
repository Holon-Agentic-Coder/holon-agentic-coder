"""Hermetic tests for TTL-consistent cache_control injection in the token-reduction proxy's payload cleaner."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from sandbox_executor.token_reduction.cache_control import (
    MAX_CACHE_CONTROL_BLOCKS,
    cache_slots,
    consistent_ttl,
    count_cache_controls,
    inject_cache_controls,
)
from sandbox_executor.token_reduction.payload_cleaner import JSONContextCleaner

ONE_HOUR = {"type": "ephemeral", "ttl": "1h"}
PLAIN = {"type": "ephemeral"}


def _clean(payload: dict[str, Any]) -> dict[str, Any]:
    cleaner = JSONContextCleaner(enable_deduplication=False, enable_prompt_caching=True)
    return cleaner.process_payload(payload, provider="anthropic")


def _ttls(payload: dict[str, Any]) -> list[str]:
    return [slot.ttl for slot in cache_slots(payload) if slot.ttl is not None]


def assert_cache_order_valid(payload: dict[str, Any]) -> None:
    """The API's rule in one place: in prefix order (tools, system, messages) no 1h block follows a 5m block."""
    ttls = _ttls(payload)
    for index, ttl in enumerate(ttls):
        if ttl == "1h":
            assert "5m" not in ttls[:index], f"a ttl='1h' block comes after a ttl='5m' block: {ttls}"


def _existing_controls(payload: dict[str, Any]) -> list[tuple[tuple[Any, ...], Any]]:
    return [
        (slot.path, copy.deepcopy(slot.block["cache_control"]))
        for slot in cache_slots(payload)
        if slot.ttl is not None and slot.block is not None
    ]


def _incident_payload() -> dict[str, Any]:
    """The claude CLI shape behind the incident: plain tools, 1h system blocks after a plain one, a conversation."""
    return {
        "tools": [{"name": "Read", "description": "r"}, {"name": "Write", "description": "w"}],
        "system": [
            {"type": "text", "text": "billing header"},
            {"type": "text", "text": "You are Claude Code.", "cache_control": dict(ONE_HOUR)},
            {"type": "text", "text": "Project instructions", "cache_control": dict(ONE_HOUR)},
        ],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "Plan the intent."}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Reading files."}]},
            {"role": "user", "content": [{"type": "text", "text": "Continue."}]},
        ],
    }


def test_incident_payload_gets_no_5m_block_before_a_1h_block() -> None:
    payload = _incident_payload()
    before = _existing_controls(payload)

    cleaned = _clean(payload)

    assert_cache_order_valid(cleaned)
    assert cleaned["tools"][-1].get("cache_control") in (None, ONE_HOUR)
    assert all(control in [c for _, c in _existing_controls(cleaned)] for _, control in before)
    assert count_cache_controls(cache_slots(cleaned)) <= MAX_CACHE_CONTROL_BLOCKS


def test_incident_payload_keeps_at_least_one_injected_breakpoint() -> None:
    # The learning target: TTL-aware injection still adds a breakpoint next to the CLI's own 1h system blocks.
    payload = _incident_payload()
    assert inject_cache_controls(payload) == 2
    assert payload["tools"][-1]["cache_control"] == ONE_HOUR
    assert payload["messages"][-2]["content"][-1]["cache_control"] == PLAIN
    assert_cache_order_valid(payload)


def test_existing_blocks_are_unchanged() -> None:
    payload = _incident_payload()
    payload["messages"][0]["content"][0]["cache_control"] = {"type": "ephemeral", "ttl": "5m"}
    payload["system"][0]["cache_control"] = {"type": "ephemeral", "ttl": "1h", "extra": "kept"}
    before = _existing_controls(payload)

    cleaned = _clean(payload)

    after = dict(_existing_controls(cleaned))
    for path, control in before:
        assert after[path] == control


def _payload_with_existing(count: int) -> dict[str, Any]:
    """A payload whose first ``count`` content blocks of messages[0] carry 1h blocks (consistent with any injection)."""
    blocks = [{"type": "text", "text": f"b{i}"} for i in range(max(count, 1))]
    for block in blocks[:count]:
        block["cache_control"] = dict(ONE_HOUR)
    return {
        "tools": [{"name": "t", "description": "d"}],
        "system": "You are a helpful assistant.",
        "messages": [
            {"role": "user", "content": blocks},
            {"role": "assistant", "content": "Ack."},
            {"role": "user", "content": "Next."},
        ],
    }


@pytest.mark.parametrize("existing", range(6))
def test_block_limit_is_respected(existing: int) -> None:
    payload = _payload_with_existing(existing)
    before = copy.deepcopy(payload)

    cleaned = _clean(payload)

    total = count_cache_controls(cache_slots(cleaned))
    if existing >= MAX_CACHE_CONTROL_BLOCKS:
        assert total == existing
        assert cleaned == before
    else:
        assert total <= MAX_CACHE_CONTROL_BLOCKS
    assert_cache_order_valid(cleaned)


def test_four_existing_blocks_get_none_and_three_get_at_most_one() -> None:
    assert inject_cache_controls(_payload_with_existing(4)) == 0
    assert inject_cache_controls(_payload_with_existing(3)) <= 1


def test_slot_between_a_5m_and_a_later_1h_block_is_skipped() -> None:
    payload = {
        "tools": [{"name": "t", "description": "d", "cache_control": dict(PLAIN)}],
        "system": [{"type": "text", "text": "Squeezed"}],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "q", "cache_control": dict(ONE_HOUR)}]},
            {"role": "assistant", "content": "a"},
        ],
    }
    # The existing payload is itself inconsistent; the proxy must not add to it, nor touch it.
    before = copy.deepcopy(payload)
    slots = cache_slots(payload)
    system_index = next(i for i, slot in enumerate(slots) if slot.path == ("system", 0))
    assert consistent_ttl(slots, system_index) is None

    inject_cache_controls(payload)

    assert "cache_control" not in payload["system"][0]
    assert payload["tools"] == before["tools"]
    assert payload["messages"][0] == before["messages"][0]


def test_1h_is_chosen_only_when_5m_would_precede_a_1h_block() -> None:
    payload = {
        "system": [{"type": "text", "text": "s"}],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "q"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "a", "cache_control": dict(ONE_HOUR)}]},
            {"role": "user", "content": "next"},
        ],
    }
    assert inject_cache_controls(payload) == 1
    assert payload["system"][0]["cache_control"] == ONE_HOUR
    assert_cache_order_valid(payload)


def test_plain_payload_keeps_todays_shape() -> None:
    payload = {
        "system": "You are a helpful assistant.",
        "tools": [{"name": "read_file", "description": "Reads a file"}],
        "messages": [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
            {"role": "user", "content": "Again"},
        ],
    }

    cleaned = _clean(payload)

    assert cleaned["system"] == [{"type": "text", "text": "You are a helpful assistant.", "cache_control": PLAIN}]
    assert cleaned["tools"][-1]["cache_control"] == PLAIN
    assert cleaned["messages"][-2]["content"] == [{"type": "text", "text": "Hello", "cache_control": PLAIN}]
    assert_cache_order_valid(cleaned)


def test_slots_follow_api_prefix_order() -> None:
    payload = {
        "messages": [{"role": "user", "cache_control": dict(PLAIN), "content": [{"type": "text", "text": "q"}]}],
        "system": {"type": "text", "text": "s", "cache_control": dict(ONE_HOUR)},
        "tools": [{"name": "t"}],
    }
    assert [slot.path for slot in cache_slots(payload)] == [
        ("tools", 0),
        ("system",),
        ("messages", 0),
        ("messages", 0, 0),
    ]
    assert [slot.ttl for slot in cache_slots(payload)] == [None, "1h", "5m", None]
