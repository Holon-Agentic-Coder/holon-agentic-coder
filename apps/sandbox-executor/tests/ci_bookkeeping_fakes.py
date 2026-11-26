"""A fake GitHub REST API and payload builders for the bookkeeping-only push gate tests.

``FakeGitHub`` is passed as the opener of ``GateClient``. It serves the URL shapes the gate reads (compare, the runs
of a workflow by branch or by head commit, the jobs of a run, for a push to ``main`` the pull requests of a commit and
the tree of a commit and the tip of ``main``, and for a merge push the parents of a commit, the merge base of a compare, recursive trees, blobs
and the push runs of a commit) from data given by the test, records every requested URL and its ``Authorization``
header, and raises ``AssertionError`` for any other URL, so nothing reaches the network. The builders return
recorded-shape entries with defaults: a run, a push run, a merged pull request, a job, the three shapes of a successful
job (a real success, a job the gate skipped, and a success from before the gate existed), a recursive tree, and a whole
merge scene (``merge_scene``).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import urllib.parse
import urllib.request
from collections.abc import Mapping

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
_TREE = re.compile(r"^git/trees/([^/?]+)\?recursive=1$")
_BLOB = re.compile(r"^git/blobs/([^/?]+)$")
_REF = re.compile(r"^git/ref/heads/([^/?]+)$")
# The commits of the usual merge scene: the merge base, the previous head, the merged-in main commit and the new head.
MERGE_BASE_SHA = "a" * 40
PREVIOUS_HEAD_SHA = "b" * 40
BASE_HEAD_SHA = "e" * 40
NEW_HEAD_SHA = "c" * 40
PREVIOUS_RUN = 49
BASE_PUSH_RUN = 70


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


def push_run(run_number: int, head_sha: str, **overrides: object) -> dict[str, object]:
    """Return a recorded-shape ``workflow_runs`` entry of a completed push run on ``main``, id ``1000 + number``."""
    return run(run_number, head_sha, **{"head_branch": "main", "event": "push", "pull_requests": [], **overrides})


def blob_id(data: bytes) -> str:
    """Return the git blob id of ``data``, as ``git hash-object`` computes it."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def tree_of(files: Mapping[str, str], truncated: bool = False) -> dict[str, object]:
    """Return a recorded-shape recursive tree answer of regular files with the given text content."""
    entries: list[dict[str, str]] = []
    for path in sorted(files):
        directories = path.split("/")[:-1]
        for depth in range(len(directories)):
            entry = {"path": "/".join(directories[: depth + 1]), "mode": "040000", "type": "tree", "sha": "d" * 40}
            if entry not in entries:
                entries.append(entry)
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob_id(files[path].encode())})
    return {"sha": "t" * 40, "tree": entries, "truncated": truncated}


def merge_base_answer(base_sha: str, base_tree: str, head_tree: str) -> dict[str, object]:
    """Return a compare answer that names the merge base ``base_sha`` and the trees of it and of the compare's base."""
    return {
        "status": "diverged",
        "merge_base_commit": {"sha": base_sha, "commit": {"tree": {"sha": base_tree}}},
        "base_commit": {"sha": PREVIOUS_HEAD_SHA, "commit": {"tree": {"sha": head_tree}}},
        "files": [],
    }


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
        self.commits: dict[str, tuple[str, list[str]]] = {}
        self.tree_listings: dict[str, dict[str, object]] = {}
        self.blobs: dict[str, bytes] = {}
        self.merge_bases: dict[tuple[str, str], object] = {}
        self.refs: dict[str, str] = {}
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

    def add_push_run(self, number: int, head_sha: str, *jobs: dict[str, object], **overrides: object) -> FakeGitHub:
        """Add one push run on ``main`` with its jobs and return the fake."""
        entry = push_run(number, head_sha, **overrides)
        self.runs.append(entry)
        self.jobs[int(str(entry["id"]))] = list(jobs)
        return self

    def set_tip(self, sha: str) -> FakeGitHub:
        """Serve ``sha`` as the tip of ``main`` and return the fake."""
        self.refs["main"] = sha
        return self

    def add_tree(self, tree_sha: str, files: Mapping[str, str], truncated: bool = False) -> FakeGitHub:
        """Serve ``files`` as the recursive tree ``tree_sha`` and their contents as blobs; return the fake."""
        self.tree_listings[tree_sha] = {**tree_of(files, truncated), "sha": tree_sha}
        self.blobs.update({blob_id(text.encode()): text.encode() for text in files.values()})
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
            return self._compare((urllib.parse.unquote(compare[1]), urllib.parse.unquote(compare[2])))
        runs = _RUNS.match(path)
        if runs:
            return self._runs(urllib.parse.unquote(runs[1]), urllib.parse.parse_qs(runs[2], strict_parsing=True))
        pulls = _PULLS.match(path)
        if pulls:
            assert pulls[1] in self.pulls, f"no pull requests for {pulls[1]}"
            return self.pulls[pulls[1]]
        commit = _COMMIT.match(path)
        if commit:
            return self._commit(commit[1])
        return self._git_object(path)

    def _compare(self, key: tuple[str, str]) -> object:
        if key not in self.compares and key in self.merge_bases:
            return self.merge_bases[key]
        assert key in self.compares, f"no compare answer for {key}"
        return self.compares[key]

    def _runs(self, workflow_file: str, query: dict[str, list[str]]) -> dict[str, object]:
        if query["event"] == ["push"] and "head_sha" in query:
            return self._push_runs(workflow_file, query)
        assert query["event"] == ["pull_request"] and query["per_page"] == ["100"], f"unexpected query: {query}"
        workflow_path = f".github/workflows/{workflow_file}"
        if "head_sha" in query:
            found = [r for r in self.runs if (r["path"], r["head_sha"]) == (workflow_path, query["head_sha"][0])]
            return {"workflow_runs": found}
        wanted = (workflow_path, query["branch"][0])
        return {"workflow_runs": [r for r in self.runs if (r["path"], r["head_branch"]) == wanted]}

    def _commit(self, sha: str) -> object:
        if sha in self.commits:
            tree, parents = self.commits[sha]
            return {"sha": sha, "tree": {"sha": tree}, "parents": [{"sha": parent} for parent in parents]}
        assert sha in self.trees, f"no tree for {sha}"
        return {"sha": sha, "tree": {"sha": self.trees[sha]}}

    def _git_object(self, path: str) -> object:
        ref = _REF.match(path)
        if ref:
            assert ref[1] in self.refs, f"no ref {ref[1]}"
            return {"ref": f"refs/heads/{ref[1]}", "object": {"sha": self.refs[ref[1]], "type": "commit"}}
        tree = _TREE.match(path)
        if tree:
            assert tree[1] in self.tree_listings, f"no tree listing for {tree[1]}"
            return self.tree_listings[tree[1]]
        blob = _BLOB.match(path)
        if blob:
            assert blob[1] in self.blobs, f"no blob {blob[1]}"
            content = base64.encodebytes(self.blobs[blob[1]]).decode()
            return {"sha": blob[1], "size": len(self.blobs[blob[1]]), "content": content, "encoding": "base64"}
        jobs = _JOBS.match(path)
        assert jobs and int(jobs[1]) in self.jobs, f"unexpected request: {path}"
        return {"jobs": self.jobs[int(jobs[1])]}

    def _push_runs(self, workflow_file: str, query: dict[str, list[str]]) -> dict[str, object]:
        assert query["per_page"] == ["100"] and "branch" in query, f"unexpected push-run query: {query}"
        wanted = (f".github/workflows/{workflow_file}", query["branch"][0], query["head_sha"][0], "push")
        found = [r for r in self.runs if (r["path"], r["head_branch"], r["head_sha"], r["event"]) == wanted]
        return {"workflow_runs": found}


# The tree ids of the usual merge scene.
TREE_IDS = {"base": "1" * 40, "previous": "2" * 40, "merged_in": "3" * 40, "merged": "4" * 40}


def merge_scene(
    base: Mapping[str, str], previous: Mapping[str, str], merged_in: Mapping[str, str], merged: Mapping[str, str]
) -> FakeGitHub:
    """Return a fake for the merge of ``BASE_HEAD_SHA`` into ``PREVIOUS_HEAD_SHA`` as ``NEW_HEAD_SHA``.

    The four mappings are the files of the merge base, the previous head, the merged-in main commit and the new head.
    The unit job really succeeded in pull-request run ``PREVIOUS_RUN`` on the previous head and in push run
    ``BASE_PUSH_RUN`` on the merged-in commit. The test adds the ``before...head`` compare it needs.
    """
    fake = FakeGitHub()
    fake.commits[NEW_HEAD_SHA] = (TREE_IDS["merged"], [PREVIOUS_HEAD_SHA, BASE_HEAD_SHA])
    fake.commits[BASE_HEAD_SHA] = (TREE_IDS["merged_in"], [MERGE_BASE_SHA])
    fake.merge_bases[(PREVIOUS_HEAD_SHA, BASE_HEAD_SHA)] = merge_base_answer(
        MERGE_BASE_SHA, TREE_IDS["base"], TREE_IDS["previous"]
    )
    for name, files in (("base", base), ("previous", previous), ("merged_in", merged_in), ("merged", merged)):
        fake.add_tree(TREE_IDS[name], files)
    fake.add_run(PREVIOUS_RUN, PREVIOUS_HEAD_SHA, job(marker="success"))
    return fake.add_push_run(BASE_PUSH_RUN, BASE_HEAD_SHA, job(marker="success"))
