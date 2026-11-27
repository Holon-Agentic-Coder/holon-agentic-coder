#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence

BUDGET_CONFIG = "holon-config/hygiene/module_budgets.json"
BUDGET_CHANGE_LABEL = "module-budget-change"
API_ROOT = "https://api.github.com"
PAGE_SIZE = 100
REQUEST_TIMEOUT_SECONDS = 30
TOKEN_ENV = "GITHUB_TOKEN"
AUTHORISING_PERMISSION = "admin"
AUTHORISING_ACTOR_TYPE = "User"
NOT_A_COLLABORATOR = 404

Opener = Callable[[urllib.request.Request], bytes]


def changed_paths(base: str, head: str) -> list[str] | None:
    try:
        diff = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...{head}"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if diff.returncode != 0:
        return None
    return [line for line in diff.stdout.splitlines() if line]


def config_may_change(paths: list[str] | None) -> bool:
    return paths is None or BUDGET_CONFIG in paths


def build_request(url: str, token: str) -> urllib.request.Request:
    request = urllib.request.Request(url)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    # why: an unredirected header never follows a redirect to another host, so the token stays with api.github.com.
    request.add_unredirected_header("Authorization", f"Bearer {token}")
    return request


def default_opener(request: urllib.request.Request) -> bytes:
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return response.read()


def fetch_issue_events(repo: str, number: int, token: str, opener: Opener) -> list[dict]:
    events: list[dict] = []
    page = 1
    while True:
        url = f"{API_ROOT}/repos/{repo}/issues/{number}/events?per_page={PAGE_SIZE}&page={page}"
        batch = json.loads(opener(build_request(url, token)))
        if not isinstance(batch, list):
            raise ValueError(f"the issue events answer for page {page} is not a JSON array")
        events.extend(batch)
        if len(batch) < PAGE_SIZE:
            return events
        page += 1


def fetch_collaborator_permission(repo: str, login: str, token: str, opener: Opener) -> str:
    url = f"{API_ROOT}/repos/{repo}/collaborators/{urllib.parse.quote(login, safe='')}/permission"
    try:
        answer = json.loads(opener(build_request(url, token)))
    except urllib.error.HTTPError as error:
        if error.code == NOT_A_COLLABORATOR:
            return "none"
        raise
    return str(answer["permission"])


def label_events(events: Sequence[Mapping], label: str) -> list[Mapping]:
    return [
        event
        for event in events
        if event.get("event") in ("labeled", "unlabeled") and (event.get("label") or {}).get("name") == label
    ]


def active_label_event(events: Sequence[Mapping], label: str) -> tuple[Mapping | None, str]:
    matching = label_events(events, label)
    if not matching:
        return None, "the label was never applied"
    if matching[-1]["event"] == "unlabeled":
        return None, "the label was removed after it was last applied"
    return matching[-1], ""


def actor_login(event: Mapping) -> str:
    return str((event.get("actor") or {}).get("login", ""))


def actor_type(event: Mapping) -> str:
    return str((event.get("actor") or {}).get("type", ""))


def label_actor_refusal(event: Mapping, permission: str) -> str | None:
    login = actor_login(event)
    kind = actor_type(event)
    if kind != AUTHORISING_ACTOR_TYPE:
        return f"applied by {login}, an actor of type {kind}, not a {AUTHORISING_ACTOR_TYPE}"
    if permission != AUTHORISING_PERMISSION:
        return f"applied by {login}, whose permission is '{permission}', not '{AUTHORISING_PERMISSION}'"
    return None


def refusal_message(reason: str) -> str:
    return (
        f"{BUDGET_CONFIG} changed, but the '{BUDGET_CHANGE_LABEL}' label does not authorise it: {reason}. "
        "Only a label applied by a repository administrator authorises a module budget change."
    )


def check(repo: str, number: int, token: str, opener: Opener) -> tuple[str | None, str]:
    event, missing_reason = active_label_event(fetch_issue_events(repo, number, token, opener), BUDGET_CHANGE_LABEL)
    if event is None:
        return missing_reason, ""
    login = actor_login(event)
    if actor_type(event) != AUTHORISING_ACTOR_TYPE:
        return label_actor_refusal(event, "none"), login
    permission = fetch_collaborator_permission(repo, login, token, opener)
    return label_actor_refusal(event, permission), login


def parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"Require the '{BUDGET_CHANGE_LABEL}' label on a change to {BUDGET_CONFIG} to come from an admin."
    )
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    return parser.parse_args(argv)


def checked_exit_code(arguments: argparse.Namespace, token: str, opener: Opener) -> int:
    try:
        refusal, login = check(arguments.repo, arguments.pr, token, opener)
    except urllib.error.URLError as error:
        print(f"Cannot read the label history of pull request #{arguments.pr}: {error.reason}", file=sys.stderr)
        return 1
    except (ValueError, KeyError, TypeError) as error:
        print(
            f"Cannot read the GitHub answer for pull request #{arguments.pr}: {type(error).__name__}", file=sys.stderr
        )
        return 1
    if refusal is not None:
        print(refusal_message(refusal), file=sys.stderr)
        return 1
    print(f"{BUDGET_CONFIG} changed; the '{BUDGET_CHANGE_LABEL}' label was applied by the administrator {login}.")
    return 0


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] = os.environ,
    opener: Opener = default_opener,
) -> int:
    arguments = parse_arguments(argv)
    if not config_may_change(changed_paths(arguments.base, arguments.head)):
        print(f"{BUDGET_CONFIG} is unchanged; no label authorisation is needed.")
        return 0
    token = environ.get(TOKEN_ENV, "")
    if not token:
        print(f"{BUDGET_CONFIG} may have changed, but {TOKEN_ENV} is not set to check the label.", file=sys.stderr)
        return 1
    return checked_exit_code(arguments, token, opener)


if __name__ == "__main__":
    sys.exit(main())
