"""``holon ledger promote``: CLI options, output, exit codes and self-registration under ``holon ledger``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sandbox_executor.cli.commands import discover
from sandbox_executor.cli.main import main
from sandbox_executor.ledger import PromotionRaceError

LEDGER_REL = "holon-knowledge/ledger"


def _seed(root: Path, roots: int = 2) -> None:
    ledger_dir = root / LEDGER_REL
    ledger_dir.mkdir(parents=True)
    (ledger_dir / "plans.jsonl").write_text(json.dumps({"plan_id": "P-0"}) + "\n", encoding="utf-8")
    for index in range(roots):
        fragment = ledger_dir / "pending" / "plans" / f"I-{index}-r.jsonl"
        fragment.parent.mkdir(parents=True, exist_ok=True)
        fragment.write_text(json.dumps({"plan_id": f"P-{index + 1}", "intent_branch": f"I-{index}-r/_"}) + "\n")


def _run(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    return int(exc.value.code or 0)


def _promote(root: Path, *options: str) -> int:
    return _run(["ledger", "promote", "--target-worktree", str(root), *options])


def _fragments(root: Path) -> list[Path]:
    return sorted((root / LEDGER_REL / "pending").rglob("*.jsonl"))


def test_help_parses(capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(["ledger", "promote", "--help"]) == 0
    out = capsys.readouterr().out
    assert "--force" in out and "--check-only" in out and "no active flows" in out


def test_check_only_reports_and_changes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seed(tmp_path)
    assert _promote(tmp_path, "--check-only") == 0
    assert capsys.readouterr().out.strip() == "root intents with fragments: 2 / 10 (threshold not met)"
    assert len(_fragments(tmp_path)) == 2


def test_below_threshold_message(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seed(tmp_path)
    assert _promote(tmp_path) == 0
    assert "below threshold, nothing promoted (2 / 10); use --force to promote now" in capsys.readouterr().out
    assert len(_fragments(tmp_path)) == 2


@pytest.mark.parametrize("options", [("--force",), ("--every", "1"), ("--every", "2")])
def test_promotes_when_forced_or_threshold_met(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], options: tuple[str, ...]
) -> None:
    _seed(tmp_path)
    assert _promote(tmp_path, *options) == 0
    out = capsys.readouterr().out
    assert "plans.jsonl: appended 2 row(s)" in out and "deleted 2 promoted fragment(s)" in out
    assert _fragments(tmp_path) == []


def test_environment_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(tmp_path)
    monkeypatch.setenv("HOLON_LEDGER_PROMOTE_EVERY", "1")
    assert _promote(tmp_path) == 0
    assert _fragments(tmp_path) == []


@pytest.mark.parametrize("options", [(), ("--check-only",)])
def test_invalid_environment_threshold_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], options: tuple[str, ...]
) -> None:
    _seed(tmp_path)
    monkeypatch.setenv("HOLON_LEDGER_PROMOTE_EVERY", "zero")
    assert _promote(tmp_path, *options) == 2
    assert "HOLON_LEDGER_PROMOTE_EVERY" in capsys.readouterr().err
    assert len(_fragments(tmp_path)) == 2


@pytest.mark.parametrize("value", ["0", "-1", "two"])
def test_invalid_every_is_a_usage_error(tmp_path: Path, value: str) -> None:
    _seed(tmp_path)
    assert _promote(tmp_path, "--every", value) == 2
    assert len(_fragments(tmp_path)) == 2


def test_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seed(tmp_path)
    bad = tmp_path / LEDGER_REL / "pending" / "intents" / "I-0-r.jsonl"
    bad.parent.mkdir(parents=True)
    bad.write_text("{oops\n", encoding="utf-8")
    assert _promote(tmp_path, "--force", "--json") == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["root_intents"] == 2 and result["every"] == 10
    assert result["forced"] is True and result["threshold_met"] is False
    assert result["appended"] == {"plans.jsonl": 2}
    assert len(result["deleted"]) == 2
    assert result["held"] == [f"{LEDGER_REL}/pending/intents/I-0-r.jsonl"]
    assert "held malformed fragment" in captured.err


def test_check_only_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seed(tmp_path)
    assert _promote(tmp_path, "--check-only", "--json", "--every", "2") == 0
    assert json.loads(capsys.readouterr().out) == {"root_intents": 2, "every": 2, "threshold_met": True}


def test_race_exits_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def racing_promote(*_args: object, **_kwargs: object) -> None:
        raise PromotionRaceError("fragment changed")

    monkeypatch.setattr("sandbox_executor.cli.commands.ledger_promote.promote", racing_promote)
    _seed(tmp_path)
    assert _promote(tmp_path, "--force") == 1
    assert "fragment changed" in capsys.readouterr().err


def test_ledger_query_still_works(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ledger = tmp_path / "executions.jsonl"
    ledger.write_text(json.dumps({"entry_type": "action", "action_id": "ACT-1"}) + "\n", encoding="utf-8")
    assert _run(["ledger", "query", "--ledger-file", str(ledger), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["action_id"] == "ACT-1"


def test_ledger_promote_is_not_a_top_level_command() -> None:
    names = [module.__name__.rpartition(".")[2] for module in discover()]
    assert "ledger" in names and "ledger_promote" not in names
