from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import pytest
from sandbox_executor.cli import main
from sandbox_executor.flow import SKIP_PUSH_DISPATCH_ERROR, FlowContext

from tests.leak_assertions import assert_no_fragment, make_probe


class TestHolonFlowCLI(unittest.TestCase):
    def setUp(self):
        self._env_patcher = patch.dict(
            os.environ,
            {
                "HOLON_AGENT": "antigravity-agent",
                "HOLON_MODEL": "gemini-3.8-flash-medium",
            },
        )
        self._env_patcher.start()
        self.addCleanup(self._env_patcher.stop)

    def test_flow_fails_fast_without_agent_or_model(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            intent_file = os.path.join(tmp_dir, "intent.json")
            with open(intent_file, "w", encoding="utf-8") as f:
                json.dump({"slug": "test", "goal": "test"}, f)

            self.assert_flow_fails_fast(["holon", "flow", intent_file], "Agent runner is mandatory")
            self.assert_flow_fails_fast(
                ["holon", "flow", intent_file, "--agent", "antigravity-agent"], "Model is mandatory"
            )

    def assert_flow_fails_fast(self, argv, expected_error):
        with (
            patch.dict(os.environ, {"HOLON_IN_SANDBOX": "1"}, clear=True),
            patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
            patch("sys.argv", argv),
            self.assertRaises(SystemExit) as cm,
        ):
            main()
        self.assertEqual(cm.exception.code, 1)
        self.assertIn(expected_error, mock_stderr.getvalue())

    def test_flow_rejects_the_removed_skip_review_flag(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            intent_file = os.path.join(tmp_dir, "intent.json")
            with open(intent_file, "w", encoding="utf-8") as f:
                json.dump({"slug": "test-skip-rev"}, f)

            with (
                patch("sandbox_executor.cli.run_flow_pipeline") as mock_pipeline,
                patch("sandbox_executor.cli.dispatch_role_agent") as mock_dispatch,
                patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
                patch("sys.argv", ["holon", "flow", intent_file, "--skip-review"]),
                self.assertRaises(SystemExit) as cm,
            ):
                main()
            self.assertEqual(cm.exception.code, 2)
            self.assertIn("unrecognized arguments: --skip-review", mock_stderr.getvalue())
            mock_pipeline.assert_not_called()
            mock_dispatch.assert_not_called()

    def test_flow_skip_calibrate_flag(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            intent_file = os.path.join(tmp_dir, "intent.json")
            with open(intent_file, "w", encoding="utf-8") as f:
                json.dump({"slug": "test-skip-cal"}, f)

            with patch("sandbox_executor.cli.run_flow_pipeline") as mock_pipeline, patch("sys.exit") as mock_exit:
                mock_pipeline.return_value = {"status": "ok"}
                with patch("sys.argv", ["holon", "flow", intent_file, "--skip-calibrate"]):
                    main()
                mock_pipeline.assert_called_once()
                ctx = mock_pipeline.call_args[1]["context"]
                self.assertTrue(ctx.skip_calibrate)
                self.assertFalse(ctx.auto_calibrate)
                mock_exit.assert_called_with(0)

    def test_flow_max_review_iterations_flag(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            intent_file = os.path.join(tmp_dir, "intent.json")
            with open(intent_file, "w", encoding="utf-8") as f:
                json.dump({"slug": "test-max-iter"}, f)

            with patch("sandbox_executor.cli.run_flow_pipeline") as mock_pipeline, patch("sys.exit") as mock_exit:
                mock_pipeline.return_value = {"status": "ok"}
                with patch("sys.argv", ["holon", "flow", intent_file, "--max-review-iterations", "5"]):
                    main()
                mock_pipeline.assert_called_once()
                ctx = mock_pipeline.call_args[1]["context"]
                self.assertEqual(ctx.max_review_iterations, 5)
                mock_exit.assert_called_with(0)

    def test_flow_skip_push_rejected_outside_dry_run_for_any_resume(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            intent_file = os.path.join(tmp_dir, "intent.json")
            with open(intent_file, "w", encoding="utf-8") as f:
                json.dump({"slug": "test-skip-push", "goal": "g"}, f)
            checkpoint_file = os.path.join(tmp_dir, "checkpoint.json")
            ckpt_ctx = FlowContext(target_worktree=tmp_dir, intent_data={"slug": "cp"}, dry_run=True, skip_push=True)
            with open(checkpoint_file, "w", encoding="utf-8") as f:
                json.dump({"context": ckpt_ctx.to_dict(), "current_stage": "calibrate"}, f)

            argvs = [
                ["holon", "flow", intent_file, "--skip-push"],
                ["holon", "flow", intent_file, "--skip-push", "--from-stage", "review"],
                ["holon", "flow", intent_file, "--skip-push", "--from-stage", "calibrate"],
                # why: a checkpoint that recorded a dry run must not stand in for --dry-run on resume
                ["holon", "flow", "--checkpoint", checkpoint_file, "--skip-push"],
            ]
            for argv in argvs:
                with (
                    self.subTest(argv=argv[2:]),
                    patch("sandbox_executor.cli.run_flow_pipeline") as mock_pipeline,
                    patch("sandbox_executor.cli.preflight_gate") as mock_preflight,
                    patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
                    self.assertRaises(SystemExit) as cm,
                    patch("sys.argv", argv),
                ):
                    main()
                self.assertEqual(cm.exception.code, 1)
                self.assertIn(SKIP_PUSH_DISPATCH_ERROR, mock_stderr.getvalue())
                mock_pipeline.assert_not_called()
                mock_preflight.assert_not_called()

    def test_flow_skip_push_accepted_with_dry_run_resume(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            intent_file = os.path.join(tmp_dir, "intent.json")
            with open(intent_file, "w", encoding="utf-8") as f:
                json.dump({"slug": "test-skip-push-dry", "goal": "g"}, f)
            with patch("sandbox_executor.cli.run_flow_pipeline") as mock_pipeline, patch("sys.exit") as mock_exit:
                mock_pipeline.return_value = {"status": "ok"}
                argv = ["holon", "flow", intent_file, "--dry-run", "--skip-push", "--from-stage", "calibrate"]
                with patch("sys.argv", argv):
                    main()
                mock_pipeline.assert_called_once()
                self.assertTrue(mock_pipeline.call_args[1]["context"].skip_push)
                mock_exit.assert_called_with(0)

    def test_flow_invalid_max_review_iterations(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            intent_file = os.path.join(tmp_dir, "intent.json")
            with open(intent_file, "w", encoding="utf-8") as f:
                json.dump({"slug": "test-invalid-iter"}, f)

            for invalid_val in ["0", "-1"]:
                with (
                    patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
                    self.assertRaises(SystemExit) as cm,
                    patch("sys.argv", ["holon", "flow", intent_file, "--max-review-iterations", invalid_val]),
                ):
                    main()
                self.assertEqual(cm.exception.code, 1)
                self.assertIn("--max-review-iterations must be at least 1", mock_stderr.getvalue())

    def test_flow_token_reduce_and_mitm_web_flags(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            intent_file = os.path.join(tmp_dir, "intent.json")
            with open(intent_file, "w", encoding="utf-8") as f:
                json.dump({"slug": "test-mitm"}, f)

            with patch("sandbox_executor.cli.run_flow_pipeline") as mock_pipeline, patch("sys.exit") as mock_exit:
                mock_pipeline.return_value = {"status": "ok"}
                with patch("sys.argv", ["holon", "flow", intent_file, "--token-reduce", "--mitm-web"]):
                    main()
                mock_pipeline.assert_called_once()
                ctx = mock_pipeline.call_args[1]["context"]
                self.assertTrue(ctx.token_reduce)
                self.assertTrue(ctx.mitm_web)
                mock_exit.assert_called_with(0)

            # why: --mitm-web alone enables token_reduce, because the dashboard needs the token-reduce proxy
            with patch("sandbox_executor.cli.run_flow_pipeline") as mock_pipeline, patch("sys.exit") as mock_exit:
                mock_pipeline.return_value = {"status": "ok"}
                with patch("sys.argv", ["holon", "flow", intent_file, "--mitm-web"]):
                    main()
                mock_pipeline.assert_called_once()
                ctx = mock_pipeline.call_args[1]["context"]
                self.assertTrue(ctx.token_reduce)
                self.assertTrue(ctx.mitm_web)
                mock_exit.assert_called_with(0)

    def test_flow_combined_flags(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            intent_file = os.path.join(tmp_dir, "intent.json")
            with open(intent_file, "w", encoding="utf-8") as f:
                json.dump({"slug": "test-combined"}, f)

            with patch("sandbox_executor.cli.run_flow_pipeline") as mock_pipeline, patch("sys.exit") as mock_exit:
                mock_pipeline.return_value = {"status": "ok"}
                args = [
                    "holon",
                    "flow",
                    intent_file,
                    "--dry-run",
                    "--skip-calibrate",
                    "--max-review-iterations",
                    "4",
                    "--token-reduce",
                    "--mitm-web",
                ]
                with patch("sys.argv", args):
                    main()
                mock_pipeline.assert_called_once()
                ctx = mock_pipeline.call_args[1]["context"]
                self.assertTrue(ctx.dry_run)
                self.assertTrue(ctx.skip_calibrate)
                self.assertFalse(ctx.auto_calibrate)
                self.assertEqual(ctx.max_review_iterations, 4)
                self.assertTrue(ctx.token_reduce)
                self.assertTrue(ctx.mitm_web)
                mock_exit.assert_called_with(0)

    def test_flow_json_output_with_skipped_stages(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            intent_file = os.path.join(tmp_dir, "intent.json")
            with open(intent_file, "w", encoding="utf-8") as f:
                json.dump({"slug": "test-json"}, f)

            def fake_run(context, **kwargs):
                from sandbox_executor.flow import FlowStage, StageResult, StageStatus

                context.stage_results["calibrate"] = StageResult(
                    stage=FlowStage.CALIBRATE,
                    status=StageStatus.SKIPPED,
                    payload={"skipped_by_flag": True},
                )
                return context.to_dict()

            with (
                patch("sandbox_executor.cli.run_flow_pipeline", side_effect=fake_run),
                patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
                patch("sys.exit") as mock_exit,
            ):
                with patch("sys.argv", ["holon", "flow", intent_file, "--skip-calibrate", "--json"]):
                    main()
                mock_exit.assert_called_with(0)
                output = mock_stdout.getvalue()
                parsed = json.loads(output)
                self.assertEqual(parsed["stage_results"]["calibrate"]["status"], "skipped")
                self.assertNotIn("skip_review", parsed)
                self.assertTrue(parsed["skip_calibrate"])

    def test_flow_summary_lists_merged_child_prs_and_the_combined_pr(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            intent_file = os.path.join(tmp_dir, "intent.json")
            with open(intent_file, "w", encoding="utf-8") as f:
                json.dump({"slug": "test-decomposed"}, f)

            def fake_run(context, **kwargs):
                from sandbox_executor.flow import FlowStage, StageResult, StageStatus

                context.stage_results["execute"] = StageResult(
                    stage=FlowStage.EXECUTE,
                    status=StageStatus.SUCCESS,
                    payload={
                        "decomposed": True,
                        "plan_branch": "I-1-parent/P-2/_",
                        "execution_branch": "I-1-parent/P-2/E-3/_",
                        "parent_target_branch": "main",
                        "children": [
                            {"slug": "add-the-parser", "pr_url": "https://example.test/pull/7", "merged": True},
                            {
                                "slug": "add-the-cli-flag",
                                "execution_branch": "I-2-add-the-cli-flag/P-2/E-2/_",
                                "status": "skipped",
                            },
                        ],
                    },
                )
                return context.to_dict()

            with (
                patch("sandbox_executor.cli.run_flow_pipeline", side_effect=fake_run),
                patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
                patch("sys.exit") as mock_exit,
            ):
                with patch("sys.argv", ["holon", "flow", intent_file]):
                    main()
                mock_exit.assert_called_with(0)
                output = mock_stdout.getvalue()
                self.assertIn(
                    "\nDecomposed into 2 sub-intent(s); each child PR was merged into 'I-1-parent/P-2/_':\n"
                    "  1. add-the-parser: https://example.test/pull/7 (merged into 'I-1-parent/P-2/_')\n",
                    output,
                )
                self.assertIn("2. add-the-cli-flag: I-2-add-the-cli-flag/P-2/E-2/_ (skipped)", output)
                self.assertIn(
                    "Combined PR: Stage 4 raises and reviews 'I-1-parent/P-2/E-3/_' (with 'I-1-parent/P-2/_' merged "
                    "in) into 'main'.\n\n",
                    output,
                )

    def test_flow_exit_code_on_failure_vs_success(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            intent_file = os.path.join(tmp_dir, "intent.json")
            with open(intent_file, "w", encoding="utf-8") as f:
                json.dump({"slug": "test-exit-code"}, f)

            def failing_run(context, **kwargs):
                from sandbox_executor.flow import FlowStage, StageResult, StageStatus

                context.stage_results["intent"] = StageResult(
                    stage=FlowStage.INTENT,
                    status=StageStatus.FAILED,
                    error="Stage failed intentionally",
                )
                return context.to_dict()

            with (
                patch("sandbox_executor.cli.run_flow_pipeline", side_effect=failing_run),
                patch("sys.exit") as mock_exit,
            ):
                with patch("sys.argv", ["holon", "flow", intent_file]):
                    main()
                mock_exit.assert_called_with(1)

    def test_flow_checkpoint_resumption_flag_overrides(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            from sandbox_executor.flow import FlowContext

            subprocess.run(["git", "init", "-q", "-b", "feature-x", tmp_dir], check=True, capture_output=True)
            orig_ctx = FlowContext(
                target_worktree=tmp_dir,
                intent_data={"slug": "test-resume"},
                skip_calibrate=False,
                max_review_iterations=5,
            )
            checkpoint_file = os.path.join(tmp_dir, "checkpoint.json")
            with open(checkpoint_file, "w", encoding="utf-8") as f:
                json.dump({"context": orig_ctx.to_dict(), "current_stage": "execute"}, f)

            with patch("sandbox_executor.cli.run_flow_pipeline") as mock_pipeline, patch("sys.exit") as mock_exit:
                mock_pipeline.return_value = {"status": "ok"}
                args = [
                    "holon",
                    "flow",
                    "--checkpoint",
                    checkpoint_file,
                    "--skip-calibrate",
                    "--max-review-iterations",
                    "10",
                    "--mitm-web",
                ]
                with patch("sys.argv", args):
                    main()
                mock_pipeline.assert_called_once()
                ctx = mock_pipeline.call_args[1]["context"]
                self.assertTrue(ctx.skip_calibrate)
                self.assertFalse(ctx.auto_calibrate)
                self.assertEqual(ctx.max_review_iterations, 10)
                self.assertTrue(ctx.token_reduce)
                self.assertTrue(ctx.mitm_web)
                mock_exit.assert_called_with(0)

    def test_review_loop_cli_subcommand_registration(self):
        with patch("sys.argv", ["holon", "--help"]), patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)
            help_output = mock_stdout.getvalue()
            self.assertIn("review-loop", help_output)

    def test_review_loop_cli_argument_parsing(self):
        with (
            patch("sandbox_executor.flow.run_review_loop_cli", return_value=0) as mock_runner,
            patch("sys.exit") as mock_exit,
        ):
            args = [
                "holon",
                "review-loop",
                "my-exec-branch",
                "--max-iterations",
                "15",
                "--ensemble-size",
                "2",
                "--dry-run",
            ]
            with patch("sys.argv", args):
                main()
            mock_runner.assert_called_once()
            call_args = mock_runner.call_args[0][0]
            self.assertEqual(call_args.command, "review-loop")
            self.assertEqual(call_args.target, "my-exec-branch")
            self.assertEqual(call_args.max_iterations, 15)
            self.assertEqual(call_args.ensemble_size, 2)
            self.assertTrue(call_args.dry_run)
            mock_exit.assert_called_with(0)

    def test_review_loop_cli_alias_invocations(self):
        for alias in ["review", "pr-review-loop"]:
            with (
                patch("sandbox_executor.flow.run_review_loop_cli", return_value=0) as mock_runner,
                patch("sys.exit") as mock_exit,
            ):
                with patch("sys.argv", ["holon", alias, "branch-123", "--local"]):
                    main()
                mock_runner.assert_called_once()
                call_args = mock_runner.call_args[0][0]
                self.assertEqual(call_args.target, "branch-123")
                mock_exit.assert_called_with(0)

    def test_review_loop_cli_validation(self):
        for invalid_arg in [["--max-iterations", "0"], ["--ensemble-size", "0"]]:
            with (
                patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
                patch("sys.exit") as mock_exit,
                patch("sandbox_executor.flow.run_review_loop_cli") as mock_runner,
            ):
                with patch("sys.argv", ["holon", "review-loop", "target-branch", *invalid_arg]):
                    main()
                mock_exit.assert_called_with(1)
                mock_runner.assert_not_called()
                self.assertIn("Error:", mock_stderr.getvalue())

    def test_review_loop_local_exits_one_when_not_approved(self):
        fake_result = {
            "target": "my-branch",
            "consensus": {"approved": False, "status": "post_failed", "iterations": 1},
            "error": "Posting the review consensus for 'my-branch' failed: HTTP 502",
        }
        with (
            patch("sandbox_executor.flow.review.cli.run_review_loop", return_value=fake_result),
            patch("sandbox_executor.cli.detect_holon_coherence", return_value=False),
            patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
            patch("sys.exit") as mock_exit,
        ):
            with patch("sys.argv", ["holon", "review-loop", "my-branch", "--local"]):
                main()
            mock_exit.assert_called_with(1)
            self.assertIn("POST_FAILED", mock_stdout.getvalue())
            self.assertIn("HTTP 502", mock_stdout.getvalue())

    def test_review_loop_cli_json_output(self):
        fake_result = {
            "target": "my-branch",
            "consensus": {"approved": True, "status": "approved", "iterations": 1},
            "ensemble_verdict": "APPROVED",
            "halted_for_human": True,
            "human_merge_gate_enforced": True,
            "human_approval_command": "holon review approve my-branch",
        }
        with (
            patch("sandbox_executor.flow.review.cli.run_review_loop", return_value=fake_result),
            patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
            patch("sys.exit") as mock_exit,
        ):
            with patch("sys.argv", ["holon", "review-loop", "my-branch", "--json"]):
                main()
            mock_exit.assert_called_with(0)
            output = mock_stdout.getvalue()
            parsed = json.loads(output)
            self.assertEqual(parsed["target"], "my-branch")
            self.assertTrue(parsed["consensus"]["approved"])


def _urlparse_port_cast_error(head: str) -> ValueError:
    return ValueError(f"Port could not be cast to integer value as '{head}'")


@pytest.mark.parametrize("delim", ["/", "?", "#"])
def test_review_loop_handler_masks_a_quoted_password_head(monkeypatch, capsys, delim):
    head, tail = make_probe("a"), make_probe("b")
    monkeypatch.setenv("HOLON_PROXY_URL", f"http://alice:{head}{delim}{tail}@127.0.0.1:3128")
    with (
        patch("sys.argv", ["holon", "review-loop", "1"]),
        patch("sandbox_executor.cli.detect_holon_coherence", side_effect=_urlparse_port_cast_error(head)),
        pytest.raises(SystemExit) as exit_info,
    ):
        main()

    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert "Error during review-loop execution" in captured.err
    for text in (captured.out, captured.err):
        assert_no_fragment(text, head)
        assert_no_fragment(text, tail)


def test_flow_handler_masks_a_quoted_password_head(monkeypatch, capsys, tmp_path):
    head, tail = make_probe("a"), make_probe("b")
    monkeypatch.setenv("HOLON_PROXY_URL", f"alice:{head}/{tail}@127.0.0.1:3128")
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text("{}", encoding="utf-8")
    with (
        patch("sys.argv", ["holon", "flow", "--checkpoint", str(checkpoint)]),
        patch("sandbox_executor.cli.load_checkpoint", side_effect=_urlparse_port_cast_error(head)),
        pytest.raises(SystemExit) as exit_info,
    ):
        main()

    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert "Error during flow execution" in captured.err
    for text in (captured.out, captured.err):
        assert_no_fragment(text, head)


def test_calibrate_handler_masks_a_quoted_password_head(monkeypatch, capsys):
    head, tail = make_probe("a"), make_probe("b")
    monkeypatch.setenv("HOLON_LOCAL_BASE_URL", f"socks5://alice:{head}#{tail}@127.0.0.1:3128")
    with (
        patch("sys.argv", ["holon", "calibrate", "I-x/P-y/E-z/_"]),
        patch("sandbox_executor.cli.run_calibrate", side_effect=RuntimeError(f"bad value {head}#{tail}")),
        pytest.raises(SystemExit) as exit_info,
    ):
        main()

    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert "Error during calibration" in captured.err
    for text in (captured.out, captured.err):
        assert_no_fragment(text, head)
        assert_no_fragment(text, tail)
