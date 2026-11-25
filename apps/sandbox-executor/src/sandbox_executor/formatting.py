import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

logger = logging.getLogger(__name__)

PRETTIER_SPEC: str = "prettier@3.8.4"
DEFAULT_MAX_PASSES: int = 3
DEFAULT_TIMEOUT_SECONDS: int = 30


def is_prettier_available() -> bool:
    return shutil.which("npx") is not None


def _normalize_file_paths(files: Sequence[str | Path] | str | Path) -> list[str]:
    if isinstance(files, (str, Path)):
        return [str(files)]
    return [str(file_path) for file_path in files]


def _filter_existing_files(file_list: list[str], resolved_repo_dir: str) -> list[str]:
    existing_files: list[str] = []
    for file_path in file_list:
        disk_path = file_path if os.path.isabs(file_path) else os.path.join(resolved_repo_dir, file_path)
        if os.path.exists(disk_path):
            existing_files.append(file_path)
        else:
            logger.debug("Skipping nonexistent file for prettier formatting: %s", file_path)
    return existing_files


def _warn_and_print(message: str) -> None:
    logger.warning(message)
    print(message, file=sys.stderr)


def _run_prettier_pass(
    existing_files: list[str],
    resolved_repo_dir: str,
    pass_count: int,
    timeout_seconds: int,
) -> bool | None:
    write_cmd = ["npx", "--yes", PRETTIER_SPEC, "--write", *existing_files]
    write_res = subprocess.run(
        write_cmd,
        cwd=resolved_repo_dir,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    if write_res.returncode != 0:
        _warn_and_print(
            f"Warning: Prettier write pass {pass_count} failed with exit code {write_res.returncode}:\n"
            f"{write_res.stderr.strip()}"
        )
        return False

    check_cmd = ["npx", "--yes", PRETTIER_SPEC, "--check", *existing_files]
    check_res = subprocess.run(
        check_cmd,
        cwd=resolved_repo_dir,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    if check_res.returncode == 0:
        logger.info("Prettier converged in %d pass(es) for %d file(s).", pass_count, len(existing_files))
        return True
    return None


def _converge_files(
    existing_files: list[str],
    resolved_repo_dir: str,
    max_passes: int,
    timeout_seconds: int,
) -> bool:
    for pass_count in range(1, max_passes + 1):
        pass_result = _run_prettier_pass(existing_files, resolved_repo_dir, pass_count, timeout_seconds)
        if pass_result is not None:
            return pass_result
    _warn_and_print(f"Warning: Prettier failed to converge on {len(existing_files)} file(s) after {max_passes} passes.")
    return False


def _format_exception_message(exc: Exception, timeout_seconds: int) -> str:
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"Warning: Prettier command timed out after {timeout_seconds} seconds: {exc}"
    if isinstance(exc, FileNotFoundError):
        return f"Warning: Prettier execution failed: executable not found ({exc})"
    return f"Warning: Unexpected error during prettier formatting: {exc}"


def converge_prettier(
    files: Sequence[str | Path] | str | Path,
    repo_dir: str | Path | None = None,
    max_passes: int = DEFAULT_MAX_PASSES,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> bool:
    try:
        file_list = _normalize_file_paths(files)
        if not file_list:
            return True
        resolved_repo_dir = str(repo_dir) if repo_dir is not None else os.getcwd()
        existing_files = _filter_existing_files(file_list, resolved_repo_dir)
        if not existing_files:
            return True
        if not is_prettier_available():
            _warn_and_print(
                "Warning: 'npx' executable not found on PATH. "
                "Markdown formatting skipped; proceeding without formatting."
            )
            return False
        return _converge_files(existing_files, resolved_repo_dir, max_passes, timeout_seconds)
    except Exception as exc:
        _warn_and_print(_format_exception_message(exc, timeout_seconds))
        return False
