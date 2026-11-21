"""Tests that the resolver turn honours the turn and stall budgets the environment configures."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox_executor.agent_runner import get_runner
from sandbox_executor.budgets import TurnBudgets
from sandbox_executor.entrypoint.review_loop import _run_resolver_agent
from sandbox_executor.turns import TurnResult


class TestResolverTurnBudgets:
    """The resolver turn is supervised with the turn and stall budgets the environment configures."""

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
