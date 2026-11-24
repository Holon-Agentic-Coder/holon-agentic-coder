"""One-time migration of the plaintext claude token file into the OS secret store, and the pre-flight wording.

Holon used to keep the claude credential in ``~/.holon/secrets/claude-oauth-token``. This is the only code that reads
that file, and only to import it: the token is stored through ``claude_keychain`` and the file is deleted once a
read-back from the store returned the identical token. Every doubt keeps the file. A token-file reference that names
the file is ignored by the configuration layer (``config_env_secrets``, through ``is_legacy_token_file``), so a kept
file is never read as the credential. No message holds the token, a part of it or any tool output.
"""

from __future__ import annotations

import enum
import os
import sys
from dataclasses import dataclass

from sandbox_executor import claude_keychain
from sandbox_executor.claude_credential import read_token_file
from sandbox_executor.claude_keychain import KeychainLookup, LookupStatus

LEGACY_TOKEN_FILE_DISPLAY = "~/.holon/secrets/claude-oauth-token"
_ENTRY = f"service '{claude_keychain.SERVICE}', account '{claude_keychain.ACCOUNT}'"
_STALE_REFERENCE_NOTE = "Remove any HOLON_AGENT_OAUTH_TOKEN_FILE line that named this file from the Holon .env."


def legacy_token_file_path() -> str:
    """Return the target-native path of the retired plaintext token file under the current home."""
    return os.path.join(os.path.expanduser("~"), ".holon", "secrets", "claude-oauth-token")


def is_legacy_token_file(path: str) -> bool:
    """Return True when ``path`` names the retired plaintext token file, directly or through symbolic links."""
    return os.path.realpath(path) == os.path.realpath(legacy_token_file_path())


class MigrationStatus(enum.Enum):
    """What the one-time migration did."""

    NOT_NEEDED = "not_needed"  # there is no plaintext file
    MIGRATED = "migrated"  # the store holds the token and the file is deleted
    KEPT = "kept"  # the file still exists


@dataclass(frozen=True)
class MigrationOutcome:
    """The migration result; ``message`` is one line for the operator, empty for ``NOT_NEEDED``."""

    status: MigrationStatus
    message: str = ""


def _store_command_text() -> str:
    """The operator's store one-liner for the current platform, or both one-liners on an unsupported platform."""
    return claude_keychain.store_command() or claude_keychain.store_commands_text()


def _kept(reason: str, delete_by_hand: bool = False) -> MigrationOutcome:
    """The outcome for a file that was not imported.

    Args:
        reason: Why the file was not imported; it never holds the token.
        delete_by_hand: True when no later import can remove the file (it cannot be imported, or the store holds
            another credential), so the line ends with the manual delete that stops it repeating on every pre-flight.
    """
    remedy = f"To store a credential by hand: {_store_command_text()}"
    if delete_by_hand:
        remedy = (
            f"Store the credential you want by hand if {claude_keychain.store_name()} does not hold it "
            f"({_store_command_text()}), then delete the plaintext file by hand."
        )
    return MigrationOutcome(
        MigrationStatus.KEPT,
        f"The claude credential in {LEGACY_TOKEN_FILE_DISPLAY} was not imported: {reason}. The plaintext file was "
        f"left untouched and is no longer read. {remedy}",
    )


def _delete_imported_file(path: str, already_stored: bool) -> MigrationOutcome:
    """Delete the plaintext file once the store provably holds its token; the secrets directory stays."""
    store = f"{claude_keychain.store_name()} ({_ENTRY})"
    try:
        os.remove(path)
    except OSError:
        return MigrationOutcome(
            MigrationStatus.KEPT,
            f"The claude credential from {LEGACY_TOKEN_FILE_DISPLAY} is stored in {store}, but the plaintext file "
            "could not be deleted. It is no longer read; delete it by hand.",
        )
    done = (
        f"The claude credential in {LEGACY_TOKEN_FILE_DISPLAY} was already stored in {store}; nothing was written"
        if already_stored
        else f"Imported the claude credential from {LEGACY_TOKEN_FILE_DISPLAY} into {store}"
    )
    message = f"{done} and the plaintext file was deleted. {_STALE_REFERENCE_NOTE}"
    return MigrationOutcome(MigrationStatus.MIGRATED, message)


def _importable_token(path: str) -> tuple[str | None, str]:
    """Return the file's token, or None with the reason the file cannot be imported."""
    if os.path.islink(path) or not os.path.isfile(path):
        return None, "it is not a regular file"
    token, _warning = read_token_file(path)
    if token is None:
        return None, "the file is empty, unreadable or does not hold a single token"
    if not claude_keychain.is_storable_token(token):
        return None, "the file holds a character that cannot be imported (only ASCII letters, digits and ._~+/=- can)"
    return token, ""


def _ensure_stored(token: str) -> tuple[str, bool]:
    """Make the store hold ``token`` without ever overwriting another entry.

    Returns:
        ``(reason, already_stored)``: the reason is empty only when the store provably holds the identical token.
    """
    lookup = claude_keychain.lookup_token()
    store = claude_keychain.store_name()
    if lookup.status is LookupStatus.UNAVAILABLE:
        return "no usable secret store was found", False
    if lookup.status is LookupStatus.FOUND:
        different = f"{store} already holds a different claude credential, which was kept"
        return ("" if lookup.token == token else different), True
    return ("" if claude_keychain.store_token(token) else f"the write to {store} could not be verified"), False


def _migrate(path: str) -> MigrationOutcome:
    if not os.path.lexists(path):
        return MigrationOutcome(MigrationStatus.NOT_NEEDED)
    token, reason = _importable_token(path)
    if token is None:
        return _kept(reason, delete_by_hand=True)
    reason, already_stored = _ensure_stored(token)
    if reason:
        # A refusal with an entry in the store means the store holds another credential, which no import replaces.
        return _kept(reason, delete_by_hand=already_stored)
    return _delete_imported_file(path, already_stored)


def migrate_legacy_token_file() -> MigrationOutcome:
    """Import the plaintext token file into the OS secret store once, and delete it only after a verified store.

    Returns:
        ``NOT_NEEDED`` when there is no file (no platform tool is started), ``MIGRATED`` when the store holds the
        file's token and the file is deleted, ``KEPT`` in every other case: the file is unreadable or holds a
        character the store command cannot carry, there is no usable store, the store holds a different credential
        (never overwritten), the write could not be verified by a read-back, or the delete failed.

    Side effects:
        May write one entry to the OS secret store and delete the plaintext file. Prints the outcome's message, when
        there is one, as one line on standard error; it never contains the token.
    """
    outcome = _migrate(legacy_token_file_path())
    if outcome.message:
        print(outcome.message, file=sys.stderr)
    return outcome


def claude_missing_credential_message(lookup: KeychainLookup | None = None) -> str:
    """The pre-flight output for "no claude credential": the cause, the legacy file's state and the remedy.

    Args:
        lookup: The keychain answer the caller's credential resolution already got; without it the store is asked.
    """
    if lookup is None:
        lookup = claude_keychain.lookup_token()
    if lookup.status is LookupStatus.UNAVAILABLE:
        cause = "no usable OS secret store was found (tool missing, keychain locked or no secret service running)"
    else:
        cause = f"{claude_keychain.store_name()} has no entry for {_ENTRY}"
    legacy = ""
    if os.path.lexists(legacy_token_file_path()):
        legacy = (
            f" The plaintext file {LEGACY_TOKEN_FILE_DISPLAY} was not imported, was left untouched and is no longer"
            " read."
        )
    return (
        f"No claude credential was found: {cause}, and neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set."
        f"{legacy} Run 'claude setup-token' to obtain a token, then store it with: {_store_command_text()}"
    )


def claude_not_logged_in_hint() -> str:
    """The pre-flight remediation hint for claude: the keychain first, then the explicit variables, then a session."""
    return (
        "No claude credential reached the container. Run 'claude setup-token' and store the token in the OS keychain "
        f"({_store_command_text()}), or set CLAUDE_CODE_OAUTH_TOKEN for a subscription token (sk-ant-oat...), or set "
        "ANTHROPIC_API_KEY for an API key (sk-ant-api...). Alternatively initialise and mount the session "
        "(see docs/executor/agent_credentials_requirements.md), or choose another runner with --agent or HOLON_AGENT."
    )
