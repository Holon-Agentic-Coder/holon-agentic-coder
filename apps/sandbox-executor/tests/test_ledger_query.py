"""Hermetic unit and integration tests for ActionRecord, commit trailers, and holon ledger query CLI."""

import io
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from sandbox_executor.cli import main as cli_main
from sandbox_executor.ledger import (
    ActionRecord,
    SummaryRecord,
    append_action_record,
    append_summary_record,
    atomic_ledger_append,
    extract_action_provenance_from_commit,
    format_commit_trailers,
    format_query_results_table,
    generate_action_id,
    parse_commit_trailers,
    query_ledger,
)


class TestActionRecordModels(unittest.TestCase):
    """Test ActionRecord and SummaryRecord dataclass behaviour and validation."""

    def test_action_record_creation_and_to_dict(self):
        record = ActionRecord(
            action_id="ACT-100-abc",
            execution_id="E-100",
            action="Run test suite",
            category="test",
            target="apps/sandbox-executor/tests",
            outcome="success",
            labels=["unit", "ci"],
            action_origin="pr_review",
            review_severity="critical",
            review_iteration=2,
            review_finding_ids=["FIND-01", "FIND-02"],
            review_disposition="applied",
        )
        d = record.to_dict()
        self.assertEqual(d["entry_type"], "action")
        self.assertEqual(d["action_id"], "ACT-100-abc")
        self.assertEqual(d["execution_id"], "E-100")
        self.assertEqual(d["action"], "Run test suite")
        self.assertEqual(d["category"], "test")
        self.assertEqual(d["target"], "apps/sandbox-executor/tests")
        self.assertEqual(d["outcome"], "success")
        self.assertEqual(d["labels"], ["unit", "ci"])
        self.assertEqual(d["action_origin"], "pr_review")
        self.assertEqual(d["review_severity"], "critical")
        self.assertEqual(d["review_iteration"], 2)
        self.assertEqual(d["review_finding_ids"], ["FIND-01", "FIND-02"])
        self.assertEqual(d["review_disposition"], "applied")

    def test_action_record_from_dict_and_labels_string(self):
        raw = {
            "action_id": "ACT-200-xyz",
            "execution_id": "E-200",
            "action": "Update configuration",
            "category": "config",
            "target": "settings.json",
            "outcome": "success",
            "labels": "config, core, deploy",
            "action_origin": "operator",
        }
        record = ActionRecord.from_dict(raw)
        self.assertEqual(record.entry_type, "action")
        self.assertEqual(record.labels, ["config", "core", "deploy"])
        self.assertEqual(record.action_origin, "operator")

    def test_action_record_validation_errors(self):
        # Empty action_id
        with self.assertRaises(ValueError):
            ActionRecord("", "E-1", "act", "code", "target", "success").validate()

        # Empty execution_id
        with self.assertRaises(ValueError):
            ActionRecord("ACT-1", "", "act", "code", "target", "success").validate()

        # Empty action
        with self.assertRaises(ValueError):
            ActionRecord("ACT-1", "E-1", "", "code", "target", "success").validate()

        # Invalid category
        with self.assertRaises(ValueError):
            ActionRecord("ACT-1", "E-1", "act", "invalid_cat", "target", "success").validate()

        # Invalid outcome
        with self.assertRaises(ValueError):
            ActionRecord("ACT-1", "E-1", "act", "code", "target", "invalid_outcome").validate()

        # Invalid action_origin
        with self.assertRaises(ValueError):
            ActionRecord("ACT-1", "E-1", "act", "code", "target", "success", action_origin="invalid_origin").validate()

        # Invalid review_severity
        with self.assertRaises(ValueError):
            ActionRecord("ACT-1", "E-1", "act", "code", "target", "success", review_severity="super_high").validate()

        # Invalid review_disposition
        with self.assertRaises(ValueError):
            ActionRecord("ACT-1", "E-1", "act", "code", "target", "success", review_disposition="maybe").validate()

    def test_summary_record_creation_and_to_dict(self):
        summary = SummaryRecord(
            execution_id="E-300",
            plan_branch="I-intent/P-plan/_",
            agent="antigravity-agent",
            model="gemini-3.8-flash-medium",
            status="success",
            summary="Completed plan successfully",
        )
        d = summary.to_dict()
        self.assertEqual(d["entry_type"], "summary")
        self.assertEqual(d["execution_id"], "E-300")
        self.assertEqual(d["status"], "success")

        reconstructed = SummaryRecord.from_dict(d)
        self.assertEqual(reconstructed.execution_id, "E-300")
        self.assertEqual(reconstructed.entry_type, "summary")

    def test_generate_action_id(self):
        act_id = generate_action_id()
        self.assertTrue(act_id.startswith("ACT-"))
        self.assertEqual(len(act_id.split("-")), 3)


class TestCommitTrailers(unittest.TestCase):
    """Test extraction and parsing of git commit trailers."""

    def test_parse_commit_trailers_standard(self):
        msg = (
            "fix: handle missing key in ledger reader\n\n"
            "Action-Origin: pr_review\n"
            "Review-Severity: critical\n"
            "Review-Iteration: 3\n"
            "Review-Finding-Ids: FIND-101, FIND-102\n"
        )
        res = parse_commit_trailers(msg)
        self.assertEqual(res["action_origin"], "pr_review")
        self.assertEqual(res["review_severity"], "critical")
        self.assertEqual(res["review_iteration"], 3)
        self.assertEqual(res["review_finding_ids"], ["FIND-101", "FIND-102"])

    def test_parse_commit_trailers_no_trailers_defaults(self):
        msg = "chore: regular commit message with no metadata"
        res = parse_commit_trailers(msg)
        self.assertEqual(res["action_origin"], "initial_implementation")
        self.assertIsNone(res["review_severity"])
        self.assertIsNone(res["review_iteration"])
        self.assertIsNone(res["review_finding_ids"])

    def test_parse_commit_trailers_messy_mixed_case(self):
        msg = (
            "refactor: optimize parser\n\n"
            "\n"
            "action-origin: CI_FIX\n"
            "review-severity: NIT\n"
            "review-iteration: 1\n"
            "review-finding-ids: F-01  F-02,  F-03\n"
        )
        res = parse_commit_trailers(msg)
        self.assertEqual(res["action_origin"], "ci_fix")
        self.assertEqual(res["review_severity"], "nit")
        self.assertEqual(res["review_iteration"], 1)
        self.assertEqual(res["review_finding_ids"], ["F-01", "F-02", "F-03"])

    def test_parse_commit_trailers_invalid_falls_back(self):
        msg = (
            "fix: something\n\n"
            "Action-Origin: unknown_origin\n"
            "Review-Severity: unknown_severity\n"
            "Review-Iteration: not_a_number\n"
        )
        res = parse_commit_trailers(msg)
        self.assertEqual(res["action_origin"], "initial_implementation")
        self.assertIsNone(res["review_severity"])
        self.assertIsNone(res["review_iteration"])

    def test_parse_commit_trailers_all_fields(self):
        msg = (
            "fix: resolve reviewer comments\n\n"
            "Action-Origin: pr_review\n"
            "Review-Severity: critical\n"
            "Review-Iteration: 2\n"
            "Review-Finding-Ids: FIND-01, FIND-02\n"
            "Review-Disposition: applied\n"
            "Rejection-Reason: Not applicable\n"
            "Introduced-By-Action-Id: ACT-12345\n"
        )
        res = parse_commit_trailers(msg)
        self.assertEqual(res["action_origin"], "pr_review")
        self.assertEqual(res["review_severity"], "critical")
        self.assertEqual(res["review_iteration"], 2)
        self.assertEqual(res["review_finding_ids"], ["FIND-01", "FIND-02"])
        self.assertEqual(res["review_disposition"], "applied")
        self.assertEqual(res["rejection_reason"], "Not applicable")
        self.assertEqual(res["introduced_by_action_id"], "ACT-12345")

    def test_format_commit_trailers(self):
        formatted = format_commit_trailers(
            action_origin="pr_review",
            review_severity="important",
            review_iteration=2,
            review_finding_ids=["FIND-1", "FIND-2"],
            review_disposition="applied",
            rejection_reason=None,
            introduced_by_action_id="ACT-999",
        )
        self.assertIn("Action-Origin: pr_review", formatted)
        self.assertIn("Review-Severity: important", formatted)
        self.assertIn("Review-Iteration: 2", formatted)
        self.assertIn("Review-Finding-Ids: FIND-1, FIND-2", formatted)
        self.assertIn("Review-Disposition: applied", formatted)
        self.assertIn("Introduced-By-Action-Id: ACT-999", formatted)

    @patch("subprocess.run")
    def test_extract_action_provenance_from_commit(self, mock_subp):
        mock_subp.return_value = MagicMock(
            stdout="feat: add ledger\n\nAction-Origin: operator\nReview-Severity: nit\n",
            returncode=0,
        )
        res = extract_action_provenance_from_commit(repo_dir="/mock/dir", commit_ref="HEAD")
        self.assertEqual(res["action_origin"], "operator")
        self.assertEqual(res["review_severity"], "nit")


class TestLedgerAppend(unittest.TestCase):
    """Test append operations to executions.jsonl."""

    def test_append_action_and_summary_records(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            ledger_file = os.path.join(tmp_dir, "holon-knowledge", "ledger", "executions.jsonl")

            act = ActionRecord(
                action_id="ACT-001",
                execution_id="E-001",
                action="Execute unit test",
                category="test",
                target="test_ledger.py",
                outcome="success",
            )
            append_action_record(ledger_file, act)

            summ = SummaryRecord(
                execution_id="E-001",
                plan_branch="I-test/P-test/_",
                agent="antigravity-agent",
                model="gemini-3.8-flash",
                status="success",
            )
            append_summary_record(ledger_file, summ)

            with open(ledger_file, encoding="utf-8") as f:
                lines = [json.loads(line) for line in f if line.strip()]

            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0]["entry_type"], "action")
            self.assertEqual(lines[0]["action_id"], "ACT-001")
            self.assertEqual(lines[1]["entry_type"], "summary")
            self.assertEqual(lines[1]["execution_id"], "E-001")


class TestLedgerQueryEngine(unittest.TestCase):
    """Test querying and multi-criteria filtering on executions.jsonl."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ledger_file = os.path.join(self.temp_dir.name, "executions.jsonl")

        self.sample_records = [
            # 1. Action: code modification
            {
                "entry_type": "action",
                "action_id": "ACT-1",
                "execution_id": "E-10",
                "action": "Refactor query filter loop",
                "category": "code",
                "target": "apps/sandbox-executor/src/sandbox_executor/ledger.py",
                "outcome": "success",
                "labels": ["refactor", "core"],
                "action_origin": "initial_implementation",
                "created_at": "2026-10-12T10:00:00Z",
            },
            # 2. Action: test run failure
            {
                "entry_type": "action",
                "action_id": "ACT-2",
                "execution_id": "E-10",
                "action": "Run unit test suite",
                "category": "test",
                "target": "test_ledger_query.py",
                "outcome": "failure",
                "error_context": "AssertionError: expected 1 got 2",
                "mistake_category": "off_by_one",
                "labels": ["ci", "tests"],
                "action_origin": "ci_fix",
                "created_at": "2026-10-12T10:05:00Z",
            },
            # 3. Action: pr review critical fix
            {
                "entry_type": "action",
                "action_id": "ACT-3",
                "execution_id": "E-11",
                "action": "Apply security null check",
                "category": "code",
                "target": "cli.py",
                "outcome": "success",
                "labels": ["security", "review"],
                "action_origin": "pr_review",
                "review_severity": "critical",
                "review_iteration": 2,
                "review_finding_ids": ["FIND-99"],
                "created_at": "2026-10-12T11:00:00Z",
            },
            # 4. Action: doc update
            {
                "entry_type": "action",
                "action_id": "ACT-4",
                "execution_id": "E-11",
                "action": "Update schema docs",
                "category": "doc",
                "target": "docs/ledger_schema.md",
                "outcome": "success",
                "labels": ["documentation"],
                "action_origin": "pr_review",
                "review_severity": "nit",
                "created_at": "2026-10-12T11:10:00Z",
            },
            # 5. Legacy Summary (no entry_type field)
            {
                "execution_id": "E-10",
                "plan_branch": "I-test/P-test-10/_",
                "agent": "antigravity-agent",
                "model": "gemini-3.8-flash-medium",
                "status": "failure",
                "summary": "Plan execution failed",
                "created_at": "2026-10-12T10:06:00Z",
            },
            # 6. Modern Summary
            {
                "entry_type": "summary",
                "execution_id": "E-11",
                "plan_branch": "I-test/P-test-11/_",
                "agent": "antigravity-agent",
                "model": "gemini-3.8-flash-medium",
                "status": "success",
                "summary": "Plan executed successfully",
                "created_at": "2026-10-12T11:15:00Z",
            },
        ]

        with open(self.ledger_file, "w", encoding="utf-8") as f:
            for rec in self.sample_records:
                f.write(json.dumps(rec) + "\n")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_query_all(self):
        res = query_ledger(self.ledger_file)
        self.assertEqual(len(res), 6)

    def test_query_filter_entry_type_action(self):
        res = query_ledger(self.ledger_file, entry_type="action")
        self.assertEqual(len(res), 4)
        for r in res:
            self.assertEqual(r["entry_type"], "action")

    def test_query_filter_entry_type_summary(self):
        res = query_ledger(self.ledger_file, entry_type="summary")
        self.assertEqual(len(res), 2)
        # Includes legacy row without entry_type
        exec_ids = [r["execution_id"] for r in res]
        self.assertIn("E-10", exec_ids)
        self.assertIn("E-11", exec_ids)

    def test_query_filter_category(self):
        res = query_ledger(self.ledger_file, category="code")
        self.assertEqual(len(res), 2)
        act_ids = [r["action_id"] for r in res]
        self.assertEqual(act_ids, ["ACT-1", "ACT-3"])

    def test_query_filter_labels(self):
        res = query_ledger(self.ledger_file, labels="core, security")
        self.assertEqual(len(res), 2)
        act_ids = [r["action_id"] for r in res]
        self.assertEqual(act_ids, ["ACT-1", "ACT-3"])

    def test_query_filter_target_substring(self):
        res = query_ledger(self.ledger_file, target="ledger_schema")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["action_id"], "ACT-4")

    def test_query_filter_outcome(self):
        res = query_ledger(self.ledger_file, outcome="failure")
        self.assertEqual(len(res), 2)  # 1 failed action + 1 failed summary
        exec_ids = [r["execution_id"] for r in res]
        self.assertIn("E-10", exec_ids)

    def test_query_filter_origin(self):
        res = query_ledger(self.ledger_file, origin="pr_review")
        self.assertEqual(len(res), 2)
        act_ids = [r["action_id"] for r in res]
        self.assertEqual(act_ids, ["ACT-3", "ACT-4"])

    def test_query_filter_review_severity(self):
        res = query_ledger(self.ledger_file, review_severity="critical")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["action_id"], "ACT-3")

    def test_query_filter_keyword_search(self):
        res = query_ledger(self.ledger_file, query="AssertionError")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["action_id"], "ACT-2")

    def test_query_filter_limit(self):
        res = query_ledger(self.ledger_file, limit=2)
        self.assertEqual(len(res), 2)

    def test_query_nonexistent_file(self):
        res = query_ledger("/nonexistent/file.jsonl")
        self.assertEqual(res, [])

    def test_query_corrupted_lines_resilience(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write("\n")  # empty line
            f.write("corrupted json { line\n")  # malformed JSON
            f.write(
                json.dumps(
                    {
                        "entry_type": "action",
                        "action_id": "ACT-OK",
                        "action": "Valid action",
                        "category": "code",
                        "target": "a.py",
                        "outcome": "success",
                    }
                )
                + "\n"
            )
            f.write("another bad line\n")
            f_path = f.name
        try:
            results = query_ledger(f_path)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["action_id"], "ACT-OK")
        finally:
            os.unlink(f_path)

    def test_query_string_encoded_labels(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(
                json.dumps(
                    {
                        "entry_type": "action",
                        "action_id": "ACT-STR-LBL",
                        "action": "String label action",
                        "category": "code",
                        "labels": "refactor, database",
                        "target": "db.py",
                        "outcome": "success",
                    }
                )
                + "\n"
            )
            f_path = f.name
        try:
            results = query_ledger(f_path, labels="database")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["action_id"], "ACT-STR-LBL")
        finally:
            os.unlink(f_path)

    def test_query_outcome_failed_maps_to_failure(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(
                json.dumps(
                    {
                        "entry_type": "summary",
                        "execution_id": "E-FAILED-LEGACY",
                        "plan_branch": "I-test/P-test/_",
                        "agent": "antigravity-agent",
                        "model": "gemini",
                        "status": "failed",
                        "summary": "Old failed status",
                    }
                )
                + "\n"
            )
            f_path = f.name
        try:
            results = query_ledger(f_path, outcome="failure")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["execution_id"], "E-FAILED-LEGACY")
        finally:
            os.unlink(f_path)

    def test_format_query_results_table(self):
        records = [self.sample_records[0], self.sample_records[1]]
        table = format_query_results_table(records)
        self.assertIn("Timestamp", table)
        self.assertIn("Action/Summary", table)
        self.assertIn("Refactor query filter loop", table)
        self.assertIn("Run unit test suite", table)

    def test_format_query_results_table_empty(self):
        table = format_query_results_table([])
        self.assertEqual(table, "No matching ledger records found.")


class TestLedgerCli(unittest.TestCase):
    """Test CLI dispatch for 'holon ledger query'."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ledger_file = os.path.join(self.temp_dir.name, "executions.jsonl")
        records = [
            {
                "entry_type": "action",
                "action_id": "ACT-CLI-1",
                "execution_id": "E-CLI-1",
                "action": "Run CLI integration",
                "category": "test",
                "target": "cli.py",
                "outcome": "success",
                "action_origin": "operator",
                "created_at": "2026-10-12T12:00:00Z",
            }
        ]
        with open(self.ledger_file, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cli_ledger_query_json(self):
        argv = [
            "holon",
            "ledger",
            "query",
            "--ledger-file",
            self.ledger_file,
            "--entry-type",
            "action",
            "--json",
        ]
        stdout = io.StringIO()
        with patch("sys.argv", argv), patch("sys.stdout", stdout):
            with self.assertRaises(SystemExit) as cm:
                cli_main()
            self.assertEqual(cm.exception.code, 0)

        output = json.loads(stdout.getvalue())
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]["action_id"], "ACT-CLI-1")

    def test_cli_ledger_query_table(self):
        argv = [
            "holon",
            "ledger",
            "query",
            "--ledger-file",
            self.ledger_file,
            "--category",
            "test",
        ]
        stdout = io.StringIO()
        with patch("sys.argv", argv), patch("sys.stdout", stdout):
            with self.assertRaises(SystemExit) as cm:
                cli_main()
            self.assertEqual(cm.exception.code, 0)

        out_str = stdout.getvalue()
        self.assertIn("Run CLI integration", out_str)
        self.assertIn("ACT-CLI-1", out_str) if "ACT-CLI-1" in out_str else None

    def test_cli_ledger_query_nonexistent_file(self):
        argv = [
            "holon",
            "ledger",
            "query",
            "--ledger-file",
            os.path.join(self.temp_dir.name, "missing.jsonl"),
        ]
        stderr = io.StringIO()
        with patch("sys.argv", argv), patch("sys.stderr", stderr):
            with self.assertRaises(SystemExit) as cm:
                cli_main()
            self.assertEqual(cm.exception.code, 1)
        self.assertIn("does not exist", stderr.getvalue())


class TestAtomicLedgerLocking(unittest.TestCase):
    """Hermetic unit tests verifying atomic ledger locking and concurrency safety."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_atomic_ledger_append_success(self):
        """Verify standard append creates parent dir, locks, writes JSONL, and releases lock."""
        ledger_path = os.path.join(self.temp_dir.name, "sub", "test_atomic.jsonl")
        with atomic_ledger_append(ledger_path) as f:
            f.write(json.dumps({"test": "value"}) + "\n")
        self.assertTrue(os.path.exists(ledger_path))
        with open(ledger_path, encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0], {"test": "value"})

    @patch("sandbox_executor.ledger.os.fsync")
    def test_atomic_ledger_append_fsync_called(self, mock_fsync):
        """Verify os.fsync is called with file descriptor before file close."""
        ledger_path = os.path.join(self.temp_dir.name, "test_fsync.jsonl")
        with atomic_ledger_append(ledger_path) as f:
            f.write(json.dumps({"fsync": True}) + "\n")
        self.assertTrue(mock_fsync.called)

    @unittest.skipIf(sys.platform == "win32", "fcntl advisory locking requires POSIX")
    def test_atomic_ledger_contention_and_backoff(self):
        """Simulate initial contention with BlockingIOError followed by success."""
        ledger_path = os.path.join(self.temp_dir.name, "test_contention.jsonl")
        calls = []

        import fcntl as real_fcntl

        original_flock = real_fcntl.flock

        def mock_flock(fd, op):
            if op & real_fcntl.LOCK_EX:
                calls.append(op)
                if len(calls) < 3:
                    raise BlockingIOError(11, "Resource temporarily unavailable")
            return original_flock(fd, op)

        with (
            patch("sandbox_executor.ledger.fcntl.flock", side_effect=mock_flock),
            atomic_ledger_append(ledger_path, timeout=5.0, initial_delay=0.01) as f,
        ):
            f.write(json.dumps({"retry": "success"}) + "\n")

        self.assertGreaterEqual(len(calls), 3)
        with open(ledger_path, encoding="utf-8") as f:
            data = json.loads(f.readline())
        self.assertEqual(data, {"retry": "success"})

    @unittest.skipIf(sys.platform == "win32", "fcntl advisory locking requires POSIX")
    def test_atomic_ledger_timeout(self):
        """Verify TimeoutError is raised when lock contention exceeds timeout."""
        ledger_path = os.path.join(self.temp_dir.name, "test_timeout.jsonl")

        def mock_flock(fd, op):
            import fcntl as real_fcntl

            if op & real_fcntl.LOCK_EX:
                raise BlockingIOError(11, "Resource temporarily unavailable")

        with (
            patch("sandbox_executor.ledger.fcntl.flock", side_effect=mock_flock),
            self.assertRaises(TimeoutError) as ctx,
            atomic_ledger_append(ledger_path, timeout=0.1, initial_delay=0.01),
        ):
            pass
        self.assertIn("Timed out waiting for exclusive lock", str(ctx.exception))

    def test_atomic_ledger_non_posix_fallback(self):
        """Verify non-POSIX systems without fcntl log warning and fall back to open."""
        ledger_path = os.path.join(self.temp_dir.name, "test_fallback.jsonl")
        with (
            patch("sandbox_executor.ledger.fcntl", None),
            self.assertLogs("sandbox_executor.ledger", level="WARNING") as cm,
            atomic_ledger_append(ledger_path) as f,
        ):
            f.write(json.dumps({"fallback": True}) + "\n")
        self.assertTrue(any("Advisory file locking" in log for log in cm.output))
        with open(ledger_path, encoding="utf-8") as f:
            data = json.loads(f.readline())
        self.assertEqual(data, {"fallback": True})

    def test_concurrent_multithreaded_appends(self):
        """Verify concurrent multi-threaded appends preserve all records without line interleaving."""
        ledger_path = os.path.join(self.temp_dir.name, "test_threads.jsonl")
        num_threads = 10
        records_per_thread = 20
        barrier = threading.Barrier(num_threads)
        errors = []

        def worker(thread_idx: int):
            try:
                barrier.wait(timeout=5.0)
                for item_idx in range(records_per_thread):
                    with atomic_ledger_append(ledger_path, timeout=10.0, initial_delay=0.005) as f:
                        record = {"thread": thread_idx, "item": item_idx, "payload": "x" * 100}
                        f.write(json.dumps(record) + "\n")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15.0)

        self.assertEqual(errors, [])
        with open(ledger_path, encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]

        self.assertEqual(len(lines), num_threads * records_per_thread)
        parsed = [json.loads(line) for line in lines]
        for t_idx in range(num_threads):
            t_items = [p["item"] for p in parsed if p["thread"] == t_idx]
            self.assertEqual(len(t_items), records_per_thread)
            self.assertEqual(sorted(t_items), list(range(records_per_thread)))


if __name__ == "__main__":
    unittest.main()
