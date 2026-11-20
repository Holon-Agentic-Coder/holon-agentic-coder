"""Child ids already taken under a parent branch, so a same-second twin derives a distinct variant name."""

from __future__ import annotations

import json
import os

from sandbox_executor.git import GitRepository
from sandbox_executor.plan_paths import plan_file_rel


def taken_child_ids(repo: GitRepository, parent_prefix: str, kind: str) -> set[str]:
    """Ids of the ``{kind}-*`` children whose branches exist under ``parent_prefix``, locally or on origin.

    A trailing-slash pattern is a literal prefix match, so it covers the ``.../{child}/_`` refs (and any deeper
    descendants) that a ``{kind}-*`` glob never matches, since a glob's ``*`` stops at a slash.
    """
    parent = parent_prefix.removesuffix("/_").rstrip("/")
    taken: set[str] = set()
    for root in (f"refs/heads/{parent}/", f"refs/remotes/origin/{parent}/"):
        res = repo.run(["for-each-ref", "--format=%(refname)", root], check=False)
        if res.returncode != 0:
            continue
        for ref in res.stdout.splitlines():
            child = ref.strip().removeprefix(root).split("/", 1)[0]
            if child.startswith(f"{kind}-"):
                taken.add(child)
    return taken


def taken_plan_ids(repo: GitRepository, repo_dir: str, intent_branch_prefix: str) -> set[str]:
    """Plan ids taken under the intent: sibling plan branches, this branch's ledger rows and plan files on disk."""
    prefix = intent_branch_prefix.removesuffix("/_").rstrip("/")
    # A plan file already on disk under this intent is taken too, so a new plan never overwrites one.
    plan_dir = os.path.dirname(os.path.join(repo_dir, plan_file_rel(prefix, "P-0")))
    on_disk = os.listdir(plan_dir) if os.path.isdir(plan_dir) else []
    taken = {name.removesuffix(".md") for name in on_disk if name.startswith("P-") and name.endswith(".md")}
    taken |= taken_child_ids(repo, prefix, "P")
    ledger_path = os.path.join(repo_dir, "holon-knowledge/ledger/plans.jsonl")
    if not os.path.exists(ledger_path):
        return taken
    with open(ledger_path, encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line) if line.strip() else {}
            except json.JSONDecodeError:
                continue
            recorded = str(row.get("intent_branch") or "").removesuffix("/_").rstrip("/")
            if recorded == prefix and isinstance(row.get("plan_id"), str):
                taken.add(row["plan_id"])
    return taken
