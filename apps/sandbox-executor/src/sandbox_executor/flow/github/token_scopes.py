"""The GitHub token the flow hands to the sandbox: where it comes from, what kind it is and which OAuth scopes it has.

Pushing a commit that changes ``.github/workflows/**`` needs the ``workflow`` scope. A classic or ``gh`` OAuth token
lists its scopes in the ``X-OAuth-Scopes`` header of any API response, so one ``GET /user`` tells them apart. A
fine-grained or app token carries no scopes header (its permissions are per repository), so its scopes are reported
as unverifiable rather than missing, and a network or HTTP failure is unverifiable too.

The token value never leaves this module's functions: reports and messages carry only its type label and source.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.touch_set import TouchSetEntry, entry_matches, normalise_touch_path

WORKFLOW_SCOPE = "workflow"
WORKFLOW_DIR = ".github/workflows/"
USER_API_URL = "https://api.github.com/user"
REFRESH_COMMAND = "gh auth refresh -h github.com -s workflow"
LOGIN_COMMAND = "gh auth login -h github.com -s workflow"
DEFAULT_TIMEOUT = 10.0
_WORKFLOW_PROBES = (".github/workflows/ci.yml", ".github/workflows/ci.yaml")
_MAX_NAMED_PATHS = 5
_TOKEN_TYPES = (
    ("github_pat_", "fine-grained"),
    ("ghp_", "classic"),
    ("gho_", "oauth"),
    ("ghs_", "app"),
    ("ghu_", "user-to-server"),
)
_UNVERIFIABLE_TYPES = frozenset({"fine-grained", "app"})

#: Runs ``gh auth token``-style argv and returns the completed process.
CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]
#: GETs a URL with headers and a timeout; returns the HTTP status and the response headers.
ScopeFetcher = Callable[[str, Mapping[str, str], float], tuple[int, Mapping[str, str]]]


@dataclass(frozen=True)
class TokenScopeReport:
    """What is known about the token, never the token: ``scopes`` is None when they cannot be verified."""

    token_type: str
    source: str
    scopes: tuple[str, ...] | None
    detail: str = ""

    @property
    def verifiable(self) -> bool:
        return self.scopes is not None

    @property
    def has_workflow(self) -> bool:
        return self.scopes is not None and WORKFLOW_SCOPE in self.scopes

    def format_line(self) -> str:
        """The value-free report line, e.g. ``github_token=classic source=gh scopes=repo,workflow``."""
        if self.token_type == "none":
            return "github_token=none (set GITHUB_TOKEN or run gh auth login)"
        scopes = "unverifiable" if self.scopes is None else (",".join(self.scopes) or "none")
        return f"github_token={self.token_type} source={self.source} scopes={scopes}"


def _run_gh(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=DEFAULT_TIMEOUT, check=False)


def resolve_github_token(
    environ: Mapping[str, str] | None = None, run: CommandRunner | None = None
) -> tuple[str | None, str]:
    """The token and its source: ``GITHUB_TOKEN``, then ``GH_TOKEN``, then ``gh auth token``; ``(None, "none")``."""
    env = os.environ if environ is None else environ
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        if env.get(name):
            return env[name], name
    if run is None:
        if not shutil.which("gh"):
            return None, "none"
        run = _run_gh
    try:
        res = run(["gh", "auth", "token"])
    except (OSError, subprocess.SubprocessError):
        return None, "none"
    token = (res.stdout or "").strip() if res.returncode == 0 and isinstance(res.stdout, str) else ""
    return (token, "gh") if token else (None, "none")


def token_type(token: str) -> str:
    """The token's kind from its documented prefix only (``classic``, ``fine-grained``, ...), else ``unknown``."""
    return next((label for prefix, label in _TOKEN_TYPES if token.startswith(prefix)), "unknown")


def _urllib_fetch(url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, Mapping[str, str]]:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, dict(response.headers.items())


def fetch_scopes(
    token: str, fetch: ScopeFetcher | None = None, timeout: float = DEFAULT_TIMEOUT
) -> tuple[tuple[str, ...] | None, str]:
    """The token's ``X-OAuth-Scopes`` and a reason; None scopes (with the redacted reason) when they are unknown."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "holon"}
    try:
        status, response_headers = (fetch or _urllib_fetch)(USER_API_URL, headers, timeout)
    except Exception as exc:  # A failed request only makes the scopes unverifiable.
        reason = redact_agent_secrets(f"the scope request failed ({type(exc).__name__}: {exc})")
        return None, reason.replace(token, "***")[:200]
    if status != 200:
        return None, f"the scope request returned HTTP {status}"
    lowered = {key.lower(): value for key, value in response_headers.items()}
    if "x-oauth-scopes" not in lowered:
        return None, "the response carries no X-OAuth-Scopes header"
    return tuple(s.strip() for s in lowered["x-oauth-scopes"].split(",") if s.strip()), ""


def scope_report(
    environ: Mapping[str, str] | None = None, run: CommandRunner | None = None, fetch: ScopeFetcher | None = None
) -> TokenScopeReport:
    """Resolve the token and report its type, source and scopes (one request for a classic or OAuth token)."""
    token, source = resolve_github_token(environ, run)
    if token is None:
        return TokenScopeReport("none", source, None, "no GitHub token found")
    kind = token_type(token)
    if kind in _UNVERIFIABLE_TYPES:
        return TokenScopeReport(kind, source, None, f"{kind} tokens carry no scopes header")
    scopes, reason = fetch_scopes(token, fetch)
    return TokenScopeReport(kind, source, scopes, reason)


def touches_workflows(paths: Iterable[str]) -> list[str]:
    """The declared paths or globs that are, or may match, a file under ``.github/workflows/``."""
    found: list[str] = []
    for raw in paths:
        path = normalise_touch_path(raw)
        if not path:
            continue
        entry = TouchSetEntry(path)
        if path.startswith(WORKFLOW_DIR) or any(entry_matches(entry, probe) for probe in _WORKFLOW_PROBES):
            found.append(path)
    return list(dict.fromkeys(found))


def workflow_scope_error(report: TokenScopeReport, workflow_paths: list[str]) -> str | None:
    """Why the token cannot push the workflow paths, naming the fix; None when it can or cannot be verified."""
    if not workflow_paths:
        return None
    named = ", ".join(workflow_paths[:_MAX_NAMED_PATHS]) + (" ..." if len(workflow_paths) > _MAX_NAMED_PATHS else "")
    if report.token_type == "none":
        return f"the plan touches workflow files ({named}) but no GitHub token was found; run: {LOGIN_COMMAND}"
    if not report.verifiable or report.has_workflow:
        return None
    scopes = ",".join(report.scopes or ()) or "none"
    return (
        f"the plan touches workflow files ({named}) but the {report.token_type} GitHub token from {report.source} "
        f"lacks the '{WORKFLOW_SCOPE}' scope (scopes: {scopes}), so its push would be refused; run: "
        f"{REFRESH_COMMAND} (a token set in the environment must be regenerated with the '{WORKFLOW_SCOPE}' scope)"
    )
