from __future__ import annotations

import json
import os

from sandbox_executor.git import GitRepository
from sandbox_executor.plan_paths import plan_file_rel


def taken_child_ids(repo: GitRepository, parent_prefix: str, kind: str) -> set[str]:
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


def _plan_ids_on_disk(repo_dir: str, prefix: str) -> set[str]:
    plan_dir = os.path.dirname(os.path.join(repo_dir, plan_file_rel(prefix, "P-0")))
    on_disk = os.listdir(plan_dir) if os.path.isdir(plan_dir) else []
    return {name.removesuffix(".md") for name in on_disk if name.startswith("P-") and name.endswith(".md")}


def _plan_ids_in_ledger(repo_dir: str, prefix: str) -> set[str]:
    ledger_path = os.path.join(repo_dir, "holon-knowledge/ledger/plans.jsonl")
    if not os.path.exists(ledger_path):
        return set()
    taken: set[str] = set()
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


def taken_plan_ids(repo: GitRepository, repo_dir: str, intent_branch_prefix: str) -> set[str]:
    prefix = intent_branch_prefix.removesuffix("/_").rstrip("/")
    return (
        _plan_ids_on_disk(repo_dir, prefix) | taken_child_ids(repo, prefix, "P") | _plan_ids_in_ledger(repo_dir, prefix)
    )
