"""Every environment variable the code under test reads as a credential, for the isolation fixture and its guard."""

from sandbox_executor.claude_credential import CLAUDE_CREDENTIAL_ENV_NAMES

# Runner keys that `agent_defaults` consults when it detects which runner holds a credential.
_RUNNER_KEY_NAMES = (
    "AGY_USER_TOKEN",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "PI_API_KEY",
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "OPENCODE_API_KEY",
)

#: The production Claude names come first so a new production name is picked up without an edit here.
CREDENTIAL_ENV_NAMES: tuple[str, ...] = tuple(dict.fromkeys((*CLAUDE_CREDENTIAL_ENV_NAMES, *_RUNNER_KEY_NAMES)))
