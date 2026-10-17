"""Disk-backed hybrid & semantic caching layer for LLM prompts."""

import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from typing import Any

logger = logging.getLogger(__name__)


class HybridCacheStore:
    """Hybrid exact prefix tree and semantic local cache store for LLM responses."""

    def __init__(
        self,
        cache_dir: str | None = None,
        similarity_threshold: float = 0.85,
        candidate_limit: int = 100,
    ) -> None:
        """Initialise HybridCacheStore.

        Args:
            cache_dir: Directory path for SQLite storage. Defaults to ~/.holon/cache.
            similarity_threshold: Jaccard similarity score threshold (0.0 to 1.0) required for a semantic match hit.
            candidate_limit: Maximum number of recent candidate cache entries to consider for semantic search.
        """
        if cache_dir is None:
            cache_dir = os.path.expanduser("~/.holon/cache")
        os.makedirs(cache_dir, exist_ok=True)

        self.cache_dir = cache_dir
        self.similarity_threshold = similarity_threshold
        self.candidate_limit = candidate_limit
        self.db_path = os.path.join(cache_dir, "llm_cache.db")
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS prompt_cache (
                    key TEXT PRIMARY KEY,
                    provider TEXT,
                    prompt_normalized TEXT,
                    response_json TEXT,
                    created_at REAL,
                    hit_count INTEGER DEFAULT 0
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_prompt_cache_provider_created "
                "ON prompt_cache (provider, created_at DESC)"
            )
            conn.commit()

    def generate_prefix_key(self, payload: dict[str, Any], provider: str = "anthropic") -> str:
        """Generates a stable prefix-tree hash key from the payload system and message turns."""
        normalized_str = self.normalize_payload(payload, provider)
        return hashlib.sha256(f"{provider}:{normalized_str}".encode()).hexdigest()

    def normalize_payload(self, payload: dict[str, Any], provider: str = "anthropic") -> str:
        """Normalizes payload by stripping transient variables like timestamps, run IDs, and temporary tokens."""
        raw_json = json.dumps(payload, sort_keys=True)
        # Strip ISO timestamps
        norm = re.sub(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?",
            "<TIMESTAMP>",
            raw_json,
        )
        # Strip UUIDs / hexadecimal run hashes
        norm = re.sub(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            "<UUID>",
            norm,
        )
        # Strip random digits in task IDs / run IDs
        norm = re.sub(r"\b(?:task|run)[-_]\d+\b", "task-<ID>", norm)
        return norm

    def _extract_user_content(self, payload: dict[str, Any], max_recent_turns: int = 2) -> str:
        """Extracts user message content specifically to avoid system prompt and JSON key token pollution.
        Prioritises the most recent user turns to prevent long conversation history token dilution.
        """

        def _extract_item_text(item: Any) -> list[str]:
            texts: list[str] = []
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict):
                if "text" in item and isinstance(item["text"], str):
                    texts.append(item["text"])
                if item.get("type") == "tool_result":
                    tr_content = item.get("content")
                    if isinstance(tr_content, str):
                        texts.append(tr_content)
                    elif isinstance(tr_content, list):
                        for sub in tr_content:
                            texts.extend(_extract_item_text(sub))
                    elif isinstance(tr_content, dict):
                        texts.extend(_extract_item_text(tr_content))
            return texts

        user_texts = []
        messages = payload.get("messages")
        if isinstance(messages, list):
            user_msgs = [msg for msg in messages if isinstance(msg, dict) and msg.get("role") == "user"]
            recent_msgs = user_msgs[-max_recent_turns:] if max_recent_turns > 0 else user_msgs
            for msg in recent_msgs:
                content = msg.get("content")
                if isinstance(content, str):
                    user_texts.append(content)
                elif isinstance(content, list):
                    for item in content:
                        extracted = _extract_item_text(item)
                        if extracted:
                            user_texts.extend(extracted)
                        elif isinstance(item, str):
                            user_texts.append(item)
                elif isinstance(content, dict):
                    extracted = _extract_item_text(content)
                    if extracted:
                        user_texts.extend(extracted)
                elif content is not None:
                    user_texts.append(json.dumps(content))
        contents = payload.get("contents")
        if isinstance(contents, list):
            user_turns = [
                turn for turn in contents if isinstance(turn, dict) and turn.get("role") in ("user", None, "")
            ]
            recent_turns = user_turns[-max_recent_turns:] if max_recent_turns > 0 else user_turns
            for turn in recent_turns:
                parts = turn.get("parts")
                if isinstance(parts, list):
                    for part in parts:
                        extracted = _extract_item_text(part)
                        if extracted:
                            user_texts.extend(extracted)
                        elif isinstance(part, str):
                            user_texts.append(part)
        if not user_texts and "prompt" in payload and isinstance(payload["prompt"], str):
            user_texts.append(payload["prompt"])
        return " ".join(user_texts)

    def _extract_system_prompt(self, payload: dict[str, Any]) -> str:
        """Extracts normalized system instructions from Anthropic/Gemini/OpenAI payload schemas."""
        system_texts: list[str] = []

        # 1. Top-level 'system' or 'system_instruction' field (Anthropic/Gemini)
        system_val = payload.get("system") or payload.get("system_instruction")
        if system_val is not None:
            if isinstance(system_val, str):
                system_texts.append(system_val)
            elif isinstance(system_val, list):
                for item in system_val:
                    if isinstance(item, str):
                        system_texts.append(item)
                    elif isinstance(item, dict) and "text" in item and isinstance(item["text"], str):
                        system_texts.append(item["text"])
            elif isinstance(system_val, dict):
                if "text" in system_val and isinstance(system_val["text"], str):
                    system_texts.append(system_val["text"])
                elif "parts" in system_val and isinstance(system_val["parts"], list):
                    for part in system_val["parts"]:
                        if isinstance(part, dict) and "text" in part and isinstance(part["text"], str):
                            system_texts.append(part["text"])

        # 2. System messages in 'messages' array (OpenAI schema)
        messages = payload.get("messages")
        if isinstance(messages, list):
            for msg in messages:
                if isinstance(msg, dict) and msg.get("role") == "system":
                    content = msg.get("content")
                    if isinstance(content, str):
                        system_texts.append(content)
                    elif isinstance(content, list):
                        for item in content:
                            if isinstance(item, str):
                                system_texts.append(item)
                            elif isinstance(item, dict) and "text" in item and isinstance(item["text"], str):
                                system_texts.append(item["text"])

        return " ".join(system_texts).strip()

    def get(self, payload: dict[str, Any], provider: str = "anthropic") -> dict[str, Any] | None:
        """Looks up cached response by exact prefix match or semantic similarity match.

        Returns:
            dict[str, Any] | None: Cached response JSON payload if match found, else None.
        """
        prefix_key = self.generate_prefix_key(payload, provider)

        # 1. Exact Prefix Matching
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT response_json FROM prompt_cache WHERE key = ? AND provider = ?",
                (prefix_key, provider),
            )
            row = cursor.fetchone()
            if row:
                (resp_json,) = row
                cursor.execute(
                    "UPDATE prompt_cache SET hit_count = hit_count + 1 WHERE key = ? AND provider = ?",
                    (prefix_key, provider),
                )
                conn.commit()
                logger.info("Exact cache hit for key %s", prefix_key[:10])
                return json.loads(resp_json)

        # 2. Semantic Similarity Matching
        target_norm = self.normalize_payload(payload, provider)
        try:
            target_payload = json.loads(target_norm)
        except Exception:
            target_payload = payload

        target_user_content = self._extract_user_content(target_payload)
        target_tokens = set(re.findall(r"\w+", target_user_content.lower()))

        if not target_tokens:
            return None

        target_system_prompt = self._extract_system_prompt(target_payload)

        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT key, prompt_normalized, response_json
                FROM prompt_cache
                WHERE provider = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (provider, self.candidate_limit),
            )
            rows = cursor.fetchall()

            for key, stored_norm, resp_json in rows:
                try:
                    stored_payload = json.loads(stored_norm)
                except Exception:
                    continue

                # Require exact match on system prompt before evaluating user turn similarity
                if target_system_prompt != self._extract_system_prompt(stored_payload):
                    continue

                stored_user_content = self._extract_user_content(stored_payload)
                stored_tokens = set(re.findall(r"\w+", stored_user_content.lower()))
                if not stored_tokens:
                    continue

                intersection = len(target_tokens & stored_tokens)
                union = len(target_tokens | stored_tokens)
                similarity = intersection / union if union > 0 else 0.0

                if similarity >= self.similarity_threshold:
                    cursor.execute(
                        "UPDATE prompt_cache SET hit_count = hit_count + 1 WHERE key = ?",
                        (key,),
                    )
                    conn.commit()
                    logger.info(
                        "Semantic cache hit (similarity=%.2f) for key %s",
                        similarity,
                        key[:10],
                    )
                    return json.loads(resp_json)

        return None

    def put(self, payload: dict[str, Any], response: dict[str, Any], provider: str = "anthropic") -> None:
        """Stores a prompt payload and LLM response in the cache store."""
        prefix_key = self.generate_prefix_key(payload, provider)
        norm_prompt = self.normalize_payload(payload, provider)
        resp_json = json.dumps(response)
        now = time.time()

        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO prompt_cache
                (key, provider, prompt_normalized, response_json, created_at, hit_count)
                VALUES (?, ?, ?, ?, ?, 0)
                ON CONFLICT(key) DO UPDATE SET
                    response_json = excluded.response_json,
                    created_at = excluded.created_at
                """,
                (prefix_key, provider, norm_prompt, resp_json, now),
            )
            conn.commit()
            logger.info("Stored response in cache for key %s", prefix_key[:10])

    def clear(self) -> None:
        """Clears all entries from the cache store."""
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM prompt_cache")
            conn.commit()
