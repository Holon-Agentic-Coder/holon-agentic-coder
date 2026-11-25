from __future__ import annotations

import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from sandbox_executor.launcher.mounts import CommandRunner, resolve_github_token
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.touch_set import TouchSetEntry, entry_matches, normalise_touch_path

WORKFLOW_SCOPE = "workflow"
WORKFLOW_DIR = ".github/workflows/"
USER_API_URL = "https://api.github.com/user"
REFRESH_COMMAND = "gh auth refresh -h github.com -s workflow"
LOGIN_COMMAND = "gh auth login -h github.com -s workflow"
DEFAULT_TIMEOUT = 10.0

_GITHUB_ACCEPT_HEADER = "application/vnd.github+json"
_GITHUB_USER_AGENT = "holon"
_OAUTH_SCOPES_HEADER = "x-oauth-scopes"
_HTTP_OK = 200
_MAX_REASON_CHARS = 200
_WORKFLOW_PROBES = (".github/workflows/ci.yml", ".github/workflows/ci.yaml")
_MAX_NAMED_PATHS = 5
_TOKEN_TYPES = (
    ("github_pat_", "fine-grained"),
    ("ghp_", "classic"),
    ("gho_", "oauth"),
    ("ghs_", "app"),
    ("ghu_", "user-to-server"),
)
_UNVERIFIABLE_TYPES = frozenset({"fine-grained", "app", "user-to-server"})
_SCOPED_TYPES = frozenset({"classic", "oauth"})

ScopeFetcher = Callable[[str, Mapping[str, str], float], tuple[int, Mapping[str, str]]]


@dataclass(frozen=True)
class TokenScopeReport:
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
        if self.token_type == "none":
            return "github_token=none (set GITHUB_TOKEN or run gh auth login)"
        scopes = "unverifiable" if self.scopes is None else (",".join(self.scopes) or "none")
        return f"github_token={self.token_type} source={self.source} scopes={scopes}"


def token_type(token: str) -> str:
    return next((label for prefix, label in _TOKEN_TYPES if token.startswith(prefix)), "unknown")


def _urllib_fetch(url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, Mapping[str, str]]:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, dict(response.headers.items())


def _redact_scope_request_error(exc: Exception, token: str) -> str:
    reason = redact_agent_secrets(f"the scope request failed ({type(exc).__name__}: {exc})")
    return reason.replace(token, "***")[:_MAX_REASON_CHARS]


def _parse_oauth_scopes_header(header_value: str) -> tuple[str, ...]:
    return tuple(scope.strip() for scope in header_value.split(",") if scope.strip())


def fetch_scopes(
    token: str, fetch: ScopeFetcher | None = None, timeout: float = DEFAULT_TIMEOUT
) -> tuple[tuple[str, ...] | None, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": _GITHUB_ACCEPT_HEADER,
        "User-Agent": _GITHUB_USER_AGENT,
    }
    try:
        status, response_headers = (fetch or _urllib_fetch)(USER_API_URL, headers, timeout)
    except Exception as exc:
        return None, _redact_scope_request_error(exc, token)
    if status != _HTTP_OK:
        return None, f"the scope request returned HTTP {status}"
    lowered = {key.lower(): value for key, value in response_headers.items()}
    if _OAUTH_SCOPES_HEADER not in lowered:
        return None, "the response carries no X-OAuth-Scopes header"
    return _parse_oauth_scopes_header(lowered[_OAUTH_SCOPES_HEADER]), ""


def scope_report(
    environ: Mapping[str, str] | None = None, run: CommandRunner | None = None, fetch: ScopeFetcher | None = None
) -> TokenScopeReport:
    token, source = resolve_github_token(environ, run)
    if token is None:
        return TokenScopeReport("none", source, None, "no GitHub token found")
    kind = token_type(token)
    if kind in _UNVERIFIABLE_TYPES:
        return TokenScopeReport(kind, source, None, f"{kind} tokens carry no scopes header")
    scopes, reason = fetch_scopes(token, fetch)
    if scopes == () and kind not in _SCOPED_TYPES:
        return TokenScopeReport(kind, source, None, f"an empty scopes header from a {kind} token is not a scope list")
    return TokenScopeReport(kind, source, scopes, reason)


def touches_workflows(paths: Iterable[str]) -> list[str]:
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


def doctor_lines(report: TokenScopeReport | None = None) -> list[str]:
    report = scope_report() if report is None else report
    lines = [report.format_line()]
    if report.token_type == "none":
        return lines
    if report.verifiable and not report.has_workflow:
        lines.append(
            "WARNING: the GitHub token lacks the workflow scope; plans that touch .github/workflows/** will fail "
            f"before Stage 3 (run: {REFRESH_COMMAND})"
        )
    elif not report.verifiable:
        lines.append(
            f"WARNING: the GitHub token's workflow scope cannot be verified ({report.detail}); a fine-grained "
            "token needs Workflows read/write to push workflow files"
        )
    return lines
