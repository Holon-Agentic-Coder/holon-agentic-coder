"""Pre-push verification failures of the containerised Stage 4 resolver.

A verification tool that cannot be started (``uv`` missing from the image, say) is reported as a structured failure
that names it, distinct from a lint or test failure, so the resolver records it and never commits or pushes.
"""

import subprocess
from collections.abc import Callable

from sandbox_executor.entrypoint.review_failure_detail import test_failure_message

# Exit code and stderr marker of a command whose executable could not be started (the shell's "command not found").
MISSING_TOOL_EXIT_CODE = 127
MISSING_TOOL_MARKER = "was not found on PATH"


def missing_tool_result(args: list[str], exc: OSError) -> subprocess.CompletedProcess[str]:
    """Describe a command that could not be started as a failed ``CompletedProcess`` naming the executable."""
    tool = args[0] if args else "<empty command>"
    if isinstance(exc, FileNotFoundError) and exc.filename in (None, tool):
        detail = f"required tool '{tool}' {MISSING_TOOL_MARKER}"
        if tool in ("uv", "uvx"):
            detail += "; it ships in the holon-base image (UV_VERSION): rebuild the agent image or install uv"
    else:
        detail = f"required tool '{tool}' could not be started ({type(exc).__name__}: {exc})"
    return subprocess.CompletedProcess(args, MISSING_TOOL_EXIT_CODE, stdout="", stderr=detail)


def verification_error(step: str, result: subprocess.CompletedProcess[str], bound: Callable[[str], str], workspace: str | None = None
) -> str:
    """Describe a failed pre-push ``step``, keeping a tool that could not run apart from a real lint or test failure.

    ``bound`` redacts and caps the command output; the caller supplies it so this module stays free of engine imports.
    A real test failure with a ``workspace`` names the failed tests and writes the full redacted output under ``.holon/``.
    """
    is_missing_tool = result.returncode == MISSING_TOOL_EXIT_CODE and MISSING_TOOL_MARKER in (result.stderr or "")
    if step == "test" and workspace and not is_missing_tool:
        raw = "\n".join(part for part in (result.stdout, result.stderr) if part).strip() or "test failed"
        return test_failure_message(result.returncode, raw, workspace)
    detail = bound((result.stderr or result.stdout or "").strip() or f"{step} failed")
    if is_missing_tool:
        return f"Resolver pre-push verification could not run: {detail}"
    return f"Resolver pre-push {step} failed (exit code {result.returncode}): {detail}"
