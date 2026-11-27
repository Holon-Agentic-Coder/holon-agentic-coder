from __future__ import annotations

import re

BRANCH_TIP_SUFFIX = "/_"
EXECUTION_BRANCH_MARKER = "/E-"

EXECUTOR_GIT_ENV: dict[str, str] = {
    "GIT_AUTHOR_NAME": "Holon Executor Agent",
    "GIT_AUTHOR_EMAIL": "executor-agent@holon-agentic-coder.com",
    "GIT_COMMITTER_NAME": "Holon Executor Agent",
    "GIT_COMMITTER_EMAIL": "executor-agent@holon-agentic-coder.com",
}


def _matches_dispatch_suffix(branch: str, preferred_suffix: str) -> bool:
    bare = branch.removesuffix(BRANCH_TIP_SUFFIX)
    return bare.endswith(preferred_suffix) or re.sub(r"-\d+$", "", bare).endswith(preferred_suffix)


def _execution_parent_plan_branch(execution_branch: str) -> str | None:
    clean = execution_branch.strip()
    if EXECUTION_BRANCH_MARKER not in clean:
        return None
    return clean.split(EXECUTION_BRANCH_MARKER, 1)[0] + BRANCH_TIP_SUFFIX
