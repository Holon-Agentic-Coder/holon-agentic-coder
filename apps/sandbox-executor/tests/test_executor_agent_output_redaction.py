"""Tests for ``entrypoint/agent_output_block.py`` and its redaction helpers: redaction and byte bounds of the output."""

import io
import os
import re
import unittest
from unittest.mock import patch

from tests.executor_fixtures import (
    _agent_turns_use_run_cmd,  # noqa: F401  (autouse fixture)
    _boundary_cut_offsets,
    _exact_filler,
    _fake_token,
    _superseded_bound_then_redact,
    _workspace_is_mounted,  # noqa: F401  (autouse fixture)
)
from tests.leak_assertions import make_gh_probe


class TestExecutor(unittest.TestCase):
    def test_agent_output_byte_bound_never_strands_secret_fragment(self):
        """Test that no credential fragment survives the pipeline at any straddle offset.

        Also pins why the ordering and the line-aligned cut both matter: a raw byte cut followed by
        redaction strands a fragment for a non-empty set of offsets.
        """
        from sandbox_executor.entrypoint.executor import redact_agent_secrets, sanitize_agent_output

        budget = 256
        straddle_value = make_gh_probe()
        windows = [straddle_value[start : start + 8] for start in range(len(straddle_value) - 7)]

        truncated_cases = 0
        severed_offsets = []
        with patch.dict(os.environ, {"GITHUB_TOKEN": straddle_value, "GH_TOKEN": straddle_value}):
            for offset in range(1, len(straddle_value) - 1):
                suffix = "y" * (budget + offset - len(straddle_value) - 1)
                stream = f"GH_TOKEN={straddle_value}\n{suffix}"
                self.assertGreater(len(stream), budget, "the byte budget must be exceeded for this case")

                # Shipped order: redact the whole stream, then bound, then sweep again.
                bounded, truncated, _dropped = sanitize_agent_output(redact_agent_secrets(stream), budget)
                truncated_cases += 1 if truncated else 0
                result = redact_agent_secrets(bounded)
                for window in windows:
                    self.assertNotIn(window, result, f"Secret fragment {window!r} survived at offset {offset}.")

                # Counter-example, kept so this test cannot silently become vacuous: the superseded
                # raw byte cut followed by redaction, emulated directly because sanitize_agent_output
                # now refuses to split a line.
                raw_cut = stream.encode()[-budget:].decode("utf-8", errors="replace")
                if any(w in redact_agent_secrets(raw_cut) for w in windows):
                    severed_offsets.append(offset)

        self.assertGreater(truncated_cases, 0, "some offsets must actually exercise truncation")
        self.assertTrue(
            severed_offsets,
            "bounding before redacting must demonstrably strand a fragment, or this test proves nothing",
        )

    def test_agent_output_unalignable_cut_drops_the_severed_token(self):
        """Test that a byte cut with no reachable line boundary never commits a severed credential.

        Regression: the unalignable branch of the tail bound kept the raw bytes, so a credential that
        is not one of the swept env literal values and whose key name the cut severed (``api_key=``
        reduced to ``key=``, which is not a key-name anchor) was committed intact, because every
        later pass needs bytes the cut had already discarded. The tail now drops the first
        whitespace-delimited fragment, which is by construction the token the cut landed inside.
        """
        from sandbox_executor.entrypoint.executor import (
            _TRUNCATION_MARKER_RESERVE,
            redact_agent_secrets,
            redact_env_literals,
            redact_text,
            sanitize_agent_output,
        )

        budget = 1024
        tail_budget = budget - _TRUNCATION_MARKER_RESERVE
        secret = _fake_token("sk_live_")
        unit = f"api_key={secret}"
        windows = [secret[start : start + 8] for start in range(len(secret) - 7)]

        def superseded_cut(stream: str) -> str:
            """The superseded pipeline: raw byte cut, fixed 4096 look-ahead, then the old redaction."""
            tail = stream.encode("utf-8")[-budget:]
            newline_at = tail.find(b"\n")
            if 0 <= newline_at < len(tail) - 1 and newline_at <= 4096:
                tail = tail[newline_at + 1 :]
            notice = f"[Agent output truncated: 0 bytes dropped; showing tail {len(tail)} bytes]\n"
            return redact_env_literals(redact_text(notice + tail.decode("utf-8", errors="replace")))

        truncated_cases = 0
        superseded_leaks = []
        superseded_whole_secret_leaks = []
        with patch.dict(os.environ, {"GITHUB_TOKEN": "", "GH_TOKEN": "", "HOLON_AGENT_KEY": ""}):
            # The literal sweep cannot rescue this credential; only the cut geometry can, because it
            # is not the value of any environment variable the pipeline knows.
            self.assertEqual(redact_env_literals(secret), secret)
            for cut_at in range(1, len(unit)):
                # Exactly `cut_at` bytes of `unit` fall to the cut, the rest of the retained tail is
                # padding, and the final line is unterminated: no newline is reachable from the cut.
                stream = ("noise line\n" * 200) + unit + "y" * (tail_budget - (len(unit) - cut_at))
                self.assertGreater(len(stream), budget, "the byte budget must be exceeded for this case")

                bounded, truncated, _dropped = sanitize_agent_output(redact_env_literals(stream), budget)
                truncated_cases += 1 if truncated else 0
                result = redact_agent_secrets(bounded)
                for window in windows:
                    self.assertNotIn(window, result, f"Secret fragment {window!r} survived at cut {cut_at}.")

                # Counter-example, kept so this test cannot silently become vacuous. The superseded
                # cut landed `reserve` bytes earlier, so the credential is placed for that geometry
                # as well; it then strands a fragment (or the whole credential) at the same offset.
                legacy_stream = ("noise line\n" * 200) + unit + "y" * (budget - (len(unit) - cut_at))
                legacy = superseded_cut(legacy_stream)
                if any(window in legacy for window in windows):
                    superseded_leaks.append(cut_at)
                    if secret in legacy:
                        superseded_whole_secret_leaks.append(cut_at)

        self.assertEqual(truncated_cases, len(unit) - 1, "every offset must actually truncate")
        self.assertTrue(
            superseded_leaks,
            "the superseded raw cut must demonstrably strand a fragment, or this test proves nothing",
        )
        self.assertTrue(
            superseded_whole_secret_leaks,
            "the superseded raw cut must demonstrably commit the whole credential once the key name "
            "is severed, or this test proves nothing about the reported witness",
        )

    def test_agent_output_cut_never_splits_a_secret_from_its_anchor(self):
        """Test that a byte cut cannot separate a credential from the key name that gets it masked.

        Regression F-IT5-1: the iteration-4 fix dropped the leading whitespace-delimited fragment of
        an unalignable cut on the premise that a credential never contains whitespace. That premise
        is false in the case that matters: redaction is anchored on the key NAME, and the anchor often
        sits a token ahead of the value (``api_key: SECRET``, spaced ``=``, pretty JSON, ``Bearer``).
        For those shapes the heuristic deleted the anchor and committed the credential whole, which is
        worse than the raw cut it replaced. The shipped rule is that the tail starts on a line
        boundary or is empty, so no partial line is ever committed.
        """
        from sandbox_executor.entrypoint.executor import (
            _TRUNCATION_MARKER_RESERVE,
            redact_agent_secrets,
            redact_env_literals,
            sanitize_agent_output,
        )

        budget = 65536  # the default, so this witnesses the shipped configuration
        # The budget is shared with the marker, so the cut lands this far into the credential line.
        tail_budget = budget - _TRUNCATION_MARKER_RESERVE
        secret = _fake_token("sk-proj-", "9f3Aq7ZxR2tKp8WmB4VdNc6Ye1Hg")
        # Opaque on purpose: no listed provider prefix may rescue these shapes.
        self.assertIsNone(re.match(r"sk_live_|sk_test_", secret))
        windows = [secret[start : start + 8] for start in range(len(secret) - 7)]

        shapes = {
            "single token": f"api_key={secret}",
            "space after colon": f"api_key: {secret}",
            "spaced equals": f"api_key = {secret}",
            "pretty json": f'{{\n  "api_key": "{secret}"}}',
            "bearer": f"Authorization: Bearer {secret}",
            "column padded": f"password:    {secret}",
        }

        def superseded_fragment_heuristic(stream: str) -> str:
            """The iteration-4 rule, kept as a counter-example so it cannot quietly come back."""
            tail = stream.encode("utf-8")[-tail_budget:]
            newline_at = tail.find(b"\n")
            if 0 <= newline_at < len(tail) - 1 and newline_at <= max(4096, budget // 2):
                tail = tail[newline_at + 1 :]
            else:
                tail = tail[re.match(rb"\S*", tail).end() :]
            notice = f"[Agent output truncated: 0 bytes dropped; showing tail {len(tail)} bytes]\n"
            return redact_agent_secrets(notice + tail.decode("utf-8", errors="replace"))

        superseded_leaks = {}
        noise = "noise line\n" * 100
        with patch.dict(os.environ, {"GITHUB_TOKEN": "", "GH_TOKEN": "", "HOLON_AGENT_KEY": ""}):
            self.assertEqual(redact_env_literals(secret), secret, "the sweep must not know this value")
            for label, unit in shapes.items():
                # Without a cut the same bytes are masked: the cut, not redactor scope, is the defect.
                baseline, _trunc, _dropped = sanitize_agent_output(redact_env_literals("n\n" * 400 + unit), budget)
                self.assertNotIn(secret[:8], redact_agent_secrets(baseline))

                legacy_leaks = 0
                for cut_at in range(1, len(unit)):
                    stream = noise + unit + "y" * (tail_budget - (len(unit) - cut_at))
                    bounded, truncated, _ = sanitize_agent_output(redact_env_literals(stream), budget)
                    self.assertTrue(truncated)
                    result = redact_agent_secrets(bounded)
                    for window in windows:
                        self.assertNotIn(window, result, f"{label}: fragment {window!r} survived cut {cut_at}.")
                    if label == "single token" or not legacy_leaks:
                        legacy = superseded_fragment_heuristic(stream)
                        if any(window in legacy for window in windows):
                            legacy_leaks += 1
                superseded_leaks[label] = legacy_leaks

        # The replaced heuristic must demonstrably leak on the split-anchor shapes, or this test has
        # no counter-example and proves nothing about why the rule changed. (The pretty-JSON shape
        # carries its own newline, so both rules align it and it is swept but not asserted here.)
        self.assertGreater(superseded_leaks["space after colon"], 0, str(superseded_leaks))
        self.assertGreater(superseded_leaks["spaced equals"], 0, str(superseded_leaks))
        self.assertGreater(superseded_leaks["bearer"], 0, str(superseded_leaks))
        self.assertEqual(superseded_leaks["single token"], 0, "the shape the old rule handled still passes")

    def test_agent_output_anchor_on_a_preceding_line_is_redacted(self):
        """Test that a key name sitting ABOVE its value survives the byte cut. Regression F-IT8-1.

        Redaction is anchored on the key NAME and the separator between a key name and its value
        (`\\s*(:\\s*|=)\\s*`, `Bearer\\s+`) is pure whitespace, which includes newlines. So `api_key:`
        legally sits on the line above the value that gets it masked, and the shipped-until-now
        ordering -- bound the stream, then redact the tail -- discarded that anchor line together with
        the rest of the partial line and committed the credential whole. Aligning the cut to a line
        boundary (a5ee8ce) never closed this: alignment says where the tail starts, not whether the
        anchor line is inside the window. The pipeline now redacts a region that reaches back past the
        cut, so these shapes are masked instead of committed, and the region's unanchorable front is
        dropped rather than trusted.
        """
        from sandbox_executor.entrypoint.executor import (
            _TRUNCATION_MARKER_RESERVE,
            prepare_agent_output_block,
            redact_env_literals,
        )

        budget = 65536  # the default, so this witnesses the shipped configuration
        tail_budget = budget - _TRUNCATION_MARKER_RESERVE
        head = 16384
        secret = _fake_token("sk-proj-", "9f3Aq7ZxR2tKp8WmB4VdNc6Ye1Hg")
        windows = [secret[start : start + 8] for start in range(len(secret) - 7)]

        shapes = {
            "yaml key line": f"api_key:\n    {secret}",
            "pretty json key line": f'"api_key":\n    "{secret}"',
            "wrapped bearer header": f"Authorization: Bearer\n  {secret}",
            "nested yaml": f"credentials:\n  api_key:\n    {secret}",
        }

        superseded_leaks = {}
        masked_shapes = []
        with patch.dict(os.environ, {"GITHUB_TOKEN": "", "GH_TOKEN": "", "HOLON_AGENT_KEY": ""}):
            self.assertEqual(redact_env_literals(secret), secret, "the uncapped sweep must not know this value")
            for label, cred in shapes.items():
                masked, superseded = 0, 0
                # Hoist uncut stream check outside the cut loop: verify redactor
                # capability on uncut text once per shape.
                tail_len_0 = tail_budget - len(cred) - 1
                uncut_stream = _exact_filler(head, "head") + cred + "\n" + _exact_filler(tail_len_0, "tail")
                uncut, _trunc, _dropped = prepare_agent_output_block(uncut_stream, 100_000)
                for window in windows:
                    self.assertNotIn(window, uncut, f"{label}: uncut stream was not masked")

                for k in _boundary_cut_offsets(cred, secret):
                    # The raw byte cut lands exactly k bytes into the credential block, exercising
                    # boundary cut offsets across syntax boundaries.
                    tail_len = tail_budget - len(cred) - 1 + k
                    stream = _exact_filler(head, "head") + cred + "\n" + _exact_filler(tail_len, "tail")
                    self.assertEqual(len(stream.encode("utf-8")) - tail_budget, head + k, "cut geometry")

                    block, truncated, _dropped = prepare_agent_output_block(stream, budget)
                    self.assertTrue(truncated)
                    for window in windows:
                        self.assertNotIn(window, block, f"{label}: fragment {window!r} survived cut {k}")
                    if "*******" in block:
                        masked += 1
                    # One leaking offset is the counter-example; the old pipeline is slow, so stop after it.
                    if not superseded:
                        old_block = _superseded_bound_then_redact(stream, budget)
                        superseded += any(window in old_block for window in windows)
                self.assertGreater(masked, 0, f"{label}: the anchor was never in reach, so context did not reach")
                # The replaced ordering must demonstrably leak these shapes, or this test carries no
                # counter-example and proves nothing about why the region redaction exists.
                self.assertGreater(superseded, 0, f"{label}: superseded ordering did not reproduce F-IT8-1")
                superseded_leaks[label] = superseded
                masked_shapes.append(label)

        self.assertEqual(len(masked_shapes), len(shapes), str(superseded_leaks))

    def test_agent_output_region_first_value_line_is_never_committed(self):
        """Test the structural half of F-IT8-1: an anchor beyond the region still cannot be committed.

        The whitespace between a key name and its value is unbounded, so an anchor can sit arbitrarily
        far above its value -- further back than `_ANCHOR_CONTEXT_BYTES`, past anywhere the redaction
        region can reach. Masking such a value is impossible, so safety cannot rest on the window's
        size. It rests on the drop instead: the committed block never begins with the region's first
        non-whitespace line, because a matched value never spans a line (both value branches of
        `redact_text`'s pattern exclude whitespace, the quoted branch is non-DOTALL) and every later
        line is preceded inside the region by a non-whitespace line that a whitespace-only separator
        cannot cross. The witness here is a credential the redactors CAN mask when nothing is cut, so
        a leak would be caused by the cut alone.
        """
        from sandbox_executor.entrypoint.executor import (
            _ANCHOR_CONTEXT_BYTES,
            _TRUNCATION_MARKER_RESERVE,
            prepare_agent_output_block,
        )

        budget = 65536
        tail_budget = budget - _TRUNCATION_MARKER_RESERVE
        secret = _fake_token("sk-proj-", "9f3Aq7ZxR2tKp8WmB4VdNc6Ye1Hg")
        windows = [secret[start : start + 8] for start in range(len(secret) - 7)]

        head = _exact_filler(8192, "head")
        anchor = "api_key:\n"
        gap = "\n" * (_ANCHOR_CONTEXT_BYTES * 2)  # whitespace the separator happily crosses
        prefix = head + anchor + gap
        # Land the cut 2048 bytes above the value line, inside the gap, so the region opens below the
        # anchor and the value line is the first thing in it that could carry a credential.
        cut = len(prefix.encode("utf-8")) - 2048
        tail = _exact_filler(tail_budget - len(secret) - 1 - 2048, "tail")
        stream = prefix + secret + "\n" + tail
        self.assertEqual(len(stream.encode("utf-8")) - tail_budget, cut, "cut geometry")
        region_start = cut - _ANCHOR_CONTEXT_BYTES
        anchor_end = len(head.encode("utf-8")) + len(anchor)
        self.assertLess(anchor_end, region_start, "the witness needs the anchor OUTSIDE the region")
        self.assertLess(region_start, len(prefix.encode("utf-8")), "the value line must open the region")

        with patch.dict(os.environ, {"GITHUB_TOKEN": "", "GH_TOKEN": "", "HOLON_AGENT_KEY": ""}):
            uncut, _trunc, _dropped = prepare_agent_output_block(stream, 100_000)
            self.assertNotIn(secret, uncut, "the redactor must be able to anchor this value when nothing is cut")

            block, truncated, _dropped = prepare_agent_output_block(stream, budget)
            self.assertTrue(truncated)
            for window in windows:
                self.assertNotIn(window, block, f"fragment {window!r} survived as the region's first line")

            superseded = _superseded_bound_then_redact(stream, budget)
            self.assertTrue(
                any(window in superseded for window in windows),
                "the superseded bound-then-redact ordering must commit this credential",
            )

    def test_redact_agent_secrets_masks_unanchored_provider_tokens(self):
        """Test that well-known provider credentials are masked without a key-name anchor.

        Regression: `redact_text` anchors on a key name, so a whole provider token reported as prose
        matched nothing and was committed verbatim. The sweep is a closed list of literal prefixes,
        so legitimate high-entropy diagnostics must still survive untouched.
        """
        from sandbox_executor.entrypoint.executor import redact_agent_secrets, redact_env_literals, redact_text

        tokens = [
            _fake_token("sk_live_"),
            _fake_token("sk_test_"),
            _fake_token("pk_live_"),
            _fake_token("pk_test_"),
            _fake_token("ghp_"),
            _fake_token("gho_"),
            _fake_token("ghu_"),
            _fake_token("ghs_"),
            _fake_token("ghr_"),
            _fake_token("github_pat_"),
            _fake_token("glpat-"),
            _fake_token("xoxb-"),
            _fake_token("AIza", "A1234567890abcdefghijklmnopqrstuvw"),
            # AKIA's shape is uppercase-only, so no filler can be spliced inside it; the prefix is
            # split across two literals instead, which keeps the vendor pattern out of the file.
            ("AK" + "IA") + "FAKEEXAMPLEKEY01",
        ]
        benign = (
            "upload payload U1VDQ0VTU19EQVRBX2hlbGxvd29ybGRzdHVmZg== digest "
            "8f14e45fceea167a5d36dedd4bea2543 build 0123456789abcdef0123456789abcdef\n"
        )

        with patch.dict(os.environ, {"GITHUB_TOKEN": "", "GH_TOKEN": "", "HOLON_AGENT_KEY": ""}):
            for token in tokens:
                line = f"agent reported token was: {token}\n"
                # Counter-example: the superseded pass leaves an unanchored provider token verbatim.
                self.assertEqual(redact_env_literals(redact_text(line)), line)
                masked = redact_agent_secrets(line)
                self.assertNotIn(token, masked, f"Provider token {token!r} leaked without a key= anchor")
                self.assertIn("*******", masked)

            # No generic high-entropy rule: base64 and hex diagnostics must not be shredded.
            self.assertEqual(redact_agent_secrets(benign), benign)

    def test_agent_log_byte_budget_clamps_and_falls_back(self):
        """Test the HOLON_AGENT_LOG_BYTES clamp to the redaction ceiling and its fallbacks."""
        from sandbox_executor.entrypoint.executor import (
            _AGENT_LOG_BYTE_CEILING,
            _ANCHOR_CONTEXT_BYTES,
            _MAX_REDACT_INPUT_LEN,
            get_agent_log_byte_budget,
            redact_text,
        )

        # The ceiling is the input cap minus the anchor context the redaction region needs, so a
        # budget at the cap can no longer be honoured verbatim: the region would be longer than what
        # `redact_text` redacts faithfully.
        self.assertEqual(_AGENT_LOG_BYTE_CEILING, _MAX_REDACT_INPUT_LEN - _ANCHOR_CONTEXT_BYTES)
        with patch.dict(os.environ, {"HOLON_AGENT_LOG_BYTES": "200000"}):
            self.assertEqual(get_agent_log_byte_budget(), _AGENT_LOG_BYTE_CEILING)
        with (
            patch.dict(os.environ, {"HOLON_AGENT_LOG_BYTES": "200000"}),
            patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
        ):
            get_agent_log_byte_budget()
            warning = mock_stderr.getvalue()
            self.assertIn("200000", warning)
            self.assertIn(str(_MAX_REDACT_INPUT_LEN), warning)
            self.assertIn(str(_AGENT_LOG_BYTE_CEILING), warning)
            self.assertIn("redaction", warning)

        # At or below the ceiling the operator's number is honoured verbatim; above it, clamped.
        with patch.dict(os.environ, {"HOLON_AGENT_LOG_BYTES": str(_AGENT_LOG_BYTE_CEILING)}):
            self.assertEqual(get_agent_log_byte_budget(), _AGENT_LOG_BYTE_CEILING)
        with patch.dict(os.environ, {"HOLON_AGENT_LOG_BYTES": str(_MAX_REDACT_INPUT_LEN)}):
            self.assertEqual(get_agent_log_byte_budget(), _AGENT_LOG_BYTE_CEILING)
        with patch.dict(os.environ, {"HOLON_AGENT_LOG_BYTES": "4096"}):
            self.assertEqual(get_agent_log_byte_budget(), 4096)
        for unusable in ("not-a-number", "0", "-1"):
            with patch.dict(os.environ, {"HOLON_AGENT_LOG_BYTES": unusable}):
                self.assertEqual(get_agent_log_byte_budget(), 65536)

        # Counter-example: the input cap really does discard content, which is why a bigger budget
        # was a leak and a coverage hole rather than a longer tail.
        oversized = "q" * 150_000
        self.assertLess(len(redact_text(oversized)), len(oversized))

    def test_agent_output_committed_block_fits_the_byte_budget(self):
        """Test that marker plus tail never exceed the budget the record advertises.

        Regression: the tail alone filled the budget and the truncation marker was appended on top of
        it, so the committed block exceeded `HOLON_AGENT_LOG_BYTES` while the plan called that number
        a hard cap. The budget is now shared with the marker.
        """
        from sandbox_executor.entrypoint.executor import _TRUNCATION_MARKER_RESERVE, sanitize_agent_output

        stream = "noise line 000000\n" * 20000  # 320 KB of ordinary newline-terminated output
        for budget in (512, 1024, 4096, 65536, 100_000):
            bounded, truncated, _dropped = sanitize_agent_output(stream, budget)
            self.assertTrue(truncated)
            block_len = len(bounded.encode("utf-8"))
            self.assertLessEqual(block_len, budget, f"budget {budget}: the committed block must fit")
            self.assertGreater(
                block_len,
                budget - _TRUNCATION_MARKER_RESERVE,
                f"budget {budget}: reserving for the marker must not gut the retained tail",
            )

        # The redacting seam must hold the same line, including when masking rewrites sizes: a value
        # shorter than the 7-character mask grows, and a cross-line anchor is what the region reaches
        # back for, so both are present here.
        from sandbox_executor.entrypoint.executor import _AGENT_LOG_BYTE_CEILING, prepare_agent_output_block

        secret = _fake_token("sk-proj-", "9f3Aq7ZxR2tKp8WmB4VdNc6Ye1Hg")
        redacting = "noise line 000000\n" * 20000 + f"api_key:\n  {secret}\n" + "noise line 000001\n" * 2000
        for budget in (512, 1024, 4096, 65536, 100_000):
            block, truncated, _dropped = prepare_agent_output_block(redacting, budget)
            self.assertTrue(truncated)
            block_len = len(block.encode("utf-8"))
            ceiling = min(budget, _AGENT_LOG_BYTE_CEILING)
            self.assertLessEqual(block_len, ceiling, f"budget {budget}: the committed block must fit")
            self.assertGreater(
                block_len,
                ceiling - _TRUNCATION_MARKER_RESERVE,
                f"budget {budget}: reserving for the marker must not gut the retained tail",
            )
            for start in range(len(secret) - 7):
                self.assertNotIn(secret[start : start + 8], block, f"budget {budget}: credential fragment leaked")

        # Counter-example: the superseded shape was a full-budget tail with the marker on top of it.
        superseded = f"[Agent output truncated: 1 bytes dropped; showing tail {1024} bytes]\n" + "z" * 1024
        self.assertGreater(len(superseded.encode("utf-8")), 1024)

    def test_agent_output_under_budget_shortcut_redacts_the_text_it_measured(self):
        """Test the fits-the-budget shortcut cannot let redact_text cut its own input.

        Regression F-IT7-1: the shortcut measured the swept stream but returned the redacted
        PRE-SWEEP original. Sweeping shrinks every credential occurrence, so the original can sit far
        above `redact_text`'s input cap while the swept text fits the budget; the cap then
        head/tail-cuts that original on a path that reports `truncated=False`, and a secret
        straddling its split is committed with real characters exposed -- 40 leaking placements at the
        default budget. The shortcut now redacts exactly what it measured, and the budget is clamped
        to the cap, so the cap cannot fire at all.
        """
        from sandbox_executor.entrypoint.executor import (
            _MAX_REDACT_INPUT_LEN,
            prepare_agent_output_block,
            redact_agent_secrets,
            redact_env_literals,
        )

        # Built by concatenation so no vendor-format literal exists in the file.
        token = ("gh" + "p_") + "A" * 90
        opaque = "Xk9Q" * 40  # opaque, not an env value, no listed provider prefix
        budget = 65536

        with patch.dict(os.environ, {"GITHUB_TOKEN": token, "GH_TOKEN": "", "HOLON_AGENT_KEY": ""}):
            unit = "run log entry with token " + token + " "
            body = unit * 1500  # single line, so redact_text cannot snap its split to a boundary
            swept = redact_env_literals(body)
            self.assertGreater(len(body), _MAX_REDACT_INPUT_LEN, "the raw stream must exceed the cap")
            self.assertLessEqual(len(swept.encode("utf-8")), budget, "the swept stream must fit the budget")

            split = len(body) - _MAX_REDACT_INPUT_LEN // 2
            probe = body[:split] + f"x_api_key={opaque}" + body[split:]
            block, truncated, dropped = prepare_agent_output_block(probe, budget)

            self.assertNotIn(opaque[:8], block, "a credential straddling the cap split was committed")
            self.assertNotIn(
                "... (truncated) ...",
                block,
                "redact_text cut its own input, so this block is not a faithful record of the output",
            )
            self.assertLessEqual(len(block.encode("utf-8")), budget)
            self.assertEqual((truncated, dropped), (False, 0), "the swept stream genuinely fits")

            # Counter-example: redacting the pre-sweep original, which is what the shortcut used to do.
            superseded = redact_agent_secrets(probe)
            self.assertTrue(
                opaque[:8] in superseded or "... (truncated) ..." in superseded,
                "the superseded shortcut must still demonstrate the defect, or this test proves nothing",
            )

    def test_agent_output_block_stays_within_budget_when_redaction_grows_it(self):
        """Test the budget is enforced after redaction, not only before it.

        Regression F-IT6-1: masking rewrites the secret value, and any value shorter than the
        7-character mask gets LONGER (`api_key=ab` -> `api_key=*******`). Bounding first and redacting
        afterwards therefore let a secret-dense dump commit 95 KB under a 65,536 byte budget, and the
        growth is unbounded in the number of matches, so the execution record silently stopped being
        bounded. The block is refitted after redaction by dropping whole leading lines, which keeps
        the never-mid-line invariant intact.
        """
        from sandbox_executor.entrypoint.executor import (
            _TRUNCATION_MARKER_RESERVE,
            prepare_agent_output_block,
            redact_agent_secrets,
            redact_env_literals,
            sanitize_agent_output,
        )

        budget = 65536
        with patch.dict(os.environ, {"GITHUB_TOKEN": "", "GH_TOKEN": "", "HOLON_AGENT_KEY": ""}):
            for unit in ("api_key=ab\n", "token=xy\n", "secret=1\n"):
                stream = unit * 9000  # ~144 KB of ordinary-looking config dump, dense in short values
                self.assertGreater(len(stream), budget)

                block, truncated, dropped = prepare_agent_output_block(stream, budget)
                self.assertTrue(truncated)
                self.assertGreater(dropped, 0)
                encoded = block.encode("utf-8")
                self.assertLessEqual(len(encoded), budget, f"{unit!r}: the committed block must fit the budget")
                self.assertGreater(
                    len(encoded),
                    budget - 2 * _TRUNCATION_MARKER_RESERVE,
                    f"{unit!r}: refitting must trim whole lines, not gut the retained tail",
                )
                # Whole lines only: the first retained line must be a complete masked record of the
                # input, never a fragment joined from the middle of one.
                expected_line = redact_agent_secrets(unit.rstrip("\n"))
                self.assertEqual(block.split("\n", 1)[1].split("\n")[0], expected_line)

                # Counter-example, so this test cannot rot: the superseded order bounded first and
                # redacted afterwards, with no refit, and it overshot.
                bounded, _trunc, _drop = sanitize_agent_output(redact_env_literals(stream), budget)
                superseded = redact_agent_secrets(bounded)
                self.assertGreater(
                    len(superseded.encode("utf-8")),
                    budget,
                    f"{unit!r}: the superseded order must still demonstrate the overshoot",
                )

    def test_sanitize_agent_output_aligns_tail_to_line_boundary(self):
        """Test that truncation advances to a line boundary instead of splitting a line in half."""
        from sandbox_executor.entrypoint.executor import sanitize_agent_output

        # A newline 59 bytes into the retained window: the tail must start on the next line. This is
        # the pre-existing geometry, kept so a small budget provably behaves exactly as before the
        # marker reserve and the budget-scaled look-ahead existed.
        raw = "a" * 300 + "\n" + "b" * 40
        out, trunc, dropped = sanitize_agent_output(raw, max_bytes=100)
        self.assertTrue(trunc)
        self.assertEqual(dropped, len(raw) - 40)
        self.assertTrue(out.startswith("[Agent output truncated: 301 bytes dropped; showing tail 40 bytes]\n"))
        self.assertEqual(out.split("\n", 1)[1], "b" * 40)

        # The look-ahead scales with the budget: a newline 5000 bytes into the tail is farther than
        # the fixed 4096 look-ahead, yet aligning it discards old bytes, not new ones, so it is
        # taken whenever it still leaves at least half the requested budget.
        raw = "a" * 20000 + "\n" + "b" * 6871
        out, trunc, dropped = sanitize_agent_output(raw, max_bytes=12000)
        self.assertTrue(trunc)
        self.assertEqual(dropped, 20001)
        self.assertTrue(out.startswith("[Agent output truncated: 20001 bytes dropped; showing tail 6871 bytes]\n"))
        self.assertEqual(out.split("\n", 1)[1], "b" * 6871)

        # A single unterminated line longer than the budget cannot be redacted faithfully: the bytes
        # the cut removed may have been a key name or a provider prefix, so none of the line is
        # committed and the marker says why. The pre-fix code asserted the opposite (it committed the
        # raw z * 100 slice), which is the credential exposure F-IT4-1 reported.
        out, trunc, dropped = sanitize_agent_output("z" * 300, max_bytes=100)
        self.assertTrue(trunc)
        self.assertEqual(dropped, 300)
        self.assertEqual(
            out,
            "[Agent output truncated: 300 bytes dropped; showing tail 0 bytes; no complete line within budget]\n",
        )

        out, trunc, dropped = sanitize_agent_output("z" * 3000, max_bytes=512)
        self.assertTrue(trunc)
        self.assertEqual(dropped, 3000)
        self.assertEqual(
            out,
            "[Agent output truncated: 3000 bytes dropped; showing tail 0 bytes; no complete line within budget]\n",
        )

    def test_agent_output_bound_retains_budget_of_newest_output(self):
        """Test that bounding keeps a byte budget worth of newest output on very large streams.

        Regression guard: redacting the whole stream before bounding let redact_text's input cap
        discard the newest diagnostics, retaining ~50 KB of a 64 KB budget on large agent output.
        """
        from sandbox_executor.entrypoint.executor import (
            redact_agent_secrets,
            redact_env_literals,
            sanitize_agent_output,
        )

        budget = 65536
        align_window = 4096  # executor._TAIL_ALIGN_WINDOW, inlined so this holds across revisions
        stream = "noise line 000000\n" * 12000  # ~204 KB, well above redact_text's 100k input cap
        self.assertGreater(len(stream), 100_000)

        bounded, trunc, _dropped = sanitize_agent_output(redact_env_literals(stream), budget)
        self.assertTrue(trunc)
        kept = redact_agent_secrets(bounded)

        expected_tail = stream[-(budget - align_window) :]
        self.assertTrue(
            kept.endswith(expected_tail),
            "the byte budget must be filled with the newest output, not an input-capped fraction of it",
        )

    def test_sanitize_agent_output_helper(self):
        """Test sanitize_agent_output behaviour for budget boundaries and markers."""
        from sandbox_executor.entrypoint.executor import sanitize_agent_output

        # Below limit
        out, trunc, dropped = sanitize_agent_output("short output", max_bytes=100)
        self.assertEqual(out, "short output")
        self.assertFalse(trunc)
        self.assertEqual(dropped, 0)

        # Above limit on a payload with no line boundary at all: nothing in it can be redacted
        # faithfully, so the fail-closed cut commits only the marker, and names the reason.
        raw = "0123456789" * 10  # 100 bytes
        out, trunc, dropped = sanitize_agent_output(raw, max_bytes=20)
        self.assertTrue(trunc)
        self.assertEqual(dropped, 100)
        self.assertEqual(
            out,
            "[Agent output truncated: 100 bytes dropped; showing tail 0 bytes; no complete line within budget]\n",
        )

        # Above limit with line boundaries: the tail starts on the next line and the whole block stays
        # inside the budget the marker shares.
        raw = "0123456789 line\n" * 100  # 1600 bytes
        out, trunc, dropped = sanitize_agent_output(raw, max_bytes=200)
        self.assertTrue(trunc)
        self.assertEqual(dropped, 1504)
        self.assertTrue(out.startswith("[Agent output truncated: 1504 bytes dropped; showing tail 96 bytes]\n"))
        self.assertEqual(out.split("\n", 1)[1], raw[dropped:])
        self.assertLessEqual(len(out.encode("utf-8")), 200)

        # Default fallback for zero/negative budget
        out, trunc, dropped = sanitize_agent_output("test", max_bytes=0)
        self.assertEqual(out, "test")
        self.assertFalse(trunc)

    def test_redact_agent_secrets_helper(self):
        """Test redact_agent_secrets strips both regex patterns and literal tokens."""
        from sandbox_executor.entrypoint.executor import redact_agent_secrets

        env = {
            "GITHUB_TOKEN": "ghp_alpha123",
            "GH_TOKEN": "gho_beta456",
            "HOLON_AGENT_KEY": "ak_gamma789",
        }
        with patch.dict(os.environ, env):
            sample = (
                "Call: https://x-access-token:" + "gh" + "p_alpha123@github.com/repo.git\n"
                "Literal: ghp_alpha123 and gho_beta456 and ak_gamma789\n"
                "Header: Bearer custom_secret_bearer_token\n"
            )
            redacted = redact_agent_secrets(sample)
            self.assertNotIn("ghp_alpha123", redacted)
            self.assertNotIn("gho_beta456", redacted)
            self.assertNotIn("ak_gamma789", redacted)
            self.assertNotIn("custom_secret_bearer_token", redacted)
            self.assertIn("*******", redacted)
