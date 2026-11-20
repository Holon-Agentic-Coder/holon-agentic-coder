"""The host never pushes a resolver head that changes the frozen module budget configuration without authorisation,
and role containers see ``HOLON_ALLOW_MODULE_BUDGET_CHANGE`` only when the intent carries ``module_budget_change``."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sandbox_executor.flow import FlowContext
from sandbox_executor.flow.dispatch import _dispatch_role
from sandbox_executor.flow.review import resolver
from sandbox_executor.module_budget_guard import (
    ALLOW_MODULE_BUDGET_CHANGE_ENV,
    MODULE_BUDGET_CONFIG,
    budget_push_refusal,
)
from sandbox_executor.review_target import ReviewTarget

from tests.flow.helpers import _configure_identity, _git

AUTHORISED = {"module_budget_change": "Maintainer, 2026-11-20: raise pipeline.py to 440 lines."}


def _commit(repo: Path, path: str, text: str) -> str:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", f"edit {path}")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _configure_identity(tmp_path)
    _commit(tmp_path, MODULE_BUDGET_CONFIG, '{"default_max_lines": 200}\n')
    return tmp_path


@pytest.fixture
def pushes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(resolver, "push_commit", lambda *args: calls.append(args))
    return calls


def _push(repo: Path, base: str, head: str, intent_data: dict) -> tuple[bool, str | None]:
    context = FlowContext(target_worktree=str(repo), intent_data=intent_data)
    target = ReviewTarget(raw="feature", head_branch="feature", head_sha=base)
    return resolver._push_resolver_head(context, target, head, None)


def test_a_config_changing_resolver_head_is_not_pushed_without_authorisation(repo: Path, pushes):
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, MODULE_BUDGET_CONFIG, '{"default_max_lines": 999}\n')
    pushed, error = _push(repo, base, head, {})
    assert pushed is False
    assert error is not None and "Resolver push failed" in error and "module budget configuration is frozen" in error
    assert pushes == []


def test_a_config_changing_resolver_head_is_pushed_when_authorised(repo: Path, pushes):
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, MODULE_BUDGET_CONFIG, '{"default_max_lines": 999}\n')
    assert _push(repo, base, head, AUTHORISED) == (True, None)
    assert len(pushes) == 1


def test_a_head_that_leaves_the_config_alone_is_pushed(repo: Path, pushes):
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, "src/app.py", "print('fixed')\n")
    assert _push(repo, base, head, {}) == (True, None)
    assert len(pushes) == 1


def test_the_refusal_fails_closed_and_ignores_non_object_ids(repo: Path):
    base = _git(repo, "rev-parse", "HEAD")
    assert budget_push_refusal(str(repo), base, "--receive-pack=touch /tmp/x", {}) is None
    missing = "0" * 40
    assert "could not list the files changed" in str(budget_push_refusal(str(repo), base, missing, {}))


@pytest.mark.parametrize(("intent_data", "expected"), [({}, None), (AUTHORISED, "1")])
def test_role_dispatch_exports_the_flag_only_when_authorised(monkeypatch: pytest.MonkeyPatch, intent_data, expected):
    monkeypatch.setenv(ALLOW_MODULE_BUDGET_CHANGE_ENV, "1")  # a host value never authorises by itself
    seen: dict[str, str | None] = {}

    def dispatcher(*args, **kwargs) -> int:
        seen["flag"] = os.environ.get(ALLOW_MODULE_BUDGET_CHANGE_ENV)
        return 0

    context = FlowContext(target_worktree=".", intent_data=intent_data, dispatcher=dispatcher)
    assert _dispatch_role(context, "executor", []) == 0
    assert seen["flag"] == expected
    assert os.environ[ALLOW_MODULE_BUDGET_CHANGE_ENV] == "1"
