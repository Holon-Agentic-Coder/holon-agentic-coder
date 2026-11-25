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
    return os.path.join(os.path.expanduser("~"), ".holon", "secrets", "claude-oauth-token")


def is_legacy_token_file(path: str) -> bool:
    return os.path.realpath(path) == os.path.realpath(legacy_token_file_path())


class MigrationStatus(enum.Enum):
    NOT_NEEDED = "not_needed"
    MIGRATED = "migrated"
    KEPT = "kept"


@dataclass(frozen=True)
class MigrationOutcome:
    status: MigrationStatus
    message: str = ""


def _store_command_text() -> str:
    return claude_keychain.store_command() or claude_keychain.store_commands_text()


def _kept(reason: str, delete_by_hand: bool = False) -> MigrationOutcome:
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
    if os.path.islink(path) or not os.path.isfile(path):
        return None, "it is not a regular file"
    token, _warning = read_token_file(path)
    if token is None:
        return None, "the file is empty, unreadable or does not hold a single token"
    if not claude_keychain.is_storable_token(token):
        return None, "the file holds a character that cannot be imported (only ASCII letters, digits and ._~+/=- can)"
    return token, ""


def _ensure_stored(token: str) -> tuple[str, bool]:
    lookup = claude_keychain.lookup_token()
    store_label = claude_keychain.store_name()
    if lookup.status is LookupStatus.UNAVAILABLE:
        return "no usable secret store was found", False
    if lookup.status is LookupStatus.FOUND:
        different_credential_reason = f"{store_label} already holds a different claude credential, which was kept"
        return ("" if lookup.token == token else different_credential_reason), True
    write_verified = claude_keychain.store_token(token)
    return ("" if write_verified else f"the write to {store_label} could not be verified"), False


def _migrate(path: str) -> MigrationOutcome:
    if not os.path.lexists(path):
        return MigrationOutcome(MigrationStatus.NOT_NEEDED)
    token, reason = _importable_token(path)
    if token is None:
        return _kept(reason, delete_by_hand=True)
    reason, already_stored = _ensure_stored(token)
    if reason:
        store_holds_existing_credential = already_stored
        return _kept(reason, delete_by_hand=store_holds_existing_credential)
    return _delete_imported_file(path, already_stored)


def migrate_legacy_token_file() -> MigrationOutcome:
    outcome = _migrate(legacy_token_file_path())
    if outcome.message:
        print(outcome.message, file=sys.stderr)
    return outcome


def claude_missing_credential_message(lookup: KeychainLookup | None = None) -> str:
    if lookup is None:
        lookup = claude_keychain.lookup_token()
    if lookup.status is LookupStatus.UNAVAILABLE:
        cause = "no usable OS secret store was found (tool missing, keychain locked or no secret service running)"
    else:
        cause = f"{claude_keychain.store_name()} has no entry for {_ENTRY}"
    legacy_file_note = ""
    if os.path.lexists(legacy_token_file_path()):
        legacy_file_note = (
            f" The plaintext file {LEGACY_TOKEN_FILE_DISPLAY} was not imported, was left untouched and is no longer"
            " read."
        )
    return (
        f"No claude credential was found: {cause}, and neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set."
        f"{legacy_file_note} Run 'claude setup-token' to obtain a token, then store it with: {_store_command_text()}"
    )


def claude_not_logged_in_hint() -> str:
    return (
        "No claude credential reached the container. Run 'claude setup-token' and store the token in the OS keychain "
        f"({_store_command_text()}), or set CLAUDE_CODE_OAUTH_TOKEN for a subscription token (sk-ant-oat...), or set "
        "ANTHROPIC_API_KEY for an API key (sk-ant-api...). Alternatively initialise and mount the session "
        "(see docs/executor/agent_credentials_requirements.md), or choose another runner with --agent or HOLON_AGENT."
    )
