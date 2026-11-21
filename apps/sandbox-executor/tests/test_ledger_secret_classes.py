"""Each credential class the older ``CredentialRedactor`` patterns missed is masked in ledger rows and warnings.

All fake values are assembled at runtime from fragments under neutral names, so no token-shaped literal is in the
source.
"""

from __future__ import annotations

import json
import subprocess
from uuid import uuid4

import pytest
from sandbox_executor.flow.stages import calibrate
from sandbox_executor.knowledge import CredentialRedactor
from sandbox_executor.ledger import ActionRecord, generate_action_id, try_append_action_record

from tests.leak_assertions import assert_no_fragment, make_probe


def _body(length: int) -> str:
    return (uuid4().hex * 4)[:length]


def _token(head: str, length: int) -> tuple[str, str]:
    value = head + _body(length)
    return value, value


def _url_case() -> tuple[str, str]:
    secret = make_probe("u")
    return "https://user:" + secret + "@host.example/path", secret


CLASS_CASES = {
    "gho": lambda: _token("gh" + "o_", 36),
    "ghu": lambda: _token("gh" + "u_", 36),
    "ghs": lambda: _token("gh" + "s_", 36),
    "ghr": lambda: _token("gh" + "r_", 36),
    "ghp-30": lambda: _token("gh" + "p_", 30),
    "ghp-40": lambda: _token("gh" + "p_", 40),
    "sk-ant-short": lambda: _token("sk" + "-ant-", 12),
    "url-userinfo": _url_case,
}
ENV_CASES = ("GITHUB_TOKEN", "HOLON_AGENT_SETTINGS")


@pytest.fixture(params=[*CLASS_CASES, *ENV_CASES])
def leak(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """``(text carrying the value, the secret part that must not survive)``."""
    if request.param in ENV_CASES:
        value = make_probe(request.param.lower())
        monkeypatch.setenv(request.param, value)
        return f"failed with {value} in the output", value
    carrier, secret = CLASS_CASES[request.param]()
    return f"failed with {carrier} in the output", secret


def test_credential_redactor_masks_the_class(leak: tuple[str, str]) -> None:
    text, secret = leak

    masked = CredentialRedactor.redact_text(text)

    assert_no_fragment(masked, secret)
    assert masked.startswith("failed with ")


def test_action_row_written_to_the_ledger_masks_the_class(leak: tuple[str, str], tmp_path) -> None:
    text, secret = leak
    ledger = tmp_path / "executions.jsonl"
    record = ActionRecord(generate_action_id(), "E-1", "act", "code", "target", "failure", error_context=text)

    assert try_append_action_record(str(ledger), record, label="test") is not None

    content = ledger.read_text(encoding="utf-8")
    assert_no_fragment(content, secret)
    assert json.loads(content.splitlines()[-1])["error_context"].startswith("failed with ")


def test_stage5_reconciliation_warning_masks_the_class(leak: tuple[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    text, secret = leak
    branch = "I-1-x/P-2-y/E-3-z/calibrated"

    def fake_git(args, cwd=None, env=None, check=False):
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return subprocess.CompletedProcess(args, 0, stdout=branch + "\n", stderr="")
        if args[0] == "commit":
            return subprocess.CompletedProcess(args, 1, stdout="", stderr=text)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    class Context:
        calibrated_branch = branch

        def __init__(self) -> None:
            self.lines: list[str] = []

        def log(self, message: str) -> None:
            self.lines.append(message)

    context = Context()
    monkeypatch.setattr(calibrate, "run_git", fake_git)
    calibrate._commit_stage5_reconciliation(context, "/nonexistent")

    (line,) = context.lines
    assert "reconciliation rows failed" in line
    assert_no_fragment(line, secret)
