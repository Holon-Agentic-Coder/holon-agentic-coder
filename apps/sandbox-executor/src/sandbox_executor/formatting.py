"""Prettier formatting convergence utility for flow-produced markdown and code artifacts."""

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
    """Check if npx is available on the system PATH.

    Returns:
        bool: True if npx executable is found, False otherwise.
    """
    return shutil.which("npx") is not None


def converge_prettier(
    files: Sequence[str | Path] | str | Path,
    repo_dir: str | Path | None = None,
    max_passes: int = DEFAULT_MAX_PASSES,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> bool:
    """Run prettier in a bounded loop until all specified files pass check verification.

    Executes `npx --yes prettier@3.8.4 --write <files>` followed by
    `npx --yes prettier@3.8.4 --check <files>` in up to `max_passes` iterations.
    If the check command succeeds with exit code 0, formatting has converged and
    the function returns True. If the loop completes without convergence, or if
    npx is not installed, or if a subprocess times out or encounters errors, the
    function logs a warning and cleanly returns False without raising exceptions or
    corrupting files.

    Args:
        files: A single file path or a sequence of file paths (str or Path) to format.
        repo_dir: Working directory in which to execute prettier commands. Defaults to
            the current working directory if None.
        max_passes: Maximum number of formatting passes before giving up. Defaults to 3.
        timeout_seconds: Per-command timeout in seconds for subprocess invocations.
            Defaults to 30.

    Returns:
        bool: True if all target files passed prettier check within max_passes,
            False otherwise.

    Side Effects:
        Formats target files in-place on the filesystem when write passes execute.
        Logs status and warnings to logger and sys.stderr.
    """
    try:
        # Normalize files into a list of strings
        file_list: list[str] = [str(files)] if isinstance(files, (str, Path)) else [str(f) for f in files]

        if not file_list:
            return True

        resolved_repo_dir = str(repo_dir) if repo_dir is not None else os.getcwd()

        # Filter out nonexistent files
        existing_files: list[str] = []
        for file_path in file_list:
            disk_path = file_path if os.path.isabs(file_path) else os.path.join(resolved_repo_dir, file_path)
            if os.path.exists(disk_path):
                existing_files.append(file_path)
            else:
                logger.debug("Skipping nonexistent file for prettier formatting: %s", file_path)

        if not existing_files:
            return True

        if not is_prettier_available():
            msg = (
                "Warning: 'npx' executable not found on PATH. "
                "Markdown formatting skipped; proceeding without formatting."
            )
            logger.warning(msg)
            print(msg, file=sys.stderr)
            return False

        pass_count = 0
        while pass_count < max_passes:
            pass_count += 1

            write_cmd = ["npx", "--yes", PRETTIER_SPEC, "--write", *existing_files]
            write_res = subprocess.run(
                write_cmd,
                cwd=resolved_repo_dir,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )

            if write_res.returncode != 0:
                err_msg = (
                    f"Warning: Prettier write pass {pass_count} failed with exit code {write_res.returncode}:\n"
                    f"{write_res.stderr.strip()}"
                )
                logger.warning(err_msg)
                print(err_msg, file=sys.stderr)
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
                logger.info(
                    "Prettier converged in %d pass(es) for %d file(s).",
                    pass_count,
                    len(existing_files),
                )
                return True

        warn_msg = f"Warning: Prettier failed to converge on {len(existing_files)} file(s) after {max_passes} passes."
        logger.warning(warn_msg)
        print(warn_msg, file=sys.stderr)
        return False

    except subprocess.TimeoutExpired as e:
        timeout_msg = f"Warning: Prettier command timed out after {timeout_seconds} seconds: {e}"
        logger.warning(timeout_msg)
        print(timeout_msg, file=sys.stderr)
        return False
    except FileNotFoundError as e:
        fnf_msg = f"Warning: Prettier execution failed: executable not found ({e})"
        logger.warning(fnf_msg)
        print(fnf_msg, file=sys.stderr)
        return False
    except Exception as e:
        unexpected_msg = f"Warning: Unexpected error during prettier formatting: {e}"
        logger.warning(unexpected_msg)
        print(unexpected_msg, file=sys.stderr)
        return False
