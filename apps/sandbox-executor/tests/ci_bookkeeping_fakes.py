"""A fake GitHub REST API and payload builders for the bookkeeping-only push gate tests.

``FakeGitHub`` is passed as the opener of ``GateClient``. It serves the URL shapes the gate reads (compare, the runs
of a workflow by branch or by head commit, the jobs of a run, and for a push to ``main`` the pull requests of a commit
and the tree of a commit) from data given by the test, records every requested URL and its ``Authorization`` header,
and raises ``AssertionError`` for any other URL, so nothing reaches the network. The builders return recorded-shape
entries with defaults: a run, a merged pull request, a job, and the three shapes of a successful job (a real success, a
job the gate skipped, and a success from before the gate existed).
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

from scripts.ci_bookkeeping_chain import FULL_RUN_STEP

REPO = "acme/widgets"
API = f"https://api.github.com/repos/{REPO}/"
TOKEN = "fake-token-for-the-bookkeeping-gate"
BRANCH = "I-1-intent/P-1-plan/E-1-execution/_"
WORKFLOW = "test-unit.yml"
# The names the jobs API shows for the two jobs of the unit workflow's matrix.
UNIT_JOB = "unit (ubuntu-latest)"
MACOS_JOB = "unit (macos-latest)"

_COMPARE = re.compile(r"^compare/([^/?]+)\.\.\.([^/?]+)$")
_RUNS = re.compile(r"^actions/workflows/([^/?]+)/runs\?(.+)$")
_JOBS = re.compile(r"^actions/runs/(\d+)/jobs\?per_page=100$")
_PULLS = re.compile(r"^commits/([^/?]+)/pulls\?per_page=100$")
_COMMIT = re.compile(r"^git/commits/([^/?]+)$")


def run(run_number: int, head_sha: str, **overrides: object) -> dict[str, object]:
    """Return a recorded-shape ``workflow_runs`` entry: a completed run of one pull request, id ``1000 + number``."""
    base: dict[str, object] = {
        "id": 1000 + run_number,
        "run_number": run_number,
        "head_sha": head_sha,
        "head_branch": BRANCH,
        "event": "pull_request",
        "status": "completed",
        "path": f".github/workflows/{WORKFLOW}",
        "pull_requests": [{"number": 7}],
    }
    return {**base, **overrides}


def pull(number: int, head_sha: str, merge_commit_sha: str, **overrides: object) -> dict[str, object]:
    """Return a recorded-shape pull request entry: merged into ``main`` with ``merge_commit_sha``."""
    base: dict[str, object] = {
        "number": number,
        "state": "closed",
        "merged_at": "2026-11-26T10:00:00Z",
        "merge_commit_sha": merge_commit_sha,
        "base": {"ref": "main", "sha": "f" * 40},
        "head": {"ref": BRANCH, "sha": head_sha},
    }
    return {**base, **overrides}


def job(name: str = UNIT_JOB, marker: str | None = None, **overrides: object) -> dict[str, object]:
    """Return a recorded-shape job entry that concluded ``success``; ``marker`` is the marker step's conclusion."""
    steps = [
        {"name": "Checkout repository", "conclusion": "success"},
        {"name": "Bookkeeping gate", "conclusion": "success"},
    ]
    if marker is not None:
        steps.append({"name": FULL_RUN_STEP, "conclusion": marker})
    base: dict[str, object] = {"name": name, "status": "completed", "conclusion": "success", "steps": steps}
    return {**base, **overrides}


def real_success_job(name: str = UNIT_JOB) -> dict[str, object]:
    """Return a job that ran in full and passed: the marker step concluded ``success``."""
    return job(name, marker="success")


def gate_skipped_job(name: str = UNIT_JOB) -> dict[str, object]:
    """Return a job the gate skipped: the job concluded ``success`` and the marker step ``skipped``."""
    return job(name, marker="skipped")


def legacy_success_job(name: str = UNIT_JOB) -> dict[str, object]:
    """Return a successful job from before the gate existed: it has no marker step."""
    return job(name)


def comparison(*files: str | tuple[str, str], status: str = "ahead") -> dict[str, object]:
    """Return a compare answer; a file is a path or, for a rename, an ``(old name, new name)`` pair."""
    entries: list[dict[str, str]] = []
    for item in files:
        if isinstance(item, tuple):
            entries.append({"filename": item[1], "previous_filename": item[0], "status": "renamed"})
        else:
            entries.append({"filename": item, "status": "modified"})
    return {"status": status, "files": entries}


class FakeGitHub:
    """Serve compare answers, runs and jobs; ``errors`` and ``bodies`` map a URL part to an exception or raw bytes."""

    def __init__(self, compares: dict[tuple[str, str], object] | None = None) -> None:
        self.compares: dict[tuple[str, str], object] = dict(compares or {})
        self.runs: list[dict[str, object]] = []
        self.jobs: dict[int, object] = {}
        self.pulls: dict[str, list[dict[str, object]]] = {}
        self.trees: dict[str, str] = {}
        self.errors: dict[str, Exception] = {}
        self.bodies: dict[str, bytes] = {}
        self.urls: list[str] = []
        self.auth: list[str | None] = []

    def add_run(self, number: int, head_sha: str, *jobs: dict[str, object], **overrides: object) -> FakeGitHub:
        """Add one earlier run with its jobs and return the fake, so that calls can be chained."""
        entry = run(number, head_sha, **overrides)
        self.runs.append(entry)
        self.jobs[int(str(entry["id"]))] = list(jobs)
        return self

    def calls(self, part: str) -> list[str]:
        """Return the requested URLs that contain ``part``."""
        return [url for url in self.urls if part in url]

    def __call__(self, request: urllib.request.Request) -> bytes:
        url = request.full_url
        self.urls.append(url)
        self.auth.append(request.unredirected_hdrs.get("Authorization"))
        assert url.startswith(API), f"request outside the repository API: {url}"
        for part, error in self.errors.items():
            if part in url:
                raise error
        for part, body in self.bodies.items():
            if part in url:
                return body
        return json.dumps(self._answer(url[len(API) :])).encode()

    def _answer(self, path: str) -> object:
        compare = _COMPARE.match(path)
        if compare:
            key = (urllib.parse.unquote(compare[1]), urllib.parse.unquote(compare[2]))
            assert key in self.compares, f"no compare answer for {key}"
            return self.compares[key]
        runs = _RUNS.match(path)
        if runs:
            query = urllib.parse.parse_qs(runs[2], strict_parsing=True)
            assert query["event"] == ["pull_request"] and query["per_page"] == ["100"], f"unexpected query: {path}"
            workflow_path = f".github/workflows/{urllib.parse.unquote(runs[1])}"
            if "head_sha" in query:
                found = [r for r in self.runs if (r["path"], r["head_sha"]) == (workflow_path, query["head_sha"][0])]
                return {"workflow_runs": found}
            wanted = (f".github/workflows/{urllib.parse.unquote(runs[1])}", query["branch"][0])
            return {"workflow_runs": [r for r in self.runs if (r["path"], r["head_branch"]) == wanted]}
        pulls = _PULLS.match(path)
        if pulls:
            assert pulls[1] in self.pulls, f"no pull requests for {pulls[1]}"
            return self.pulls[pulls[1]]
        commit = _COMMIT.match(path)
        if commit:
            assert commit[1] in self.trees, f"no tree for {commit[1]}"
            return {"sha": commit[1], "tree": {"sha": self.trees[commit[1]]}}
        jobs = _JOBS.match(path)
        assert jobs and int(jobs[1]) in self.jobs, f"unexpected request: {path}"
        return {"jobs": self.jobs[int(jobs[1])]}
