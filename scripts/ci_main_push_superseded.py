"""The superseded check of the push-to-``main`` route: skip a pushed commit that a newer tip of ``main`` contains.

When several pull requests merge back to back, every push to ``main`` starts the gated workflows, and each pushed tree
is a subset of the next one. A pushed commit that is a strict ancestor of the current tip of ``main`` when the gate job
starts relies on the tip's own push run, which tests a superset tree. ``decide_main_push`` asks this check right after
its event check, so a re-run attempt or an incomplete event never reaches it.

Only positive evidence skips: the compare ``pushed...tip`` must answer ``ahead``. The pushed commit being the tip,
any other compare status, and any exception (the tip cannot be read, the compare fails) return no opinion, and the
existing rules decide. Standard library only and Python 3.9 syntax, so it runs on the runner's own ``python3``.
"""

from __future__ import annotations

from scripts.ci_bookkeeping_api import GateClient, GateContext, compare
from scripts.ci_bookkeeping_chain import Decision

RULE_SUPERSEDED = "main-push-superseded"
SUPERSEDED_SKIP_RULES = (RULE_SUPERSEDED,)
MAIN_TIP_REF = "git/ref/heads/main"


def main_tip(client: GateClient) -> str:
    """Return the commit the ``main`` branch points at; raise ``ValueError`` when the answer names none."""
    target = client.get_json(MAIN_TIP_REF).get("object")
    sha = target.get("sha") if isinstance(target, dict) else None
    if not isinstance(sha, str) or not sha:
        raise ValueError("the ref answer names no commit")
    return sha


def superseded_check(context: GateContext, client: GateClient) -> Decision | None:
    """Return the "skip" decision when the pushed commit is a strict ancestor of the tip of ``main``, else ``None``.

    The tip read is the only read when the pushed commit is the tip; otherwise one compare follows.
    """
    try:
        tip = main_tip(client)
        if tip == context.sha:
            return None
        comparison = compare(client, context.sha, tip)
    except Exception:  # any doubt keeps the existing rules, which decide on their own reads
        return None
    if comparison.status != "ahead":
        return None
    return Decision(True, RULE_SUPERSEDED, f"pushed {context.sha[:12]} superseded by tip {tip[:12]}")
