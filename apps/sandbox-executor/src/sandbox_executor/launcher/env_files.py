import contextlib
import logging
import os
import tempfile
import time

from sandbox_executor.redaction import carries_url_password, is_secret_env_name, redact_command

logger = logging.getLogger(__name__)

ENV_FILE_PREFIX = "holon-env-"
REPLAN_FEEDBACK_FILE_PREFIX = "holon-replan-feedback-"
# why: Linux MAX_ARG_STRLEN caps a single execve environment entry (KEY=VALUE + NUL) at 128 KiB.
MAX_ENV_VAR_BYTES = 128 * 1024
_ENV_FILE_STALE_SECONDS = 24 * 60 * 60
_OWNER_ONLY_DIR_MODE = 0o700
_GROUP_AND_OTHER_PERMS_MASK = 0o077
_OWNER_ONLY_FILE_MODE = 0o600
_HOLON_ENV_PREFIX = "HOLON_"


def _contains_line_break(value: str) -> bool:
    return "\n" in value or "\r" in value


def env_file_dir() -> str:
    path = os.path.expanduser(os.path.join("~", ".holon", "tmp", "env-files"))
    os.makedirs(path, mode=_OWNER_ONLY_DIR_MODE, exist_ok=True)
    if os.stat(path).st_mode & _GROUP_AND_OTHER_PERMS_MASK:
        os.chmod(path, _OWNER_ONLY_DIR_MODE)
    return path


def sweep_stale_env_files(directory: str, max_age_seconds: float = _ENV_FILE_STALE_SECONDS) -> None:
    cutoff = time.time() - max_age_seconds
    try:
        entries = os.listdir(directory)
    except OSError:
        return
    for name in entries:
        if not name.startswith((ENV_FILE_PREFIX, REPLAN_FEEDBACK_FILE_PREFIX)):
            continue
        path = os.path.join(directory, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                logger.debug("Removed stale credential env-file %s", path)
        except OSError as exc:
            logger.debug("Could not sweep env-file %s: %s", path, exc)


def write_env_file(env: dict[str, str]) -> str:
    for key, value in env.items():
        if _contains_line_break(value):
            raise ValueError(f"the value of {key} contains a line break and cannot be passed to the container")
    fd, path = tempfile.mkstemp(prefix=ENV_FILE_PREFIX, dir=env_file_dir())
    try:
        os.fchmod(fd, _OWNER_ONLY_FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.writelines(f"{key}={value}\n" for key, value in sorted(env.items()))
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)
        raise
    return path


def env_var_byte_size(key: str, value: str) -> int:
    return len(f"{key}={value}".encode("utf-8", errors="surrogateescape")) + 1


def validate_env_var_size(key: str, value: str, max_bytes: int = MAX_ENV_VAR_BYTES) -> None:
    size = env_var_byte_size(key, value)
    if size > max_bytes:
        raise ValueError(
            f"environment variable {key} is {size} bytes, exceeding the {max_bytes}-byte per-variable limit"
        )


def write_replan_feedback_file(content: str) -> str:
    fd, path = tempfile.mkstemp(prefix=REPLAN_FEEDBACK_FILE_PREFIX, suffix=".txt", dir=env_file_dir())
    try:
        os.fchmod(fd, _OWNER_ONLY_FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)
        raise
    return path


def is_secret_env_item(key: str, value: str) -> bool:
    item = f"{key}={value}"
    return is_secret_env_name(key) or redact_command([item]) != item or carries_url_password(value)


def is_env_file_item(key: str, value: str) -> bool:
    if key.startswith(_HOLON_ENV_PREFIX):
        return _fits_env_file(value)
    return is_secret_env_item(key, value)


def is_passthrough_env_item(key: str, value: str) -> bool:
    return key.startswith(_HOLON_ENV_PREFIX) and not _fits_env_file(value)


def _fits_env_file(value: str) -> bool:
    if _contains_line_break(value):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True
