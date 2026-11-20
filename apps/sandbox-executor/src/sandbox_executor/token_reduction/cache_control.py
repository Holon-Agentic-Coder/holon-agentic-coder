"""Anthropic prompt-cache breakpoints the token-reduction proxy may add without breaking a valid request.

The Anthropic Messages API reads the cache prefix in the order ``tools``, then ``system``, then ``messages``, and checks
the ``cache_control`` blocks it finds in that order: a block without ``ttl`` counts as ``ttl='5m'``, a ``ttl='1h'``
block must not come after a ``ttl='5m'`` block, and a request carries at most :data:`MAX_CACHE_CONTROL_BLOCKS` blocks.
The claude CLI already puts ``ttl='1h'`` blocks on its system blocks, so a plain block injected on the last tool sat
before them and every proxied request failed with::

    system.2.cache_control.ttl: a ttl='1h' cache_control block must not come after a ttl='5m' cache_control block

:func:`inject_cache_controls` therefore lists every slot in prefix order (:func:`cache_slots`), never modifies, moves
or removes an existing block, and gives each injected block a ttl consistent with its neighbours
(:func:`consistent_ttl`), skipping the slot when none is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MAX_CACHE_CONTROL_BLOCKS = 4
TTL_5M = "5m"
TTL_1H = "1h"

# A virtual slot: a string system prompt or a string message content, which becomes a one-block list on injection.
_TEXT = "text"


@dataclass(frozen=True)
class Slot:
    """A position in API prefix order that has, or could take, a ``cache_control`` block.

    Attributes:
        path: Where the slot is: ``("tools", i)``, ``("system", i)``, ``("system",)`` (a string or dict system),
            ``("messages", i)`` (a message-level key), ``("messages", i, j)`` (a content block),
            ``("messages", i, "content")`` (a dict content) or ``("messages", i, "text")`` (a string content).
        block: The dict that holds or would hold the ``cache_control`` key; None for a string (virtual) slot.
        ttl: The existing block's ttl (``"5m"`` when it has none), or None when the slot has no block.
    """

    path: tuple[Any, ...]
    block: dict[str, Any] | None
    ttl: str | None


def _slot(path: tuple[Any, ...], block: Any) -> Slot | None:
    if isinstance(block, str):
        return Slot(path, None, None)
    if not isinstance(block, dict):
        return None
    control = block.get("cache_control")
    if control is None and "cache_control" not in block:
        return Slot(path, block, None)
    ttl = control.get("ttl", TTL_5M) if isinstance(control, dict) else TTL_5M
    return Slot(path, block, str(ttl))


def _list_slots(prefix: tuple[Any, ...], items: list[Any]) -> list[Slot]:
    slots = [_slot((*prefix, index), item) for index, item in enumerate(items) if isinstance(item, dict)]
    return [slot for slot in slots if slot is not None]


def _section_slots(name: str, value: Any) -> list[Slot]:
    """Slots of the ``tools`` or ``system`` section."""
    if isinstance(value, list):
        return _list_slots((name,), value)
    slot = _slot((name,), value) if name == "system" else None
    return [slot] if slot is not None else []


def _message_slots(index: int, message: Any) -> list[Slot]:
    if not isinstance(message, dict):
        return []
    slots = [Slot(("messages", index), message, _slot((), message).ttl)] if "cache_control" in message else []
    content = message.get("content")
    if isinstance(content, list):
        return slots + _list_slots(("messages", index), content)
    key = _TEXT if isinstance(content, str) else "content"
    slot = _slot(("messages", index, key), content)
    return slots + ([slot] if slot is not None else [])


def cache_slots(payload: dict[str, Any]) -> list[Slot]:
    """Every slot of ``payload`` in API prefix order: tools, then system, then messages."""
    slots = _section_slots("tools", payload.get("tools")) + _section_slots("system", payload.get("system"))
    messages = payload.get("messages")
    if isinstance(messages, list):
        for index, message in enumerate(messages):
            slots.extend(_message_slots(index, message))
    return slots


def count_cache_controls(slots: list[Slot]) -> int:
    """How many ``cache_control`` blocks the slots already carry."""
    return sum(1 for slot in slots if slot.ttl is not None)


def consistent_ttl(slots: list[Slot], index: int) -> str | None:
    """The ttl a block injected at ``slots[index]`` may take: ``"5m"`` if allowed, else ``"1h"``, else None.

    A 5m block must not come before an existing 1h block, and a 1h block must not come after an existing 5m block.
    """
    has_5m_before = any(slot.ttl == TTL_5M for slot in slots[:index])
    has_1h_after = any(slot.ttl == TTL_1H for slot in slots[index + 1 :])
    if not has_1h_after:
        return TTL_5M
    if not has_5m_before:
        return TTL_1H
    return None


def _candidate_paths(payload: dict[str, Any]) -> list[tuple[Any, ...]]:
    """The proxy's breakpoints in prefix order: the last tool, the last system block, ``messages[-2]``'s last block."""
    paths: list[tuple[Any, ...]] = []
    tools, system, messages = payload.get("tools"), payload.get("system"), payload.get("messages")
    if isinstance(tools, list) and tools:
        paths.append(("tools", len(tools) - 1))
    if isinstance(system, list) and system:
        paths.append(("system", len(system) - 1))
    elif isinstance(system, str):
        paths.append(("system",))
    if isinstance(messages, list) and len(messages) >= 2 and isinstance(messages[-2], dict):
        content = messages[-2].get("content")
        if isinstance(content, list) and content:
            paths.append(("messages", len(messages) - 2, len(content) - 1))
        elif isinstance(content, str):
            paths.append(("messages", len(messages) - 2, _TEXT))
    return paths


def _apply(payload: dict[str, Any], slot: Slot, ttl: str) -> None:
    control = {"type": "ephemeral"} if ttl == TTL_5M else {"type": "ephemeral", "ttl": ttl}
    if slot.block is not None:
        slot.block["cache_control"] = control
    elif slot.path == ("system",):
        payload["system"] = [{"type": "text", "text": payload["system"], "cache_control": control}]
    else:
        message = payload["messages"][slot.path[1]]
        message["content"] = [{"type": "text", "text": message["content"], "cache_control": control}]


def inject_cache_controls(payload: dict[str, Any]) -> int:
    """Add the proxy's breakpoints where a consistent ttl exists and the block limit allows; return how many.

    A 5m breakpoint is emitted as a plain ``{"type": "ephemeral"}`` block (today's payload shape), a 1h one with
    ``"ttl": "1h"``. Slots are listed again after each injection, so later candidates see the earlier ones.
    """
    injected = 0
    for path in _candidate_paths(payload):
        slots = cache_slots(payload)
        if count_cache_controls(slots) >= MAX_CACHE_CONTROL_BLOCKS:
            break
        index = next((i for i, slot in enumerate(slots) if slot.path == path), None)
        if index is None or slots[index].ttl is not None:
            continue
        ttl = consistent_ttl(slots, index)
        if ttl is not None:
            _apply(payload, slots[index], ttl)
            injected += 1
    return injected
