import json
from pathlib import Path
from unittest.mock import ANY, create_autospec, patch

import pytest
from sandbox_executor.agent_runner import AgentRunner, get_runner
from sandbox_executor.budgets import TurnBudgets
from sandbox_executor.entrypoint.review_loop import _run_resolver_agent
from sandbox_executor.turns import TurnResult


class TestResolverTurnBudgets:
    def _captured_turn_budgets(self, tmp_path: Path) -> object:
        dispositions_path = str(tmp_path / "dispositions.json")
        captured: dict[str, object] = {}

        def fake_run_turn(cmd: list[str], cwd: str | None = None, **kwargs: object) -> TurnResult:
            captured["turn_budgets"] = kwargs.get("turn_budgets")
            with open(dispositions_path, "w", encoding="utf-8") as f:
                json.dump([{"id": "FIND-001", "disposition": "applied", "rejection_reason": None}], f)
            return TurnResult(returncode=0)

        with (
            patch("sandbox_executor.entrypoint.review_loop.get_runner", return_value=get_runner("antigravity-agent")),
            patch("sandbox_executor.entrypoint.review_loop.run_turn", side_effect=fake_run_turn),
        ):
            _run_resolver_agent(str(tmp_path), "antigravity-agent", "test-model", "prompt", dispositions_path)
        return captured["turn_budgets"]

    def test_resolver_turn_receives_configured_budgets(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOLON_TURN_STALL_SECONDS", "900")
        monkeypatch.setenv("HOLON_TURN_TIMEOUT_SECONDS", "1800")

        turn_budgets = self._captured_turn_budgets(tmp_path)

        assert isinstance(turn_budgets, TurnBudgets)
        assert turn_budgets.stall_seconds == 900
        assert turn_budgets.turn_seconds == 1800

    def test_resolver_turn_uses_default_budgets_when_unset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("HOLON_TURN_STALL_SECONDS", raising=False)
        monkeypatch.delenv("HOLON_TURN_TIMEOUT_SECONDS", raising=False)

        assert self._captured_turn_budgets(tmp_path) == TurnBudgets()


class TestResolverTurnCallShape:
    def test_run_resolver_agent_with_autospec_agent_runner(self, tmp_path: Path) -> None:
        repo_dir = str(tmp_path)
        dispositions_path = str(tmp_path / "dispositions.json")
        spec_runner = create_autospec(AgentRunner, instance=True)
        spec_runner.build_command.return_value = ["spec-agent", "--model", "test-model", "prompt"]
        mock_turn_result = TurnResult(returncode=0)

        with (
            patch("sandbox_executor.entrypoint.review_loop.get_runner", return_value=spec_runner),
            patch("sandbox_executor.entrypoint.review_loop.run_turn", return_value=mock_turn_result) as mock_run_turn,
            pytest.raises(RuntimeError, match="no usable output"),
        ):
            _run_resolver_agent(repo_dir, "spec-agent", "test-model", "prompt", dispositions_path)

        spec_runner.build_command.assert_called_once_with("prompt", "test-model")
        mock_run_turn.assert_called_once_with(
            ["spec-agent", "--model", "test-model", "prompt"], cwd=repo_dir, turn_budgets=ANY
        )

    def test_run_resolver_agent_fallback_to_compose_cmd(self, tmp_path: Path) -> None:
        repo_dir = str(tmp_path)
        dispositions_path = str(tmp_path / "dispositions.json")

        class FallbackOnlyRunner:
            def compose_cmd(self, model_name: str, full_prompt: str) -> list[str]:
                return ["fallback-agent", "--model", model_name, full_prompt]

        fallback_runner = FallbackOnlyRunner()
        assert not hasattr(fallback_runner, "build_command")
        mock_turn_result = TurnResult(returncode=0)

        with (
            patch("sandbox_executor.entrypoint.review_loop.get_runner", return_value=fallback_runner),
            patch("sandbox_executor.entrypoint.review_loop.run_turn", return_value=mock_turn_result) as mock_run_turn,
            pytest.raises(RuntimeError, match="no usable output"),
        ):
            _run_resolver_agent(repo_dir, "fallback-agent", "test-model", "prompt", dispositions_path)

        mock_run_turn.assert_called_once_with(
            ["fallback-agent", "--model", "test-model", "prompt"], cwd=repo_dir, turn_budgets=ANY
        )
