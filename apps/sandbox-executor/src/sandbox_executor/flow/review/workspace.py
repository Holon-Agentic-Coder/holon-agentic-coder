"""The Stage 4 review workspace: the sandbox guard for reviewer turns and the persistent review session
volume.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Callable, Mapping

from sandbox_executor.agent_defaults import FALLBACK_AGENT, harness_detection_disabled, normalise_agent_id
from sandbox_executor.agent_runner import SANDBOX_WORKSPACE_MOUNT, SANDBOX_WORKSPACE_UID_GID
from sandbox_executor.redaction import redact_agent_secrets

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


# Label carried by every persistent Stage 4 review workspace volume, so a volume leaked by a crashed
# loop can be found and pruned: docker volume prune --filter label=holon.review-session.
REVIEW_SESSION_VOLUME_LABEL = "holon.review-session"


_REVIEW_PERSIST_DISABLED_VALUES = ("0", "false", "no", "off")


_DOCKER_CLI_TIMEOUT_SECONDS = 120


def review_persist_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Return True unless ``HOLON_REVIEW_PERSIST`` disables the persistent Stage 4 review workspace.

    The kill switch restores the historical clone-per-pass behaviour without a code revert: with
    ``HOLON_REVIEW_PERSIST`` set to ``0``, ``false``, ``no`` or ``off`` no session volume is created and
    the reviewer and resolver dispatch arguments are identical to the pre-persistence ones.
    """
    env = environ if environ is not None else os.environ
    return str(env.get("HOLON_REVIEW_PERSIST", "")).strip().lower() not in _REVIEW_PERSIST_DISABLED_VALUES


def review_session_volume_name(review_target: str) -> str:
    """Deterministic Docker volume name for one Stage 4 session on ``review_target``.

    Derived purely from the target so a resumed flow computes the same name without storing anything in
    the checkpoint; a stale survivor is safe to reuse because the entrypoint makes every reused checkout
    hermetically equivalent to a fresh clone before syncing.
    """
    digest = hashlib.sha256(review_target.encode("utf-8")).hexdigest()[:16]
    return f"holon-review-ws-{digest}"


class ReviewWorkspaceSession:
    """One persistent review workspace volume shared by all dispatches of one Stage 4 session.

    The labelled volume is created lazily on the first :meth:`volume_name` call (the first reviewer
    dispatch) and mounted at the container workspace path by every reviewer-ensemble and resolver
    dispatch of the session, so the first pass clones and every later pass fetches incrementally.
    A freshly created named volume is root-owned (the image does not pre-create the workspace path), so
    creation runs one root init container that chowns the mount to the image's unprivileged user — the
    same pattern as the seeded-remote test fixtures. Any creation failure disables the session with a
    warning and the dispatches fall back to the historical clone-per-pass behaviour; teardown failures
    are warnings too (the volume is prunable by its label), never a stage failure.
    """

    def __init__(
        self,
        review_target: str,
        *,
        agent: str | None = None,
        log: Callable[[str], None] | None = None,
        docker_runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        """Remember the session identity and seams; no Docker command runs until the first dispatch."""
        self._review_target = review_target
        self._agent = agent
        self._log = log or (lambda message: None)
        self._run = docker_runner or self._run_docker_cli
        self._volume: str | None = None
        self._disabled = False

    @staticmethod
    def _run_docker_cli(argv: list[str]) -> subprocess.CompletedProcess[str]:
        """Run one docker CLI command with captured output and a bounded timeout."""
        return subprocess.run(argv, capture_output=True, text=True, check=False, timeout=_DOCKER_CLI_TIMEOUT_SECONDS)

    def _init_image(self) -> str:
        """The image whose root user initialises volume ownership: the session's own review-loop image."""
        from sandbox_executor.cli import resolve_role_image

        return resolve_role_image("review-loop", normalise_agent_id(self._agent or FALLBACK_AGENT))

    def _checked_docker(self, argv: list[str], step: str) -> None:
        """Run one docker command, raising ``RuntimeError`` with its redacted stderr when it fails."""
        result = self._run(argv)
        if result.returncode != 0:
            stderr = (result.stderr or "").strip() or f"exit code {result.returncode}"
            raise RuntimeError(f"{step} failed: {redact_agent_secrets(stderr)}")

    def volume_name(self) -> str | None:
        """Create the session volume on first use and return its name; None when persistence is unavailable."""
        if self._volume is not None or self._disabled:
            return self._volume
        name = review_session_volume_name(self._review_target)
        try:
            self._checked_docker(
                ["docker", "volume", "create", "--label", f"{REVIEW_SESSION_VOLUME_LABEL}=1", name],
                "docker volume create",
            )
            try:
                # Hand the root-owned volume to the image's unprivileged user before any pass writes to it.
                self._checked_docker(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "--user",
                        "root",
                        "-v",
                        f"{name}:{SANDBOX_WORKSPACE_MOUNT}",
                        "--entrypoint",
                        "chown",
                        self._init_image(),
                        "-R",
                        SANDBOX_WORKSPACE_UID_GID,
                        SANDBOX_WORKSPACE_MOUNT,
                    ],
                    "review workspace ownership init",
                )
            except Exception:
                self._run(["docker", "volume", "rm", "-f", name])
                raise
        except Exception as exc:
            self._disabled = True
            self._log(
                "[Review Loop] Warning: persistent review workspace unavailable, falling back to a full "
                f"clone per pass: {redact_agent_secrets(str(exc))}"
            )
            return None
        self._volume = name
        self._log(f"[Review Loop] Persistent review workspace volume '{name}' initialised for this session.")
        return name

    def teardown(self) -> None:
        """Remove the session volume if one was created; failures warn and never fail the stage."""
        if self._volume is None:
            return
        name, self._volume = self._volume, None
        try:
            self._checked_docker(["docker", "volume", "rm", "-f", name], "docker volume rm")
        except Exception as exc:
            self._log(
                f"[Review Loop] Warning: could not remove review workspace volume '{name}' "
                f"(prunable via its {REVIEW_SESSION_VOLUME_LABEL} label): {redact_agent_secrets(str(exc))}"
            )
