from __future__ import annotations

import os

from sandbox_executor.entrypoint.taken_ids import taken_plan_ids
from sandbox_executor.git import GitRepository
from sandbox_executor.ledger import build_plan_id, read_merged_rows
from sandbox_executor.naming import parse_stage_id
from sandbox_executor.redaction import redact_agent_secrets

PLAN_ID_CLAIM_ROOT = "refs/holon/plan-ids/"


class PlanIdClaimError(ValueError):
    pass


def _is_plan_id(segment: str) -> bool:
    parsed = parse_stage_id(segment) if segment.startswith("P-") else None
    return parsed is not None and parsed.kind == "P"


def _ledger_plan_ids(repo_dir: str) -> set[str]:
    rows = read_merged_rows(os.path.join(repo_dir, "holon-knowledge/ledger/plans.jsonl"))
    return {row["plan_id"] for row in rows if isinstance(row.get("plan_id"), str)}


def _ref_plan_ids(repo: GitRepository) -> set[str]:
    roots = ["refs/heads/", "refs/remotes/origin/", PLAN_ID_CLAIM_ROOT]
    res = repo.run(["for-each-ref", "--format=%(refname)", *roots], check=False)
    if res.returncode != 0:
        return set()
    return {segment for ref in res.stdout.splitlines() for segment in ref.strip().split("/") if _is_plan_id(segment)}


def _plan_file_ids(repo_dir: str) -> set[str]:
    taken: set[str] = set()
    for _root, _dirs, names in os.walk(os.path.join(repo_dir, "plans")):
        for name in names:
            if name.startswith("P-") and name.endswith(".md") and not name.endswith("_calibration.md"):
                taken.add(name.removesuffix(".md"))
    return taken


def repository_plan_ids(repo: GitRepository, repo_dir: str) -> set[str]:
    return _ledger_plan_ids(repo_dir) | _ref_plan_ids(repo) | _plan_file_ids(repo_dir)


def _claim(repo: GitRepository, plan_id: str) -> bool:
    head = repo.run(["rev-parse", "HEAD"], check=False)
    if head.returncode != 0:
        raise PlanIdClaimError(f"cannot claim plan id '{plan_id}': HEAD does not resolve to a commit")
    claim_ref = PLAN_ID_CLAIM_ROOT + plan_id
    res = repo.run(["update-ref", claim_ref, head.stdout.strip(), ""], check=False)
    if res.returncode == 0:
        return True
    if repo.run(["show-ref", "--verify", "--quiet", claim_ref], check=False).returncode == 0:
        return False
    raise PlanIdClaimError(f"cannot claim plan id '{plan_id}': {redact_agent_secrets(res.stderr.strip())}")


def mint_plan_id(
    repo: GitRepository, repo_dir: str, intent_branch_prefix: str, plan_seq: int, agent_model: tuple[str, str]
) -> str:
    agent, model = agent_model
    taken = taken_plan_ids(repo, repo_dir, intent_branch_prefix) | repository_plan_ids(repo, repo_dir)
    while True:
        candidate = build_plan_id(plan_seq, intent_branch_prefix, agent, model, taken=taken)
        if _claim(repo, candidate):
            return candidate
        taken.add(candidate)
