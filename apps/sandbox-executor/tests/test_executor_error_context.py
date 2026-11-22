"""Tests for the executor's command_execution ``error_context``: redaction runs before the 500-character cut."""

import unittest

from tests.executor_fixtures import (
    VALID_NOTES,
    IncrementalNotesHarness,
    _workspace_is_mounted,  # noqa: F401  (autouse fixture)
)
from tests.leak_assertions import make_gh_probe


class TestExecutorErrorContext(unittest.TestCase):
    run_executor = IncrementalNotesHarness.run_executor
    write_notes = staticmethod(IncrementalNotesHarness.write_notes)

    def test_error_context_redacts_a_token_straddling_the_500_character_cut(self):
        # Truncating first would leave a token prefix too short for the provider sweep; redaction runs first.
        probe = make_gh_probe() + "0" * 4
        stderr = "e" * 490 + probe + " tail"
        run = self.run_executor(
            [self.write_notes(VALID_NOTES, returncode=1, stderr=stderr)],
            env={"HOLON_ACTION_LOGGING": "1"},
        )
        (action,) = [e for e in run["entries"] if e.get("event_type") == "command_execution"]
        context = action["error_context"]
        self.assertLessEqual(len(context), 500)
        self.assertNotIn(probe[:8], context)
        self.assertFalse([probe[i : i + 8] for i in range(4, len(probe) - 7) if probe[i : i + 8] in context])
