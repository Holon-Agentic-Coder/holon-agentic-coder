"""Context cleaning, tool output deduplication, and prompt cache optimisation for LLM payloads."""

import copy
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_RECENT_TURNS_TO_KEEP = 6
"""Number of recent conversation turns preserved verbatim at the end of the history during summarisation."""


@dataclass
class CleaningResult:
    """Result of context cleaning, tool output deduplication, and prompt cache optimisation."""

    payload: dict[str, Any]
    tool_outputs_omitted: int = 0
    turns_summarized: int = 0
    cache_control_injected: int = 0
    chars_saved: int = 0


class JSONContextCleaner:
    """Cleans JSON LLM payloads by deduplicating tool outputs, summarising history,

    and inserting prompt cache control breakpoints for Anthropic API schemas.
    """

    def __init__(
        self,
        enable_deduplication: bool = True,
        enable_prompt_caching: bool = True,
        max_turns: int = 30,
    ):
        self.enable_deduplication = enable_deduplication
        self.enable_prompt_caching = enable_prompt_caching
        self.max_turns = max_turns

    def process_payload(self, payload: dict[str, Any], provider: str = "anthropic") -> dict[str, Any]:
        """Processes and optimises an outgoing LLM JSON request payload.

        Args:
            payload: Parsed JSON payload dictionary.
            provider: Provider format ('anthropic', 'openai', or 'gemini').

        Returns:
            dict[str, Any]: Optimised JSON payload dictionary.
        """
        return self.process_payload_with_stats(payload, provider=provider).payload

    def process_payload_with_stats(self, payload: dict[str, Any], provider: str = "anthropic") -> CleaningResult:
        """Processes and optimises an outgoing LLM JSON request payload and returns metrics.

        Args:
            payload: Parsed JSON payload dictionary.
            provider: Provider format ('anthropic', 'openai', or 'gemini').

        Returns:
            CleaningResult: Dataclass containing the cleaned payload and execution stats.
        """
        seen_content_hashes: dict[str, tuple[int, str]] = {}
        cleaned_payload = copy.deepcopy(payload)
        stats = {
            "tool_outputs_omitted": 0,
            "turns_summarized": 0,
            "cache_control_injected": 0,
        }

        if provider == "anthropic":
            cleaned_payload = self._clean_anthropic(cleaned_payload, seen_content_hashes, stats=stats)
        elif provider == "gemini":
            cleaned_payload = self._clean_gemini(cleaned_payload, seen_content_hashes, stats=stats)
        else:
            cleaned_payload = self._clean_openai(cleaned_payload, seen_content_hashes, stats=stats)

        try:
            raw_chars = len(json.dumps(payload))
            cleaned_chars = len(json.dumps(cleaned_payload))
            chars_saved = max(0, raw_chars - cleaned_chars)
        except Exception:
            chars_saved = 0

        return CleaningResult(
            payload=cleaned_payload,
            tool_outputs_omitted=stats["tool_outputs_omitted"],
            turns_summarized=stats["turns_summarized"],
            cache_control_injected=stats["cache_control_injected"],
            chars_saved=chars_saved,
        )

    def _clean_anthropic(
        self,
        payload: dict[str, Any],
        seen_content_hashes: dict[str, tuple[int, str]],
        stats: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        messages = payload.get("messages", [])
        if not isinstance(messages, list):
            return payload

        # 1. Deduplicate tool outputs in history turns
        if self.enable_deduplication:
            messages = self._deduplicate_anthropic_tool_outputs(messages, seen_content_hashes, stats=stats)

        # 2. History summarisation if message turns exceed max_turns
        if len(messages) > self.max_turns:
            messages = self._summarize_anthropic_history(messages, stats=stats)
            messages = self._merge_consecutive_roles(messages)

        payload["messages"] = messages

        # 3. Automatic Prompt Cache Breakpoints Insertion for Anthropic
        if self.enable_prompt_caching:
            payload = self._inject_anthropic_cache_control(payload, stats=stats)

        return payload

    def _deduplicate_anthropic_tool_outputs(
        self,
        messages: list[dict[str, Any]],
        seen_content_hashes: dict[str, tuple[int, str]],
        stats: dict[str, int] | None = None,
    ) -> list[dict[str, Any]]:
        cleaned_messages = []
        turn_count = len(messages)

        for turn_idx, msg in enumerate(messages):
            if not isinstance(msg, dict):
                cleaned_messages.append(msg)
                continue
            msg_copy = copy.deepcopy(msg)
            # Only deduplicate in older history turns (leave current turn intact)
            is_older_turn = turn_idx < (turn_count - 2)
            content = msg_copy.get("content")

            if isinstance(content, list):
                cleaned_content = []
                for item in content:
                    if not isinstance(item, dict):
                        cleaned_content.append(item)
                        continue
                    item_copy = copy.deepcopy(item)
                    item_type = item_copy.get("type")
                    if item_type == "tool_result":
                        tool_out = item_copy.get("content", "")
                        resource_name = item_copy.get("tool_use_id", f"tool_result_{turn_idx}")

                        if isinstance(tool_out, str) and len(tool_out) > 100:
                            content_hash = hashlib.sha256(tool_out.encode("utf-8")).hexdigest()
                            if content_hash in seen_content_hashes and is_older_turn:
                                prev_turn, prev_res = seen_content_hashes[content_hash]
                                item_copy["content"] = (
                                    f"[Omitted: Tool result content is identical to Turn {prev_turn} ({prev_res})]"
                                )
                                if stats is not None:
                                    stats["tool_outputs_omitted"] += 1
                            else:
                                seen_content_hashes[content_hash] = (
                                    turn_idx,
                                    resource_name,
                                )
                        elif isinstance(tool_out, list):
                            serialized = json.dumps(tool_out, sort_keys=True)
                            if len(serialized) > 100:
                                content_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
                                if content_hash in seen_content_hashes and is_older_turn:
                                    prev_turn, prev_res = seen_content_hashes[content_hash]
                                    item_copy["content"] = (
                                        f"[Omitted: Tool result content is identical to Turn {prev_turn} ({prev_res})]"
                                    )
                                    if stats is not None:
                                        stats["tool_outputs_omitted"] += 1
                                else:
                                    seen_content_hashes[content_hash] = (
                                        turn_idx,
                                        resource_name,
                                    )
                    elif item_type == "text":
                        text_out = item_copy.get("text", "")
                        if isinstance(text_out, str) and len(text_out) > 100:
                            content_hash = hashlib.sha256(text_out.encode("utf-8")).hexdigest()
                            if content_hash in seen_content_hashes and is_older_turn:
                                prev_turn, prev_res = seen_content_hashes[content_hash]
                                item_copy["text"] = (
                                    f"[Omitted: Message content is identical to Turn {prev_turn} ({prev_res})]"
                                )
                                if stats is not None:
                                    stats["tool_outputs_omitted"] += 1
                            else:
                                seen_content_hashes[content_hash] = (
                                    turn_idx,
                                    f"turn_{turn_idx}",
                                )

                    cleaned_content.append(item_copy)
                msg_copy["content"] = cleaned_content

            elif isinstance(content, str) and len(content) > 200:
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if content_hash in seen_content_hashes and is_older_turn:
                    prev_turn, prev_res = seen_content_hashes[content_hash]
                    msg_copy["content"] = f"[Omitted: Message content is identical to Turn {prev_turn} ({prev_res})]"
                    if stats is not None:
                        stats["tool_outputs_omitted"] += 1
                else:
                    seen_content_hashes[content_hash] = (turn_idx, f"turn_{turn_idx}")

            cleaned_messages.append(msg_copy)

        return cleaned_messages

    def _is_clean_user_message(self, msg: dict[str, Any], provider: str) -> bool:
        if not isinstance(msg, dict):
            return False
        role = msg.get("role", "user" if provider == "gemini" else "")
        if role != "user":
            return False

        content = msg.get("content")
        if provider == "anthropic":
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        return False
            return True
        elif provider in ("openai", "gemini"):
            return True
        return False

    def _summarize_anthropic_history(
        self, messages: list[dict[str, Any]], stats: dict[str, int] | None = None
    ) -> list[dict[str, Any]]:
        target_idx = len(messages) - _RECENT_TURNS_TO_KEEP
        suffix_idx = None
        for i in range(target_idx, 0, -1):
            if self._is_clean_user_message(messages[i], "anthropic"):
                suffix_idx = i
                break
        if suffix_idx is None:
            for i in range(target_idx + 1, len(messages)):
                if self._is_clean_user_message(messages[i], "anthropic"):
                    suffix_idx = i
                    break

        if suffix_idx is None or suffix_idx <= 1:
            return messages

        prefix = messages[:1]
        suffix = messages[suffix_idx:]
        middle = messages[1:suffix_idx]

        if stats is not None:
            stats["turns_summarized"] += len(middle)

        summary_text = f"[Summary of omitted {len(middle)} intermediate conversation turns]"
        summary_msg = {
            "role": "assistant",
            "content": summary_text,
        }

        return [*prefix, summary_msg, *suffix]

    def _merge_consecutive_roles(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not messages:
            return []
        merged = []
        for msg in messages:
            if not isinstance(msg, dict):
                merged.append(copy.deepcopy(msg))
                continue
            if not merged:
                merged.append(copy.deepcopy(msg))
                continue
            prev = merged[-1]
            if not isinstance(prev, dict):
                merged.append(copy.deepcopy(msg))
                continue
            if prev.get("role") == msg.get("role"):
                if "content" in prev or "content" in msg:
                    prev_content = prev.get("content")
                    curr_content = msg.get("content")

                    if isinstance(prev_content, list) or isinstance(curr_content, list):
                        prev_blocks = []
                        if isinstance(prev_content, list):
                            prev_blocks.extend(copy.deepcopy(prev_content))
                        elif isinstance(prev_content, str):
                            prev_blocks.append({"type": "text", "text": prev_content})

                        curr_blocks = []
                        if isinstance(curr_content, list):
                            curr_blocks.extend(copy.deepcopy(curr_content))
                        elif isinstance(curr_content, str):
                            curr_blocks.append({"type": "text", "text": curr_content})

                        prev["content"] = prev_blocks + curr_blocks
                    else:
                        prev["content"] = str(prev_content) + "\n\n" + str(curr_content)

                if "parts" in prev or "parts" in msg:
                    prev_parts = prev.get("parts", [])
                    curr_parts = msg.get("parts", [])
                    if isinstance(prev_parts, list) and isinstance(curr_parts, list):
                        prev["parts"] = copy.deepcopy(prev_parts) + copy.deepcopy(curr_parts)
            else:
                merged.append(copy.deepcopy(msg))
        return merged

    def _count_existing_cache_controls(self, payload: dict[str, Any]) -> int:
        count = 0

        system = payload.get("system")
        if isinstance(system, list):
            for block in system:
                if isinstance(block, dict) and "cache_control" in block:
                    count += 1
        elif isinstance(system, dict) and "cache_control" in system:
            count += 1

        tools = payload.get("tools")
        if isinstance(tools, list):
            for tool in tools:
                if isinstance(tool, dict) and "cache_control" in tool:
                    count += 1

        messages = payload.get("messages")
        if isinstance(messages, list):
            for msg in messages:
                if isinstance(msg, dict):
                    if "cache_control" in msg:
                        count += 1
                    content = msg.get("content")
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and "cache_control" in block:
                                count += 1
                    elif isinstance(content, dict) and "cache_control" in content:
                        count += 1

        return count

    def _inject_anthropic_cache_control(
        self, payload: dict[str, Any], stats: dict[str, int] | None = None
    ) -> dict[str, Any]:
        existing_count = self._count_existing_cache_controls(payload)
        max_allowed = 4
        budget = max_allowed - existing_count
        if budget <= 0:
            return payload

        injected = 0
        # Inject cache_control on system prompt block
        system = payload.get("system")
        if isinstance(system, list) and len(system) > 0:
            last_sys = system[-1]
            if isinstance(last_sys, dict) and "cache_control" not in last_sys:
                last_sys["cache_control"] = {"type": "ephemeral"}
                budget -= 1
                injected += 1
        elif isinstance(system, str):
            payload["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
            budget -= 1
            injected += 1

        # Inject cache_control on tools definition if present
        if budget > 0:
            tools = payload.get("tools")
            if isinstance(tools, list) and len(tools) > 0:
                last_tool = tools[-1]
                if isinstance(last_tool, dict) and "cache_control" not in last_tool:
                    last_tool["cache_control"] = {"type": "ephemeral"}
                    budget -= 1
                    injected += 1

        # Inject cache_control on recent messages history turn
        if budget > 0:
            messages = payload.get("messages", [])
            if isinstance(messages, list) and len(messages) >= 2:
                target_msg = messages[-2]
                if isinstance(target_msg, dict):
                    content = target_msg.get("content")
                    if isinstance(content, list) and len(content) > 0:
                        last_block = content[-1]
                        if isinstance(last_block, dict) and "cache_control" not in last_block:
                            last_block["cache_control"] = {"type": "ephemeral"}
                            budget -= 1
                            injected += 1
                    elif isinstance(content, str):
                        target_msg["content"] = [
                            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
                        ]
                        budget -= 1
                        injected += 1

        if stats is not None:
            stats["cache_control_injected"] += injected

        return payload

    def _clean_openai(
        self,
        payload: dict[str, Any],
        seen_content_hashes: dict[str, tuple[int, str]],
        stats: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        messages = payload.get("messages", [])
        if not isinstance(messages, list):
            return payload

        turn_count = len(messages)
        cleaned_messages = []

        for turn_idx, msg in enumerate(messages):
            if not isinstance(msg, dict):
                cleaned_messages.append(msg)
                continue
            msg_copy = copy.deepcopy(msg)
            is_older_turn = turn_idx < (turn_count - 2)
            content = msg_copy.get("content", "")

            if isinstance(content, str) and len(content) > 200:
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if content_hash in seen_content_hashes and is_older_turn:
                    prev_turn, prev_res = seen_content_hashes[content_hash]
                    msg_copy["content"] = f"[Omitted: Message content is identical to Turn {prev_turn} ({prev_res})]"
                    if stats is not None:
                        stats["tool_outputs_omitted"] += 1
                else:
                    seen_content_hashes[content_hash] = (turn_idx, f"turn_{turn_idx}")

            cleaned_messages.append(msg_copy)

        if len(cleaned_messages) > self.max_turns:
            prefix_len = (
                2
                if (
                    len(cleaned_messages) > 1
                    and isinstance(cleaned_messages[0], dict)
                    and cleaned_messages[0].get("role") == "system"
                    and isinstance(cleaned_messages[1], dict)
                    and cleaned_messages[1].get("role") == "user"
                )
                else 1
            )
            target_idx = max(prefix_len + 1, len(cleaned_messages) - _RECENT_TURNS_TO_KEEP)
            suffix_idx = None
            # Search forward first from target_idx to find clean user message that leaves middle turns to summarise
            for i in range(target_idx, len(cleaned_messages)):
                if self._is_clean_user_message(cleaned_messages[i], "openai"):
                    suffix_idx = i
                    break
            # Fallback: search backward down to prefix_len + 1
            if suffix_idx is None:
                for i in range(target_idx - 1, prefix_len, -1):
                    if self._is_clean_user_message(cleaned_messages[i], "openai"):
                        suffix_idx = i
                        break

            if suffix_idx is not None and suffix_idx > prefix_len:
                prefix = cleaned_messages[:prefix_len]
                suffix = cleaned_messages[suffix_idx:]
                middle_count = len(cleaned_messages) - len(prefix) - len(suffix)
                if middle_count > 0:
                    if stats is not None:
                        stats["turns_summarized"] += middle_count
                    summary_msg = {
                        "role": "assistant",
                        "content": f"[Summary of omitted {middle_count} intermediate conversation turns]",
                    }
                    cleaned_messages = [*prefix, summary_msg, *suffix]
                    cleaned_messages = self._merge_consecutive_roles(cleaned_messages)

        payload["messages"] = cleaned_messages
        return payload

    def _clean_gemini(
        self,
        payload: dict[str, Any],
        seen_content_hashes: dict[str, tuple[int, str]],
        stats: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        contents = payload.get("contents")
        if contents is None or not isinstance(contents, list):
            return payload

        turn_count = len(contents)
        cleaned_contents = []

        for turn_idx, turn in enumerate(contents):
            if not isinstance(turn, dict):
                cleaned_contents.append(turn)
                continue
            turn_copy = copy.deepcopy(turn)
            is_older_turn = turn_idx < (turn_count - 2)
            parts = turn_copy.get("parts", [])
            cleaned_parts = []

            if isinstance(parts, list):
                for part in parts:
                    if not isinstance(part, dict):
                        cleaned_parts.append(part)
                        continue
                    part_copy = copy.deepcopy(part)
                    text = part_copy.get("text", "")
                    if isinstance(text, str) and len(text) > 200:
                        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                        if text_hash in seen_content_hashes and is_older_turn:
                            prev_turn, prev_res = seen_content_hashes[text_hash]
                            part_copy["text"] = f"[Omitted: Content is identical to Turn {prev_turn} ({prev_res})]"
                            if stats is not None:
                                stats["tool_outputs_omitted"] += 1
                        else:
                            seen_content_hashes[text_hash] = (turn_idx, f"turn_{turn_idx}")

                    cleaned_parts.append(part_copy)

            turn_copy["parts"] = cleaned_parts
            cleaned_contents.append(turn_copy)

        if len(cleaned_contents) > self.max_turns:
            target_idx = len(cleaned_contents) - _RECENT_TURNS_TO_KEEP
            suffix_idx = None
            for i in range(target_idx, 0, -1):
                if self._is_clean_user_message(cleaned_contents[i], "gemini"):
                    suffix_idx = i
                    break
            if suffix_idx is None:
                for i in range(target_idx + 1, len(cleaned_contents)):
                    if self._is_clean_user_message(cleaned_contents[i], "gemini"):
                        suffix_idx = i
                        break

            if suffix_idx is not None and suffix_idx > 1:
                prefix = cleaned_contents[:1]
                suffix = cleaned_contents[suffix_idx:]
                middle_count = len(cleaned_contents) - len(prefix) - len(suffix)
                if middle_count > 0:
                    if stats is not None:
                        stats["turns_summarized"] += middle_count
                    summary_msg = {
                        "role": "model",
                        "parts": [{"text": f"[Summary of omitted {middle_count} intermediate conversation turns]"}],
                    }
                    cleaned_contents = [*prefix, summary_msg, *suffix]
                    cleaned_contents = self._merge_consecutive_roles(cleaned_contents)

        payload["contents"] = cleaned_contents
        return payload
