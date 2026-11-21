"""Bound and redact the agent's output into the block an execution row commits.

The block is a trailing tail of the output under a byte budget, behind a truncation marker; the region around the
byte cut is redacted before the cut, so no partial line (and no secret a cut severed from its key) is committed.
"""

import os
import sys

from sandbox_executor.redaction import _MAX_REDACT_INPUT_LEN, redact_agent_secrets, redact_env_literals

# Room taken out of the byte budget for the truncation marker itself, so the whole committed block
# (marker + tail) fits the budget rather than exceeding it by the marker length. The marker is a
# fixed ~60 character phrase plus at most two 10-digit byte counts, so it stays under 128 bytes for
# any realistic size. A budget below twice this still shares itself with the marker, so the reserve
# is capped at half the budget: a small budget keeps content instead of degrading to a marker-only
# record. Below 256 bytes the marker can still outrun the reserve, which costs budget accuracy, not
# the never-mid-line guarantee.
_TRUNCATION_MARKER_RESERVE: int = 128

# How far back past the byte-cut point the redaction region reaches, so that a key name which sits
# BEFORE its value is inside the text `redact_text` is given. Its anchor is the key name, and the
# separator between a key name and its value (`\s*(:\s*|=)\s*`, `Bearer\s+`) is pure whitespace,
# which includes newlines, so `api_key:` legally sits on the line ABOVE the value that gets it
# masked. Bounding first discarded that anchor line and committed the value whole. This is a COST
# knob (more context means more regex), never a safety knob: the guarantee that the committed block
# carries no unmasked credential comes from `_drop_unanchorable_front`, and it holds however far
# back the anchor actually sat.
_ANCHOR_CONTEXT_BYTES: int = 4096

# The largest byte budget the redactors can serve faithfully. The redaction region is
# `max_bytes + _ANCHOR_CONTEXT_BYTES` long and `redact_text` head/tail-cuts anything longer than
# `_MAX_REDACT_INPUT_LEN`, which is exactly the raw cut this pipeline exists to avoid.
_AGENT_LOG_BYTE_CEILING: int = _MAX_REDACT_INPUT_LEN - _ANCHOR_CONTEXT_BYTES


def get_agent_log_byte_budget() -> int:
    """Retrieve the byte limit for agent log retention from HOLON_AGENT_LOG_BYTES, defaulting to 65536.

    Clamped to `_AGENT_LOG_BYTE_CEILING`, which is `redact_text`'s input cap minus the anchor context
    the byte cut needs: the redaction region is the budget plus that context, and above the cap the
    regex pass head/tail-cuts its own input, which would both discard the newest output the bound
    just retained and sever a key name at its own split. A byte clamp is a safe character clamp
    because UTF-8 never encodes a character in fewer than one byte.
    """
    val = os.getenv("HOLON_AGENT_LOG_BYTES")
    if val:
        try:
            parsed = int(val)
            if parsed > 0:
                if parsed > _AGENT_LOG_BYTE_CEILING:
                    print(
                        f"Warning: HOLON_AGENT_LOG_BYTES={parsed} exceeds the {_AGENT_LOG_BYTE_CEILING} "
                        f"byte redaction limit (the {_MAX_REDACT_INPUT_LEN} byte redaction input cap minus "
                        f"the {_ANCHOR_CONTEXT_BYTES} bytes of anchor context the byte cut needs); bounding "
                        "to the limit instead, because output beyond it is not redacted faithfully.",
                        file=sys.stderr,
                    )
                    return _AGENT_LOG_BYTE_CEILING
                return parsed
        except (ValueError, TypeError):
            pass
    return 65536


def _marker_line(dropped_bytes: int, tail_len: int, reason: str = "") -> str:
    """Render the truncation notice.

    Units, because the two numbers deliberately do not reconcile: `tail_len` is a fact about the
    committed block (the exact byte length of the tail that follows the marker), while
    `dropped_bytes` counts everything discarded, which spans sizes -- bytes of the swept stream before
    the redaction region, the discarded region front, and whole lines trimmed from the already
    redacted text. Masking rewrites sizes as it goes, so `dropped_bytes + tail_len` is neither the
    size of the stream the agent produced nor the budget. Witnesses measured on this shape: an
    env-swept 130,000-byte stream reports `14013 + 51987 = 66000`, a masking-grown 138,000-byte
    stream reports `86807 + 65408 = 152215`. The wording is pinned by many tests; the meaning lives
    here.
    """
    return f"[Agent output truncated: {dropped_bytes} bytes dropped; showing tail {tail_len} bytes{reason}]\n"


def _marker_reason(*causes: str) -> str:
    """Render marker causes as the marker's trailing clauses, in the order they were hit."""
    return "".join(f"; {cause}" for cause in causes if cause)


def _bound_tail(encoded: bytes, max_bytes: int) -> tuple[bytes, int, str]:
    """Cut `encoded` down to a tail that fits the budget next to a marker.

    Returns (tail_bytes, dropped_bytes, marker_cause), where the cause is the bare phrase the marker
    renders, not a punctuated clause.

    The tail must start on a line boundary, or be empty: this helper bounds text and never redacts it,
    so a partial line is the one thing it will not hand back. That is a completeness rule for a
    bound-only helper. It is NOT a credential-safety argument -- a line boundary says nothing about
    whether the key name of a value on the first kept line is still present, and `redact_text`'s
    separator between a key name and its value is pure whitespace that includes newlines, so the
    anchor may sit on a line the cut already discarded. Callers that redact must redact a region and
    then drop its unanchorable front; see `prepare_agent_output_block`.
    """
    reserve = min(_TRUNCATION_MARKER_RESERVE, max_bytes // 2)
    tail_budget = max_bytes - reserve
    tail_bytes = encoded[-tail_budget:] if tail_budget > 0 else b""
    dropped_bytes = len(encoded) - len(tail_bytes)
    newline_at = tail_bytes.find(b"\n")
    if 0 <= newline_at < len(tail_bytes) - 1:
        dropped_bytes += newline_at + 1
        return tail_bytes[newline_at + 1 :], dropped_bytes, ""
    # One unterminated line longer than the whole budget: it cannot be redacted faithfully, so the
    # record keeps the notice and none of the line.
    return b"", dropped_bytes + len(tail_bytes), "no complete line within budget"


def _drop_unanchorable_front(redacted_region: str) -> tuple[str, int]:
    """Discard the front of a redacted region: returns (kept_text, dropped_bytes).

    Drops the leading whitespace, then the next complete line. That line is the only committed line
    whose anchor can sit outside the region: a matched value never spans a line (both value branches
    of `redact_text`'s pattern exclude whitespace and the quoted branch is non-DOTALL) and everything
    between a key name and its value is pure whitespace, so any later line is preceded inside the
    region by that first non-whitespace line, which a whitespace-only separator cannot cross.
    Dropping it is what makes the guarantee structural rather than a measurement of how far back an
    anchor happened to sit, of how much masking shrank or grew the text, or of the value being an
    environment literal.
    """
    leading_len = len(redacted_region) - len(redacted_region.lstrip())
    newline_at = redacted_region.find("\n", leading_len)
    if newline_at < 0:
        # The region's first line is also its last: none of it can be anchored from inside.
        return "", len(redacted_region.encode("utf-8"))
    front = redacted_region[: newline_at + 1]
    return redacted_region[len(front) :], len(front.encode("utf-8"))


def prepare_agent_output_block(raw_output: str, max_bytes: int = 65536) -> tuple[str, bool, int]:
    """Produce the `## Agent Output` block: redacted, and no larger than the byte budget.

    Field contract: the returned text is `marker + tail` and, for any budget of at least twice the
    marker reserve (256 bytes), that block is at most `max_bytes`. Below that the marker itself does
    not shrink, so it outruns its own reserve and the block overshoots by the difference -- a budget
    accuracy cost, never a redaction one.

    Ordering matters four times over:

    1. Sweep environment-literal credentials across the whole stream first, uncapped, while no value
       has been severed. `redact_text` cannot run here: its input cap would discard the newest output
       the bound is about to keep.
    2. Redact a REGION that reaches back over the byte-cut point, so the key name of a credential
       whose value the cut lands on is inside the text the regexes see.
    3. Discard the region's front, which is what makes the guarantee structural rather than a
       measurement of how far back an anchor happened to sit.
    4. Bound the REDACTED bytes. Masking rewrites sizes -- any value shorter than the 7-character
       mask gets LONGER (`api_key=ab` -> `api_key=*******`), growth unbounded in the number of
       matches, which once committed 95 KB under a 65,536 byte budget. Because the byte cut now runs
       after the redaction, that overshoot is impossible by construction rather than refitted after
       the fact, and only whole leading lines are ever dropped.

    INVARIANT: no credential that the redactors can recognise survives the cut unmasked, because the
    committed block never begins with the first value line of the redaction region. A matched value
    can never span a line -- both value branches of `redact_text`'s pattern exclude whitespace and the
    quoted branch is non-DOTALL -- and everything between a key name and its value is pure whitespace
    (`\\s*(:\\s*|=)\\s*`, `Bearer\\s+`). Therefore a value whose anchor fell outside the region can
    only be sitting on the region's first non-whitespace line: every later line is preceded inside the
    region by that non-whitespace line, which the whitespace-only separator cannot cross. Dropping
    that one line makes the guarantee structural: it does not depend on how far back the anchor
    actually was, on how much the masking shrank or grew the text, or on the value being an
    environment literal.

    RESIDUAL, stated honestly: the URL-query redactor's anchor (`[?&]token...=`) can be separated from
    its value by non-whitespace text spanning lines, so a `=`-adjacent secret on the region's SECOND
    line can still be beyond this rule. Broadening `redact_text`'s key alternation and adding entropy
    heuristics are both rejected in the loop ledger as coverage regressions, so that gap is deferred
    to its own intent rather than widened into this change.

    Args:
        raw_output: Combined stdout/stderr of the agent process.
        max_bytes: Maximum allowed byte length of the whole returned block, clamped to
            `_AGENT_LOG_BYTE_CEILING` because redaction above that is not faithful.

    Returns:
        A tuple of (block, is_truncated, dropped_bytes).
    """
    if max_bytes <= 0:
        max_bytes = 65536
    if max_bytes > _AGENT_LOG_BYTE_CEILING:
        # Above that ceiling `redact_text` head/tail-cuts the redaction region itself. That cut is
        # another raw byte cut with the same severing problem as the bound, and it also throws away
        # output this function was asked to retain, so the budget can never exceed what the redactors
        # handle faithfully -- the anchor context the region needs included.
        max_bytes = _AGENT_LOG_BYTE_CEILING

    swept = redact_env_literals(raw_output)
    encoded = swept.encode("utf-8")
    if len(encoded) <= max_bytes:
        # Redact the text that was measured, never the pre-sweep original: sweeping shrinks every
        # credential occurrence, so the original can sit above redact_text's input cap while the swept
        # text fits the budget. Redacting that original lets the cap fire on a path that reports
        # `truncated=False`, and a secret straddling its split is committed unredacted.
        redacted = redact_agent_secrets(swept)
        if len(redacted.encode("utf-8")) <= max_bytes:
            return redacted, False, 0

    reserve = min(_TRUNCATION_MARKER_RESERVE, max_bytes // 2)
    tail_budget = max_bytes - reserve
    redacted_region, dropped_bytes, causes = _redact_cut_region(encoded, tail_budget)

    # Bound the REDACTED bytes, which is the only bound that can honour the budget once masking has
    # rewritten sizes: growing a value past the budget is no longer a refit problem, because nothing
    # is committed that was not already redacted. Whole leading lines are dropped, never a partial
    # line, and the newest bytes are always the ones kept.
    encoded_tail = redacted_region.encode("utf-8")
    pos = 0
    while len(encoded_tail) - pos > tail_budget:
        newline_at = encoded_tail.find(b"\n", pos)
        if newline_at < 0 or newline_at == len(encoded_tail) - 1:
            # No complete line fits: the record keeps the notice and none of the line.
            pos = len(encoded_tail)
            if "no complete line within budget" not in causes:
                causes.append("no complete line within budget")
            break
        pos = newline_at + 1
    dropped_bytes += pos
    encoded_tail = encoded_tail[pos:]

    tail = encoded_tail.decode("utf-8", errors="replace")
    marker = _marker_line(dropped_bytes, len(encoded_tail), _marker_reason(*causes))
    return marker + tail, True, dropped_bytes


def _redact_cut_region(encoded: bytes, tail_budget: int) -> tuple[str, int, list[str]]:
    """Redact the region around the byte cut and drop its unanchorable front.

    Returns (redacted_region, dropped_bytes, marker_causes): the bytes before the region and its dropped front count
    as dropped, and the causes are bare marker phrases in the order they were hit.
    """
    # The byte cut lands at `cut_offset`; the REDACTION region reaches `_ANCHOR_CONTEXT_BYTES` past
    # it and then out to that line's start, so a key name sitting above the value the cut lands on is
    # inside the text the regexes see and gets masked instead of being dropped with the front.
    cut_offset = max(0, len(encoded) - tail_budget)
    context_start = max(0, cut_offset - _ANCHOR_CONTEXT_BYTES)
    context_break = encoded.rfind(b"\n", 0, context_start)
    ideal_start = 0 if context_break < 0 else context_break + 1
    # `redact_text` head/tail-cuts anything longer than its input cap, which is the raw cut this
    # pipeline exists to avoid, so the cap always wins over the context window: a single line longer
    # than the context pushes the ideal start past it and the region is pulled forward instead. The
    # front-drop rule below is what keeps unredactable text out of the block, never this window, so
    # losing context costs diagnostics and not safety -- and the marker says so.
    region_start = max(ideal_start, len(encoded) - _MAX_REDACT_INPUT_LEN)
    causes: list[str] = []
    if region_start > ideal_start:
        causes.append("anchor context dropped")

    dropped_bytes = region_start
    region = encoded[region_start:].decode("utf-8", errors="replace")
    redacted_region = redact_agent_secrets(region)
    if region_start:
        # None of this can be anchored from inside the region, so the region's first value line is
        # never committed.
        redacted_region, front_bytes = _drop_unanchorable_front(redacted_region)
        dropped_bytes += front_bytes
        if not redacted_region:
            causes.append("no complete line within budget")
    return redacted_region, dropped_bytes, causes


def sanitize_agent_output(raw_output: str, max_bytes: int = 65536) -> tuple[str, bool, int]:
    """Bound `raw_output` to a trailing `max_bytes` block with a truncation notice.

    Field contract: the returned text is `marker + tail` and, for any budget of at least twice the
    marker reserve (256 bytes), that block is at most `max_bytes`. The returned tail never begins
    mid-line: it starts immediately after a newline, or the budget could not reach a line boundary at
    all and the tail is empty with the marker saying why. A partial line is never committed, because
    the bytes a byte cut deletes are exactly the bytes the redactors need in order to recognise a
    secret. This bounds only; it does not redact -- a caller that also redacts must redact the region
    around the cut and refit afterwards, which is what `prepare_agent_output_block` does.

    Args:
        raw_output: Raw string output from the agent process.
        max_bytes: Maximum allowed byte length for the whole returned block.

    Returns:
        A tuple of (bounded_output, is_truncated, dropped_bytes).
    """
    if max_bytes <= 0:
        max_bytes = 65536

    encoded = raw_output.encode("utf-8")
    total_bytes = len(encoded)

    if total_bytes <= max_bytes:
        return raw_output, False, 0

    # Leave room for the marker so marker + tail stays inside the budget, without letting the marker
    # swallow a small budget whole.
    tail_bytes, dropped_bytes, cause = _bound_tail(encoded, max_bytes)
    decoded_tail = tail_bytes.decode("utf-8", errors="replace")
    marker = _marker_line(dropped_bytes, len(tail_bytes), _marker_reason(cause))
    return marker + decoded_tail, True, dropped_bytes
