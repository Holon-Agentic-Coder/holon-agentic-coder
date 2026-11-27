from __future__ import annotations

import ast
import io
import json
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from scripts import check_module_budget_label_actor as actor_check
from scripts.check_module_budget_label_actor import (
    BUDGET_CHANGE_LABEL,
    BUDGET_CONFIG,
    PAGE_SIZE,
    active_label_event,
    changed_paths,
    label_actor_refusal,
    main,
)
from tests.test_ci_bookkeeping_wiring import split_steps

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "check_module_budget_label_actor.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "test-hygiene.yml"
REPO = "holon-org/holon"
PULL_REQUEST = 42
TOKEN = "ghs_secretTokenValue123"
ARGUMENTS = ["--pr", str(PULL_REQUEST), "--repo", REPO, "--base", "base-sha", "--head", "head-sha"]
EVENTS_PATH = f"/repos/{REPO}/issues/{PULL_REQUEST}/events"
THIN_API_FUNCTIONS = {"build_request", "default_opener", "fetch_issue_events", "fetch_collaborator_permission"}


def github_actor(login: str, kind: str = "User") -> dict:
    return {
        "login": login,
        "id": sum(map(ord, login)),
        "node_id": f"MDQ6VXNlcj{login}",
        "type": kind,
        "site_admin": False,
    }


def issue_event(event_id: int, event: str, login: str, label: str | None = None, kind: str = "User") -> dict:
    answer = {
        "id": event_id,
        "node_id": f"LE_kwDO{event_id}",
        "url": f"https://api.github.com/repos/{REPO}/issues/events/{event_id}",
        "actor": github_actor(login, kind),
        "event": event,
        "commit_id": None,
        "commit_url": None,
        "created_at": f"2026-11-{10 + event_id % 10:02d}T12:00:00Z",
        "performed_via_github_app": None,
    }
    if label is not None:
        answer["label"] = {"name": label, "color": "d73a4a"}
    return answer


UNRELATED_EVENTS = [
    issue_event(1, "labeled", "maintainer", "enhancement"),
    issue_event(2, "review_requested", "maintainer"),
    issue_event(3, "head_ref_force_pushed", "contributor"),
]


def labeled(event_id: int, login: str, kind: str = "User") -> dict:
    return issue_event(event_id, "labeled", login, BUDGET_CHANGE_LABEL, kind)


def unlabeled(event_id: int, login: str) -> dict:
    return issue_event(event_id, "unlabeled", login, BUDGET_CHANGE_LABEL)


def permission_answer(login: str, permission: str) -> dict:
    return {"permission": permission, "role_name": permission, "user": github_actor(login)}


class FakeGitHub:
    def __init__(self, event_pages: list[list[dict]], permissions: dict[str, str] | None = None) -> None:
        self.event_pages = event_pages
        self.permissions = permissions or {}
        self.requests: list[urllib.request.Request] = []

    @property
    def paths(self) -> list[str]:
        return [urllib.parse.urlsplit(request.full_url).path for request in self.requests]

    def __call__(self, request: urllib.request.Request) -> bytes:
        self.requests.append(request)
        url = urllib.parse.urlsplit(request.full_url)
        if url.path == EVENTS_PATH:
            page = int(urllib.parse.parse_qs(url.query)["page"][0])
            return json.dumps(self.event_pages[page - 1] if page <= len(self.event_pages) else []).encode()
        login = url.path.removeprefix(f"/repos/{REPO}/collaborators/").removesuffix("/permission")
        if login not in self.permissions:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b"{}"))  # type: ignore[arg-type]
        return json.dumps(permission_answer(login, self.permissions[login])).encode()


@pytest.fixture
def config_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(actor_check, "changed_paths", lambda base, head: ["README.md", BUDGET_CONFIG])


def run_check(github: FakeGitHub, token: str = TOKEN) -> int:
    return main(ARGUMENTS, {"GITHUB_TOKEN": token}, github)


@pytest.mark.usefixtures("config_changed")
def test_a_label_applied_by_a_user_with_write_permission_fails_naming_the_file_and_the_label(
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = FakeGitHub([[*UNRELATED_EVENTS, labeled(4, "triager")]], {"triager": "write"})
    assert run_check(github) == 1
    stderr = capsys.readouterr().err
    assert BUDGET_CONFIG in stderr
    assert BUDGET_CHANGE_LABEL in stderr
    assert "triager" in stderr
    assert "'write'" in stderr


@pytest.mark.usefixtures("config_changed")
def test_a_label_applied_by_an_admin_user_passes(capsys: pytest.CaptureFixture[str]) -> None:
    github = FakeGitHub([[*UNRELATED_EVENTS, labeled(4, "maintainer")]], {"maintainer": "admin"})
    assert run_check(github) == 0
    assert "maintainer" in capsys.readouterr().out
    assert github.paths[-1] == f"/repos/{REPO}/collaborators/maintainer/permission"


@pytest.mark.usefixtures("config_changed")
def test_a_label_removed_after_it_was_applied_fails_without_a_permission_request(
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = FakeGitHub(
        [[labeled(4, "maintainer"), *UNRELATED_EVENTS, unlabeled(5, "maintainer")]], {"maintainer": "admin"}
    )
    assert run_check(github) == 1
    assert "removed" in capsys.readouterr().err
    assert github.paths == [EVENTS_PATH]


@pytest.mark.usefixtures("config_changed")
def test_a_label_re_applied_by_an_admin_after_its_removal_passes() -> None:
    events = [labeled(4, "triager"), unlabeled(5, "maintainer"), labeled(6, "maintainer")]
    github = FakeGitHub([events], {"maintainer": "admin", "triager": "triage"})
    assert run_check(github) == 0


@pytest.mark.usefixtures("config_changed")
def test_a_label_applied_by_a_bot_fails_without_a_permission_request(capsys: pytest.CaptureFixture[str]) -> None:
    github = FakeGitHub([[labeled(4, "github-actions[bot]", "Bot")]], {"github-actions[bot]": "admin"})
    assert run_check(github) == 1
    stderr = capsys.readouterr().err
    assert "github-actions[bot]" in stderr
    assert "Bot" in stderr
    assert github.paths == [EVENTS_PATH]


@pytest.mark.usefixtures("config_changed")
def test_no_label_at_all_fails(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_check(FakeGitHub([UNRELATED_EVENTS])) == 1
    stderr = capsys.readouterr().err
    assert "never applied" in stderr
    assert BUDGET_CONFIG in stderr


@pytest.mark.usefixtures("config_changed")
def test_an_actor_who_is_not_a_collaborator_counts_as_without_permission(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_check(FakeGitHub([[labeled(4, "outsider")]])) == 1
    assert "'none'" in capsys.readouterr().err


@pytest.mark.usefixtures("config_changed")
def test_the_active_label_on_a_later_page_is_found() -> None:
    filler = [issue_event(100 + index, "review_requested", "maintainer") for index in range(PAGE_SIZE)]
    github = FakeGitHub([filler, [labeled(4, "maintainer")]], {"maintainer": "admin"})
    assert run_check(github) == 0
    assert github.paths.count(EVENTS_PATH) == 2


@pytest.mark.usefixtures("config_changed")
def test_the_token_never_appears_in_the_output(capsys: pytest.CaptureFixture[str]) -> None:
    run_check(FakeGitHub([[labeled(4, "triager")]], {"triager": "write"}))
    run_check(FakeGitHub([[labeled(4, "maintainer")]], {"maintainer": "admin"}))
    output = capsys.readouterr()
    assert TOKEN not in output.out + output.err


@pytest.mark.usefixtures("config_changed")
def test_the_token_travels_only_as_an_unredirected_authorization_header() -> None:
    github = FakeGitHub([[labeled(4, "maintainer")]], {"maintainer": "admin"})
    run_check(github)
    for request in github.requests:
        assert TOKEN not in request.full_url
        assert request.unredirected_hdrs["Authorization"] == f"Bearer {TOKEN}"
        assert "Authorization" not in request.headers


@pytest.mark.usefixtures("config_changed")
def test_a_missing_token_fails_when_the_config_may_change(capsys: pytest.CaptureFixture[str]) -> None:
    github = FakeGitHub([[labeled(4, "maintainer")]], {"maintainer": "admin"})
    assert run_check(github, token="") == 1
    assert "GITHUB_TOKEN" in capsys.readouterr().err
    assert github.requests == []


@pytest.mark.usefixtures("config_changed")
def test_an_answer_that_is_not_an_event_list_fails() -> None:
    assert main(ARGUMENTS, {"GITHUB_TOKEN": TOKEN}, lambda request: b'{"message": "Moved"}') == 1


def test_an_unchanged_config_exits_zero_without_any_api_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(actor_check, "changed_paths", lambda base, head: ["scripts/check_module_budgets.py"])
    github = FakeGitHub([UNRELATED_EVENTS])
    assert main(ARGUMENTS, {}, github) == 0
    assert github.requests == []


def test_a_git_failure_counts_as_a_changed_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(actor_check, "changed_paths", lambda base, head: None)
    github = FakeGitHub([UNRELATED_EVENTS])
    assert run_check(github) == 1
    assert github.paths == [EVENTS_PATH]


def test_active_label_event_picks_the_last_labeled_event_without_a_later_removal() -> None:
    events = [labeled(1, "a"), unlabeled(2, "b"), *UNRELATED_EVENTS, labeled(7, "c")]
    event, reason = active_label_event(events, BUDGET_CHANGE_LABEL)
    assert event is not None
    assert event["id"] == 7
    assert reason == ""
    assert active_label_event([labeled(1, "a"), unlabeled(2, "b")], BUDGET_CHANGE_LABEL)[0] is None
    assert active_label_event(UNRELATED_EVENTS, BUDGET_CHANGE_LABEL) == (None, "the label was never applied")


@pytest.mark.parametrize(
    ("event", "permission", "expected"),
    [
        (labeled(1, "maintainer"), "admin", None),
        (labeled(1, "maintainer"), "maintain", "applied by maintainer, whose permission is 'maintain', not 'admin'"),
        (labeled(1, "renovate[bot]", "Bot"), "admin", "applied by renovate[bot], an actor of type Bot, not a User"),
    ],
)
def test_label_actor_refusal_requires_an_admin_user(event: dict, permission: str, expected: str | None) -> None:
    assert label_actor_refusal(event, permission) == expected


def git(repository: Path, *arguments: str) -> str:
    command = ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false"]
    return subprocess.run([*command, *arguments], cwd=repository, check=True, capture_output=True, text=True).stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_changed_paths_lists_the_three_dot_diff_and_fails_closed_on_an_unknown_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git(tmp_path, "init", "-q")
    (tmp_path / "README.md").write_text("one\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    base = git(tmp_path, "rev-parse", "HEAD").strip()
    config = tmp_path / BUDGET_CONFIG
    config.parent.mkdir(parents=True)
    config.write_text("{}\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "head")
    head = git(tmp_path, "rev-parse", "HEAD").strip()
    monkeypatch.chdir(tmp_path)
    assert changed_paths(base, head) == [BUDGET_CONFIG]
    assert changed_paths("0" * 40, head) is None


def label_actor_step() -> str:
    steps = split_steps(WORKFLOW.read_text(encoding="utf-8"))
    names = [step.name for step in steps]
    index = names.index("Check module budget label actor")
    assert names[index - 1] == "Check module budgets"
    return steps[index].text


def test_the_hygiene_workflow_runs_the_check_on_pull_request_events_only() -> None:
    step = label_actor_step()
    run = step.partition("run: |\n")[2]
    assert 'if [ "$GITHUB_EVENT_NAME" = "pull_request" ]; then' in run
    assert "scripts/check_module_budget_label_actor.py" in run.partition("then")[2].partition("fi")[0]
    assert "${{" not in run
    assert "GITHUB_TOKEN: ${{ github.token }}" in step
    assert "labels.*.name" not in step


def test_the_hygiene_job_may_read_issues_and_pull_requests_and_write_nothing() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    block = text.partition("\n    permissions:\n")[2].partition("\n    strategy:\n")[0]
    scopes = [line.strip() for line in block.splitlines()]
    assert scopes == ["contents: read", "actions: read", "issues: read", "pull-requests: read"]


def is_network_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    called = ast.unparse(node.func)
    return called == "opener" or called.endswith("urlopen") or called.startswith("urllib.request.")


def test_the_script_reaches_the_network_only_inside_its_thin_api_functions() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    reaching = {function.name for function in functions if any(map(is_network_call, ast.walk(function)))}
    assert reaching == THIN_API_FUNCTIONS
    top_level = [node for node in tree.body if not isinstance(node, (ast.FunctionDef, ast.If))]
    assert not any(is_network_call(node) for statement in top_level for node in ast.walk(statement))
