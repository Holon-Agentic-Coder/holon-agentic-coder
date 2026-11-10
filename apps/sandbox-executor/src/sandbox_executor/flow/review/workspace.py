"""The Stage 4 review workspace sandbox guard for reviewer turns.

Role containers review the host worktree in place (bind-mounted by ``cli.run_docker_container``), so there is no
review workspace lifecycle to own here: only the refusal to run reviewer turns outside a sandbox.
"""

from __future__ import annotations

from collections.abc import Mapping

from sandbox_executor.agent_defaults import harness_detection_disabled

# Reviewer agent turns run only inside a sandbox (docs/safety.md, Invariant 2); host-side Stage 4 fails closed.
HOST_REVIEW_REFUSAL = (
    "Refusing to run reviewer agent turns on the host: Stage 4 reviewers run only inside the sandbox "
    "(use 'holon review-loop' without --local or --json, or run inside the review-loop container)"
)


def reviewers_sandboxed(environ: Mapping[str, str] | None = None) -> bool:
    """Return True when an explicit sandbox signal (``HOLON_IN_SANDBOX`` or ``HOLON_ROLE``) is set.

    Reuses ``agent_defaults.SANDBOX_GUARD_VARS``; the ``USER=holon`` heuristic is deliberately not trusted.
    """
    return harness_detection_disabled(environ)
