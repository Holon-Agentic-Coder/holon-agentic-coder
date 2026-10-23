"""Shared helpers for credential-leak tests.

Fake credentials are assembled at runtime from parts that are not themselves token-shaped, so no
literal matching a provider token pattern is ever committed (the tracked-file hygiene scan in
`test_pre_commit_check.py` would otherwise flag this very file).
"""

import string

FRAGMENT_LEN = 8


def make_fake_github_token() -> str:
    """Return a 40-character, provider-shaped fake GitHub token with no repeating 8-character window."""
    alphabet = string.ascii_letters + string.digits
    return "gh" + "p_" + "".join(alphabet[(i * 7 + 3) % len(alphabet)] for i in range(36))


def make_fake_secret(label: str) -> str:
    """Return a distinctive fake secret value that is not shaped like any provider token."""
    alphabet = string.ascii_letters + string.digits
    seed = sum(map(ord, label))
    return label + "-" + "".join(alphabet[(seed + i * 11) % len(alphabet)] for i in range(24))


def token_fragments(secret: str, length: int = FRAGMENT_LEN) -> set[str]:
    """Every substring of `secret` of exactly `length` characters (any longer leak contains one)."""
    return {secret[i : i + length] for i in range(len(secret) - length + 1)}


def assert_no_token_fragment(text: str, secret: str, length: int = FRAGMENT_LEN) -> None:
    """Fail if `text` contains any `length`-character substring of `secret`.

    The failure message names the offset only, never the fragment, so a failing test cannot itself
    print the secret.
    """
    for fragment in token_fragments(secret, length):
        offset = text.find(fragment)
        assert offset == -1, f"a {length}-character fragment of a secret leaked at offset {offset}"
