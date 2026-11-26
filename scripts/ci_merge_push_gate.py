"""The merge-push route of the bookkeeping gate: skip a job when a push only merged the tested base branch in cleanly.

A "Merge remote-tracking branch origin/main" push changes, against the previous head, every file ``main`` changed since
the branch point, so the gate's diff rule blocks it and hands the ``synchronize`` push to ``decide_merge_push``. That
answers ``None`` (keep the gate's decision) when the new head is not a merge commit or its parents cannot be read, else
a ``Decision``; rule 1 answers before any request, so it decides a non-merge push of the hygiene job too. Only
positive evidence skips; the rules run cheapest first, and the first that fails decides "run":

1. ``-hygiene-never-skipped``: never for job ``hygiene`` or workflow ``test-hygiene.yml`` (no request).
2. ``-not-two-parents``, ``-first-parent-not-previous-head``, ``-incomplete-event``: two parents, the first is the
   previous head, and the event names the base branch.
3. The chain rules and the gate's diff rule: the job really succeeded on the previous head.
4. ``-base-run-missing``, ``-not-finished``, ``-not-successful``, ``-unverified``: the newest base-branch push run of
   the workflow on the merged-in commit (second parent) holding this job shows a real success.
5. ``-no-merge-base``, ``-tree-truncated``: a merge base, and complete recursive trees of it, both parents and the head.
6. ``-conflict-resolution``: every path of the new head holds the file-level three-way merge; a path both sides changed
   differently never counts as clean.
7. ``-overlapping-change``: the two sides' changed paths since the merge base share no path and no parent directory.
   Bookkeeping paths are left out of 7 and 8: every flow pull request adds siblings in the same ``executions/``,
   ``plans/`` and ledger directories and they cannot change a test result; rule 6 and the hygiene job still cover them.
8. ``-unanalysable-change``, ``-scan-too-large``, ``-related-change``: when both sides changed a file under the roots
   of ``RELATED_ROOTS`` (one pool, since tests under ``apps/`` import ``scripts.*``), every such file is a regular
   ``.py`` file (not ``conftest.py``) that parses with the running grammar, and no file of one side imports a module of
   the other (a dotted prefix counts) or is its ``test_<stem>.py``, in both directions. Content comes from the local
   checkout when its blob id matches, else from ``MAX_BLOB_READS`` reads.

Every rule name starts with ``merge-push``; the one that skips is ``merge-push-clean-main-merge`` (about 11 requests
and the blob reads of rule 8). After the merge is confirmed, any exception is ``merge-push-api-error`` with its class
name and HTTP status. No job ran the merged tree, so this skip is never evidence for a later ``main`` push:
``scripts.ci_main_push_gate`` runs a gate-skipped merge head in full (``main-push-head-skip-unproven``) unless only
bookkeeping changed since a real success. A coupling other than a direct import or a test name (a transitive import, a
data file, a fixture) is not seen; the daily full runs and the flow's final-head CI check remain the backstop. Standard
library only and Python 3.9 syntax, so it runs on the runner's own ``python3``.
"""

from __future__ import annotations

import ast
import hashlib
import os
import posixpath
import urllib.error
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from scripts.ci_bookkeeping_api import (
    CommitRef,
    GateClient,
    GateContext,
    blob_bytes,
    commit_ref,
    list_jobs,
    list_push_runs,
    merge_base,
    tree_entries,
)
from scripts.ci_bookkeeping_chain import (
    FULL_RUN_STEP,
    NOT_SUCCESSFUL,
    REAL_SUCCESS,
    Decision,
    JobQuery,
    classify_job,
    find_real_success,
    match_job,
)

HYGIENE_JOB = "hygiene"
HYGIENE_WORKFLOW = "test-hygiene.yml"
RELATED_ROOTS = ("apps", "scripts")
MAX_BLOB_READS = 40
MAX_BASE_RUNS = 5
SYMLINK_MODE = "120000"

RULE_HYGIENE_NEVER_SKIPPED = "merge-push-hygiene-never-skipped"
RULE_NOT_TWO_PARENTS = "merge-push-not-two-parents"
RULE_FIRST_PARENT_NOT_PREVIOUS_HEAD = "merge-push-first-parent-not-previous-head"
RULE_INCOMPLETE_EVENT = "merge-push-incomplete-event"
RULE_BASE_RUN_MISSING = "merge-push-base-run-missing"
RULE_BASE_RUN_NOT_FINISHED = "merge-push-base-run-not-finished"
RULE_BASE_RUN_NOT_SUCCESSFUL = "merge-push-base-run-not-successful"
RULE_BASE_RUN_UNVERIFIED = "merge-push-base-run-unverified"
RULE_NO_MERGE_BASE = "merge-push-no-merge-base"
RULE_TREE_TRUNCATED = "merge-push-tree-truncated"
RULE_CONFLICT_RESOLUTION = "merge-push-conflict-resolution"
RULE_OVERLAPPING_CHANGE = "merge-push-overlapping-change"
RULE_UNANALYSABLE_CHANGE = "merge-push-unanalysable-change"
RULE_SCAN_TOO_LARGE = "merge-push-scan-too-large"
RULE_RELATED_CHANGE = "merge-push-related-change"
RULE_API_ERROR = "merge-push-api-error"
RULE_CLEAN_MAIN_MERGE = "merge-push-clean-main-merge"
MERGE_PUSH_RUN_RULES = (
    RULE_HYGIENE_NEVER_SKIPPED,
    RULE_NOT_TWO_PARENTS,
    RULE_FIRST_PARENT_NOT_PREVIOUS_HEAD,
    RULE_INCOMPLETE_EVENT,
    RULE_BASE_RUN_MISSING,
    RULE_BASE_RUN_NOT_FINISHED,
    RULE_BASE_RUN_NOT_SUCCESSFUL,
    RULE_BASE_RUN_UNVERIFIED,
    RULE_NO_MERGE_BASE,
    RULE_TREE_TRUNCATED,
    RULE_CONFLICT_RESOLUTION,
    RULE_OVERLAPPING_CHANGE,
    RULE_UNANALYSABLE_CHANGE,
    RULE_SCAN_TOO_LARGE,
    RULE_RELATED_CHANGE,
    RULE_API_ERROR,
)
MERGE_PUSH_SKIP_RULES = (RULE_CLEAN_MAIN_MERGE,)

# A tree entry is ``(mode, type, sha)``; an absent path is ``None``. ``CONFLICT`` never equals a real entry.
Entry = tuple[str, str, str]
CONFLICT = ("", "conflict", "")


@dataclass(frozen=True)
class GateRules:
    """The gate's own rules this route reuses; passed in, so that this module never imports the gate."""

    diff_rule: Callable[[GateClient, str, str], tuple[Decision | None, int]]
    is_bookkeeping_path: Callable[[str], bool]


@dataclass(frozen=True)
class MergeTrees:
    """The files of the merge base, the previous head, the merged-in base commit and the new head."""

    base: Mapping[str, Entry]
    previous: Mapping[str, Entry]
    merged_in: Mapping[str, Entry]
    merged: Mapping[str, Entry]


def expected_entry(base: Entry | None, previous: Entry | None, merged_in: Entry | None) -> Entry | None:
    """Return the file-level three-way merge of one path, or ``CONFLICT`` when both sides changed it differently."""
    if previous == base:
        return merged_in
    if merged_in in (base, previous):
        return previous
    return CONFLICT


def conflict_path(trees: MergeTrees) -> str | None:
    """Return the first path whose merged entry is a conflict or differs from the new head's, else ``None``."""
    paths = set(trees.base) | set(trees.previous) | set(trees.merged_in) | set(trees.merged)
    for path in sorted(paths):
        expected = expected_entry(trees.base.get(path), trees.previous.get(path), trees.merged_in.get(path))
        if expected == CONFLICT or expected != trees.merged.get(path):
            return path
    return None


def changed_paths(old: Mapping[str, object], new: Mapping[str, object]) -> list[str]:
    """Return the sorted paths whose entry differs between ``old`` and ``new``, an added or removed path included."""
    return sorted(path for path in set(old) | set(new) if old.get(path) != new.get(path))


def overlap(pr_paths: Iterable[str], base_paths: Iterable[str]) -> str | None:
    """Return the first path both sides changed, else the first parent directory both share (``./`` for the root)."""
    pr_set, base_set = set(pr_paths), set(base_paths)
    shared = sorted(pr_set & base_set)
    if shared:
        return shared[0]
    directories = sorted({posixpath.dirname(path) for path in pr_set} & {posixpath.dirname(path) for path in base_set})
    return f"{directories[0] or '.'}/" if directories else None


def module_names(path: str) -> tuple[str, ...]:
    """Return the names ``path`` imports as: after ``src``, from ``scripts`` or ``tests``, bare; ``__init__`` too."""
    parts = path[: -len(".py")].split("/") if path.endswith(".py") else path.split("/")
    if parts[-1:] == ["__init__"]:
        parts = parts[:-1]
    candidates = [parts[-1:]]
    for anchor, offset in (("src", 1), ("tests", 0)):
        if anchor in parts:
            candidates.append(parts[len(parts) - parts[::-1].index(anchor) - 1 + offset :])
    if parts[:1] == ["scripts"]:
        candidates.append(parts)
    return tuple(sorted({".".join(candidate) for candidate in candidates if candidate}))


def _from_modules(node: ast.ImportFrom, packages: Sequence[str]) -> list[str]:
    """Return the absolute module names of a ``from`` import; a relative one resolves against each package."""
    if node.level == 0:
        return [node.module or ""]
    resolved = []
    for package in packages:
        parts = package.split(".") if package else []
        if node.level - 1 <= len(parts):
            prefix = parts[: len(parts) - (node.level - 1)]
            resolved.append(".".join([*prefix, node.module] if node.module else prefix))
    return resolved


def imports_of(path: str, source: bytes) -> frozenset[str] | None:
    """Return the dotted names a file imports (``from a import b`` gives ``a`` and ``a.b``); ``None`` if unparsable."""
    try:
        syntax = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    init = path.endswith("/__init__.py")
    packages = [name if init else name.rpartition(".")[0] for name in module_names(path)]
    names: set[str] = set()
    for node in ast.walk(syntax):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            for module in _from_modules(node, packages):
                names.add(module)
                names.update(f"{module}.{alias.name}" for alias in node.names)
    return frozenset(name.strip(".") for name in names if name.strip("."))


def _imports_any(imports: Iterable[str], names: Sequence[str]) -> bool:
    """Return true when an import equals a module name or one is a dotted prefix of the other."""
    return any(i == n or n.startswith(f"{i}.") or i.startswith(f"{n}.") for i in imports for n in names)


def is_test_pair(first: str, second: str) -> bool:
    """Return true when one file is ``test_<stem>.py`` and the other ``<stem>.py``."""
    first_name, second_name = posixpath.basename(first), posixpath.basename(second)
    return first_name == f"test_{second_name}" or second_name == f"test_{first_name}"


def related_pair(
    pr: Sequence[str], base: Sequence[str], imports: Mapping[str, frozenset[str]]
) -> tuple[str, str] | None:
    """Return the first pull-request and base-side file that import or test each other, in either direction."""
    for pr_path in pr:
        for base_path in base:
            if (
                is_test_pair(pr_path, base_path)
                or _imports_any(imports[pr_path], module_names(base_path))
                or _imports_any(imports[base_path], module_names(pr_path))
            ):
                return pr_path, base_path
    return None


@dataclass
class BlobReader:
    """Read content from the local checkout when its blob id matches, else from at most ``MAX_BLOB_READS`` reads."""

    client: GateClient
    reads: int = 0

    def source(self, path: str, sha: str) -> bytes | None:
        """Return the content of blob ``sha`` at ``path``; ``None`` once the blob-read budget is spent."""
        if os.path.isfile(path):
            with open(path, "rb") as handle:
                data = handle.read()
            if hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() == sha:
                return data
        if self.reads >= MAX_BLOB_READS:
            return None
        self.reads += 1
        return blob_bytes(self.client, sha)


def _is_analysable(path: str, entry: Entry | None) -> bool:
    """Return true for a regular ``.py`` blob other than ``conftest.py`` (fixtures are used by name, not imported)."""
    regular = entry is not None and entry[1] == "blob" and entry[0] != SYMLINK_MODE
    return regular and path.endswith(".py") and posixpath.basename(path) != "conftest.py"


def _relation_check(trees: MergeTrees, pr: list[str], base: list[str], client: GateClient) -> Decision | None:
    """Judge the changed files under ``RELATED_ROOTS`` when both sides changed one; ``None`` when none relates.

    The roots form one pool: a test under ``apps/`` imports ``scripts.*``, so a file is paired across roots too.
    """
    prefixes = tuple(f"{root}/" for root in RELATED_ROOTS)
    pr_paths = [path for path in pr if path.startswith(prefixes)]
    base_paths = [path for path in base if path.startswith(prefixes)]
    if not pr_paths or not base_paths:
        return None
    sides = ((pr_paths, trees.previous), (base_paths, trees.merged_in))
    entries = {p: trees.merged.get(p) or side.get(p) or trees.base.get(p) for paths, side in sides for p in paths}
    odd = next((path for path, entry in entries.items() if not _is_analysable(path, entry)), None)
    if odd is not None:
        return Decision(False, RULE_UNANALYSABLE_CHANGE, odd)
    reader = BlobReader(client)
    imports: dict[str, frozenset[str]] = {}
    for path, entry in entries.items():
        source = reader.source(path, entry[2]) if entry else None
        if source is None:
            return Decision(False, RULE_SCAN_TOO_LARGE, f"more than {MAX_BLOB_READS} blob reads")
        found = imports_of(path, source)
        if found is None:
            return Decision(False, RULE_UNANALYSABLE_CHANGE, f"{path} does not parse")
        imports[path] = found
    pair = related_pair(pr_paths, base_paths, imports)
    return Decision(False, RULE_RELATED_CHANGE, f"{pair[0]} and {pair[1]}") if pair else None


def _tree_rules(trees: MergeTrees, is_bookkeeping_path: Callable[[str], bool], client: GateClient) -> Decision | None:
    """Apply the clean-merge, overlap and relation rules; ``None`` when every one holds."""
    conflict = conflict_path(trees)
    if conflict is not None:
        return Decision(False, RULE_CONFLICT_RESOLUTION, conflict)
    pr_paths = [path for path in changed_paths(trees.base, trees.previous) if not is_bookkeeping_path(path)]
    base_paths = [path for path in changed_paths(trees.base, trees.merged_in) if not is_bookkeeping_path(path)]
    shared = overlap(pr_paths, base_paths)
    if shared is not None:
        return Decision(False, RULE_OVERLAPPING_CHANGE, shared)
    return _relation_check(trees, pr_paths, base_paths, client)


def _merge_parents(context: GateContext, client: GateClient) -> CommitRef | Decision | None:
    """Return the new head when it merges a second parent into the previous head, a "run" decision, or ``None``."""
    try:
        head = commit_ref(client, context.head_sha)
    except Exception:
        return None
    if len(head.parents) < 2:
        return None
    if len(head.parents) > 2:
        return Decision(False, RULE_NOT_TWO_PARENTS, f"{len(head.parents)} parents")
    if head.parents[0] != context.before:
        return Decision(False, RULE_FIRST_PARENT_NOT_PREVIOUS_HEAD, f"first parent {head.parents[0][:12]}")
    if not context.base_ref:
        return Decision(False, RULE_INCOMPLETE_EVENT, "no base branch")
    return head


def _previous_head_check(context: GateContext, client: GateClient, rules: GateRules) -> Decision | None:
    """Return the "run" decision unless the job really succeeded on the previous head, over bookkeeping-only pushes."""
    query = JobQuery(
        context.workflow_file, context.branch, context.job_name, context.os_name, context.run_number, context.before
    )
    chain = find_real_success(client, query)
    if chain.real_sha is None:
        return chain.run_decision()
    return rules.diff_rule(client, chain.real_sha, context.before)[0] if chain.real_sha != context.before else None


def _base_run_check(context: GateContext, client: GateClient, sha: str) -> Decision | None:
    """Return the "run" decision unless the newest base-branch push run on ``sha`` with this job really succeeded."""
    runs = list_push_runs(client, context.workflow_file, context.base_ref, sha)[:MAX_BASE_RUNS]
    jobs = (
        (run.run_number, match_job(list_jobs(client, run.run_id), context.job_name, context.os_name)) for run in runs
    )
    found = next(((number, job) for number, job in jobs if job is not None), None)
    if found is None:
        return Decision(False, RULE_BASE_RUN_MISSING, f"no single matching job on {sha[:12]}")
    where = f"run {found[0]} on {sha[:12]}"
    if found[1].get("status") != "completed":
        return Decision(False, RULE_BASE_RUN_NOT_FINISHED, f"{found[1].get('status') or 'unknown'} in {where}")
    kind = classify_job(found[1])
    if kind == NOT_SUCCESSFUL:
        return Decision(False, RULE_BASE_RUN_NOT_SUCCESSFUL, f"{found[1].get('conclusion') or 'unknown'} in {where}")
    if kind != REAL_SUCCESS:
        return Decision(False, RULE_BASE_RUN_UNVERIFIED, f"{kind}, no successful '{FULL_RUN_STEP}' in {where}")
    return None


def _trees(client: GateClient, head: CommitRef) -> MergeTrees | Decision:
    """Read the merge base of the two parents and the four recursive trees; a "run" decision when one is missing."""
    previous, merged_in = head.parents
    found = merge_base(client, previous, merged_in)
    if found is None:
        return Decision(False, RULE_NO_MERGE_BASE, f"{previous[:12]}...{merged_in[:12]}")
    entries = []
    for tree_sha in (found.tree_sha, found.base_tree_sha, commit_ref(client, merged_in).tree_sha, head.tree_sha):
        tree = tree_entries(client, tree_sha)
        if tree.truncated:
            return Decision(False, RULE_TREE_TRUNCATED, f"tree {tree_sha[:12]}")
        entries.append(tree.entries)
    return MergeTrees(*entries)


def _evidence(context: GateContext, client: GateClient, head: CommitRef, rules: GateRules) -> Decision:
    """Apply rules 3 to 8 in order; only the last statement returns "skip". Exceptions reach the caller."""
    merged_in = head.parents[1]
    blocked = _previous_head_check(context, client, rules) or _base_run_check(context, client, merged_in)
    if blocked is not None:
        return blocked
    trees = _trees(client, head)
    if isinstance(trees, Decision):
        return trees
    blocked = _tree_rules(trees, rules.is_bookkeeping_path, client)
    if blocked is not None:
        return blocked
    count = len(changed_paths(trees.base, trees.merged_in))
    proof = f"real successes on {context.before[:12]} and {merged_in[:12]}"
    return Decision(True, RULE_CLEAN_MAIN_MERGE, f"{count} base-side paths merged from {merged_in[:12]}; {proof}")


def decide_merge_push(context: GateContext, client: GateClient, rules: GateRules) -> Decision | None:
    """Decide a ``synchronize`` push that the diff rule blocked; ``None`` keeps that decision (not a merge commit)."""
    if context.job_name == HYGIENE_JOB or context.workflow_file == HYGIENE_WORKFLOW:
        return Decision(False, RULE_HYGIENE_NEVER_SKIPPED, context.job_name)
    head = _merge_parents(context, client)
    if not isinstance(head, CommitRef):
        return head
    try:
        return _evidence(context, client, head, rules)
    except Exception as error:
        status = f" {error.code}" if isinstance(error, urllib.error.HTTPError) else ""
        return Decision(False, RULE_API_ERROR, f"{type(error).__name__}{status}")
