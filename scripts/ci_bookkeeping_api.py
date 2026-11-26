"""Everything the bookkeeping-only push gate reads from GitHub: the event of this run and three REST answers.

``context_from`` fills a ``GateContext`` from the ``GITHUB_*`` environment and the event payload; a missing value is
empty text or 0, which the gate's rules turn into a full run. The three reads are one request each: ``compare`` (the
paths changed between two commits and whether the head is strictly ahead of the base), ``list_earlier_runs`` (the
earlier pull-request runs of one workflow on one branch, newest first) and ``list_jobs`` (the jobs of one run with their
steps, latest attempt). For a push to ``main``, ``scripts.ci_main_push_gate`` adds three more reads:
``pulls_for_commit`` (the pull requests associated with a commit), ``commit_tree`` (the tree of a commit) and
``list_head_runs`` (the pull-request runs of one workflow on one head commit, newest first); the context then also
carries the pushed commit (``sha``) and the ref (``ref``). For a merge push, ``scripts.ci_merge_push_gate`` reads
``commit_ref`` (the tree and parents of a commit), ``merge_base`` (the merge base of two commits and both trees),
``tree_entries`` (a recursive tree), ``blob_bytes`` (a file's content) and ``list_push_runs`` (the push runs of one
workflow on one commit of a branch); the context then also carries the pull request's base branch (``base_ref``). All
network access goes through ``GateClient.opener``, which tests replace with a fake. The token is sent only through
``scripts.ci_timing_gate.build_request`` (an unredirected header) and is never part of a URL or of the client's repr.

An answer of an unexpected shape raises (``ValueError``, ``KeyError`` or ``TypeError``) and nothing here catches a
network error: the gate turns every exception into a full run. Standard library only and Python 3.9 syntax, so the gate
runs on the runner's own ``python3``.
"""

from __future__ import annotations

import base64
import json
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from scripts.ci_timing_gate import API_ROOT, build_request, default_opener

COMPARE_FILE_CAP = 300  # the compare API lists at most 300 changed files, whatever the size of the diff
PAGE_SIZE = 100

Opener = Callable[[urllib.request.Request], bytes]


@dataclass(frozen=True)
class GateClient:
    """The repository, credentials and transport used for every API call; the opener is the test injection point."""

    repo: str
    token: str = field(repr=False)
    opener: Opener = default_opener

    def get_json(self, path: str) -> dict[str, object]:
        """Return the decoded JSON object of ``GET /repos/{repo}/{path}``; raise ``ValueError`` for any other JSON."""
        url = f"{API_ROOT}/repos/{self.repo}/{path}"
        answer = json.loads(self.opener(build_request(url, self.token)))
        if not isinstance(answer, dict):
            raise ValueError("the API answer is not a JSON object")
        return answer

    def get_list(self, path: str) -> list[object]:
        """Return the decoded JSON array of ``GET /repos/{repo}/{path}``; raise ``ValueError`` for any other JSON."""
        url = f"{API_ROOT}/repos/{self.repo}/{path}"
        answer = json.loads(self.opener(build_request(url, self.token)))
        if not isinstance(answer, list):
            raise ValueError("the API answer is not a JSON array")
        return answer


@dataclass(frozen=True)
class Comparison:
    """The compare answer the gate needs: the status, every changed path and whether the file list was cut short."""

    status: str
    paths: tuple[str, ...]
    truncated: bool


@dataclass(frozen=True)
class RunRef:
    """One earlier workflow run: its id, number, pull-request head and status, and the pull requests it names.

    ``pull_requests`` holds the number and base branch of each named pull request; the base is empty when the entry
    names none.
    """

    run_id: int
    run_number: int
    head_sha: str
    status: str
    pull_request_count: int
    pull_requests: tuple[tuple[int, str], ...] = ()


def _quote(value: str) -> str:
    """Quote ``value`` for one URL component; no character is safe, because branch names here contain ``/``."""
    return urllib.parse.quote(value, safe="")


def _file_paths(entry: object) -> list[str]:
    """Return the new name of a changed file and, for a rename, its old name."""
    if not isinstance(entry, dict) or not isinstance(entry.get("filename"), str):
        raise ValueError("a compare file entry has no text filename")
    previous = entry.get("previous_filename")
    if previous is None:
        return [entry["filename"]]
    if not isinstance(previous, str):
        raise ValueError("a compare file entry has a previous filename that is not text")
    return [entry["filename"], previous]


def compare(client: GateClient, base: str, head: str) -> Comparison:
    """Compare ``base...head``: the status is ``ahead`` only when ``head`` is a descendant of ``base``."""
    answer = client.get_json(f"compare/{_quote(base)}...{_quote(head)}")
    files = answer.get("files")
    if not isinstance(files, list):
        raise ValueError("the compare answer has no file list")
    paths = tuple(path for entry in files for path in _file_paths(entry))
    return Comparison(status=str(answer.get("status", "")), paths=paths, truncated=len(files) >= COMPARE_FILE_CAP)


def _run_pull(entry: object) -> tuple[int, str]:
    """Return the number and base branch of one pull request entry of a workflow run; a missing number raises."""
    if not isinstance(entry, dict):
        raise ValueError("a workflow run names a pull request that is not an object")
    base = entry.get("base")
    base_ref = base.get("ref") if isinstance(base, dict) else None
    return int(entry["number"]), base_ref if isinstance(base_ref, str) else ""


def _run_ref(run: object) -> RunRef:
    """Build a ``RunRef`` from one ``workflow_runs`` entry; a missing field raises."""
    if not isinstance(run, dict):
        raise ValueError("a workflow run entry is not an object")
    # The open pull requests whose head is the run's branch; the API answers null or an empty list when it knows none.
    pull_requests = run.get("pull_requests") or []
    if not isinstance(pull_requests, list):
        raise ValueError("a workflow run entry has a pull request list that is not a list")
    return RunRef(
        run_id=int(run["id"]),
        run_number=int(run["run_number"]),
        head_sha=str(run["head_sha"]),
        status=str(run.get("status", "")),
        pull_request_count=len(pull_requests),
        pull_requests=tuple(_run_pull(entry) for entry in pull_requests),
    )


def list_earlier_runs(client: GateClient, workflow_file: str, branch: str, before_run_number: int) -> list[RunRef]:
    """Return the pull-request runs of ``workflow_file`` on ``branch`` below ``before_run_number``, highest first.

    One page of 100 runs is read; the gate follows at most 20 of them.
    """
    query = f"event=pull_request&branch={_quote(branch)}&per_page={PAGE_SIZE}"
    answer = client.get_json(f"actions/workflows/{_quote(workflow_file)}/runs?{query}")
    runs = answer.get("workflow_runs")
    if not isinstance(runs, list):
        raise ValueError("the runs answer has no run list")
    earlier = [ref for ref in map(_run_ref, runs) if ref.run_number < before_run_number]
    return sorted(earlier, key=lambda ref: ref.run_number, reverse=True)


def _runs_on(client: GateClient, workflow_file: str, query: str, head_sha: str) -> list[RunRef]:
    """Return the runs of ``workflow_file`` that ``query`` finds on commit ``head_sha``, highest run number first."""
    answer = client.get_json(f"actions/workflows/{_quote(workflow_file)}/runs?{query}")
    runs = answer.get("workflow_runs")
    if not isinstance(runs, list):
        raise ValueError("the runs answer has no run list")
    on_head = [ref for ref in map(_run_ref, runs) if ref.head_sha == head_sha]
    return sorted(on_head, key=lambda ref: ref.run_number, reverse=True)


def list_head_runs(client: GateClient, workflow_file: str, head_sha: str) -> list[RunRef]:
    """Return the pull-request runs of ``workflow_file`` on the commit ``head_sha``, highest run number first."""
    query = f"event=pull_request&head_sha={_quote(head_sha)}&per_page={PAGE_SIZE}"
    return _runs_on(client, workflow_file, query, head_sha)


def list_push_runs(client: GateClient, workflow_file: str, branch: str, head_sha: str) -> list[RunRef]:
    """Return the push runs of ``workflow_file`` on ``branch`` for the commit ``head_sha``, highest run number first."""
    query = f"event=push&branch={_quote(branch)}&head_sha={_quote(head_sha)}&per_page={PAGE_SIZE}"
    return _runs_on(client, workflow_file, query, head_sha)


def list_jobs(client: GateClient, run_id: int) -> list[dict[str, object]]:
    """Return the jobs of run ``run_id`` with their steps: the latest attempt of each job, the API default."""
    answer = client.get_json(f"actions/runs/{run_id}/jobs?per_page={PAGE_SIZE}")
    jobs = answer.get("jobs")
    if not isinstance(jobs, list) or not all(isinstance(job, dict) for job in jobs):
        raise ValueError("the jobs answer has no job list")
    return jobs


@dataclass(frozen=True)
class PullRef:
    """One pull request associated with a commit: its number, whether it was merged, its base, head and merge commit."""

    number: int
    merged: bool
    base_ref: str
    head_sha: str
    merge_commit_sha: str


def _pull_ref(pull: object) -> PullRef:
    """Build a ``PullRef`` from one pull request entry; a missing or mistyped field raises."""
    if not isinstance(pull, dict) or not isinstance(pull["base"], dict) or not isinstance(pull["head"], dict):
        raise ValueError("a pull request entry is not an object with a base and a head")
    merged_at = pull.get("merged_at")
    merge_commit = pull.get("merge_commit_sha")
    return PullRef(
        number=int(pull["number"]),
        merged=isinstance(merged_at, str) and bool(merged_at),
        base_ref=str(pull["base"]["ref"]),
        head_sha=str(pull["head"]["sha"]),
        merge_commit_sha=merge_commit if isinstance(merge_commit, str) else "",
    )


def pulls_for_commit(client: GateClient, sha: str) -> list[PullRef]:
    """Return the pull requests associated with commit ``sha``: for a merge commit, the merged pull request."""
    return [_pull_ref(pull) for pull in client.get_list(f"commits/{_quote(sha)}/pulls?per_page={PAGE_SIZE}")]


def commit_tree(client: GateClient, sha: str) -> str:
    """Return the tree id of commit ``sha``; raise ``ValueError`` when the answer names none."""
    tree = client.get_json(f"git/commits/{_quote(sha)}").get("tree")
    tree_sha = tree.get("sha") if isinstance(tree, dict) else None
    if not isinstance(tree_sha, str) or not tree_sha:
        raise ValueError("the commit answer has no tree")
    return tree_sha


def _sha_of(entry: object) -> str:
    """Return the ``sha`` of an object answer; raise ``ValueError`` when it names none."""
    sha = entry.get("sha") if isinstance(entry, dict) else None
    if not isinstance(sha, str) or not sha:
        raise ValueError("an answer entry has no sha")
    return sha


@dataclass(frozen=True)
class CommitRef:
    """One commit: its id, its tree and its parents, first parent first."""

    sha: str
    tree_sha: str
    parents: tuple[str, ...]


def commit_ref(client: GateClient, sha: str) -> CommitRef:
    """Return the tree and the parents of commit ``sha``; raise ``ValueError`` when either is missing."""
    answer = client.get_json(f"git/commits/{_quote(sha)}")
    parents = answer.get("parents")
    if not isinstance(parents, list):
        raise ValueError("the commit answer has no parent list")
    return CommitRef(sha, _sha_of(answer.get("tree")), tuple(map(_sha_of, parents)))


@dataclass(frozen=True)
class MergeBase:
    """The merge base of a compare: its id and tree, and the tree of the compare's base commit."""

    sha: str
    tree_sha: str
    base_tree_sha: str


def _compare_commit_tree(entry: object) -> str:
    """Return the tree id of a commit entry of a compare answer (``commit.tree.sha``)."""
    commit = entry.get("commit") if isinstance(entry, dict) else None
    return _sha_of(commit.get("tree") if isinstance(commit, dict) else None)


def merge_base(client: GateClient, base: str, head: str) -> MergeBase | None:
    """Return the merge base of ``base...head`` with the trees of it and of ``base``; ``None`` when there is none."""
    answer = client.get_json(f"compare/{_quote(base)}...{_quote(head)}")
    found = answer.get("merge_base_commit")
    if found is None:
        return None
    return MergeBase(_sha_of(found), _compare_commit_tree(found), _compare_commit_tree(answer.get("base_commit")))


@dataclass(frozen=True)
class Tree:
    """A recursive tree: each file or submodule path mapped to ``(mode, type, sha)``, and whether it was cut short."""

    entries: dict[str, tuple[str, str, str]]
    truncated: bool


def _tree_entry(item: object) -> tuple[str, str, str, str]:
    """Return ``(path, mode, type, sha)`` of one recursive tree entry; raise ``ValueError`` for a mistyped entry."""
    fields = [item.get(key) for key in ("path", "mode", "type", "sha")] if isinstance(item, dict) else []
    texts = [value for value in fields if isinstance(value, str) and value]
    if len(texts) != 4:
        raise ValueError("a tree entry is not an object with a path, mode, type and sha")
    return texts[0], texts[1], texts[2], texts[3]


def tree_entries(client: GateClient, tree_sha: str) -> Tree:
    """Return the recursive tree ``tree_sha`` without its directory entries; a missing ``truncated`` counts as cut."""
    answer = client.get_json(f"git/trees/{_quote(tree_sha)}?recursive=1")
    items = answer.get("tree")
    if not isinstance(items, list):
        raise ValueError("the tree answer has no entry list")
    entries = {path: (mode, kind, sha) for path, mode, kind, sha in map(_tree_entry, items) if kind != "tree"}
    return Tree(entries, answer.get("truncated") is not False)


def blob_bytes(client: GateClient, sha: str) -> bytes:
    """Return the content of blob ``sha``; only strict base64 is accepted."""
    answer = client.get_json(f"git/blobs/{_quote(sha)}")
    content = answer.get("content")
    if answer.get("encoding") != "base64" or not isinstance(content, str):
        raise ValueError("the blob answer has no base64 content")
    return base64.b64decode(content.replace("\n", ""), validate=True)


@dataclass(frozen=True)
class GateContext:
    """What the event and the environment say about this run; a missing value is empty text or 0."""

    event_name: str
    action: str
    label: str
    before: str
    head_sha: str
    branch: str
    run_number: int
    run_attempt: int
    workflow_file: str
    job_name: str
    os_name: str
    sha: str = ""
    ref: str = ""
    base_ref: str = ""


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _number(value: object) -> int:
    text = _text(value)
    return int(text) if text.isascii() and text.isdigit() else 0


def _child(mapping: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = mapping.get(key)
    return value if isinstance(value, dict) else {}


def load_event(path: str) -> dict[str, object]:
    """Read the event payload; a missing or broken file is an empty payload."""
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def context_from(environ: Mapping[str, str], payload: Mapping[str, object], job_name: str, os_name: str) -> GateContext:
    """Fill the context from the ``GITHUB_*`` environment, the event payload and the job's own names."""
    workflow_file = _text(environ.get("GITHUB_WORKFLOW_REF")).partition("@")[0].rpartition("/")[2]
    pull_request = _child(payload, "pull_request")
    head = _child(pull_request, "head")
    return GateContext(
        event_name=_text(environ.get("GITHUB_EVENT_NAME")),
        action=_text(payload.get("action")),
        label=_text(_child(payload, "label").get("name")),
        before=_text(payload.get("before")),
        head_sha=_text(head.get("sha")),
        branch=_text(head.get("ref")),
        run_number=_number(environ.get("GITHUB_RUN_NUMBER")),
        run_attempt=_number(environ.get("GITHUB_RUN_ATTEMPT")),
        workflow_file=workflow_file if workflow_file.endswith((".yml", ".yaml")) else "",
        job_name=job_name,
        os_name=os_name,
        sha=_text(environ.get("GITHUB_SHA")),
        ref=_text(environ.get("GITHUB_REF")),
        base_ref=_text(_child(pull_request, "base").get("ref")),
    )
