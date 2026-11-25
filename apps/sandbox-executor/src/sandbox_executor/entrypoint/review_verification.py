import subprocess
from collections.abc import Callable

from sandbox_executor.entrypoint.rejected_attempt import capture_rejected_attempt
from sandbox_executor.entrypoint.review_failure_detail import build_test_failure_message

MISSING_TOOL_EXIT_CODE = 127
MISSING_TOOL_MARKER = "was not found on PATH"


def missing_tool_result(args: list[str], exc: OSError) -> subprocess.CompletedProcess[str]:
    tool = args[0] if args else "<empty command>"
    if isinstance(exc, FileNotFoundError) and exc.filename in (None, tool):
        detail = f"required tool '{tool}' {MISSING_TOOL_MARKER}"
        if tool in ("uv", "uvx"):
            detail += "; it ships in the holon-base image (UV_VERSION): rebuild the agent image or install uv"
    else:
        detail = f"required tool '{tool}' could not be started ({type(exc).__name__}: {exc})"
    return subprocess.CompletedProcess(args, MISSING_TOOL_EXIT_CODE, stdout="", stderr=detail)


def _is_missing_tool_result(result: subprocess.CompletedProcess[str]) -> bool:
    return result.returncode == MISSING_TOOL_EXIT_CODE and MISSING_TOOL_MARKER in (result.stderr or "")


def _combine_stderr_before_stdout(result: subprocess.CompletedProcess[str], fallback: str) -> str:
    return "\n".join(part for part in (result.stderr, result.stdout) if part).strip() or fallback


def verification_error(
    step: str,
    result: subprocess.CompletedProcess[str],
    bound: Callable[[str], str],
    workspace: str | None = None,
    iteration: int | None = None,
) -> str:
    is_missing_tool = _is_missing_tool_result(result)
    if step == "test" and workspace and not is_missing_tool:
        raw = _combine_stderr_before_stdout(result, "test failed")
        if iteration is not None:
            capture_rejected_attempt(workspace, iteration, raw)
        return build_test_failure_message(result.returncode, raw, workspace)
    detail = bound((result.stderr or result.stdout or "").strip() or f"{step} failed")
    if is_missing_tool:
        return f"Resolver pre-push verification could not run: {detail}"
    return f"Resolver pre-push {step} failed (exit code {result.returncode}): {detail}"
