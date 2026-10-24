"""Shared helpers for credential-leak tests.

Fake credentials are assembled at runtime from parts that are not themselves token-shaped
(`'gh' + 'p_' + uuid4().hex`), so no literal matching a provider token pattern is ever committed
(the tracked-file hygiene scan in `test_pre_commit_check.py` would otherwise flag this very file).
They are deliberately held under neutral names (`probe`, `sample`), never `token`, `secret` or
`password`, so CodeQL's sensitive-data heuristics do not trace a test value into a production log
sink.
"""

import os
from collections.abc import Iterable
from uuid import uuid4

FRAGMENT_LEN = 8


def make_gh_probe(prefix: str = "gh" + "p_") -> str:
    """Return a fresh value shaped like a GitHub token (`<prefix>` followed by 32 hex characters)."""
    return prefix + uuid4().hex


def make_probe(label: str = "v") -> str:
    """Return a fresh, distinctive value that is not shaped like any provider token.

    Args:
        label: A short readable tag, kept to one character of the result so the tag itself can
            never form a leak-check fragment that also occurs in ordinary output.
    """
    return label[:1] + uuid4().hex


def fragments(value: str, length: int = FRAGMENT_LEN) -> set[str]:
    """Every substring of `value` of exactly `length` characters (any longer leak contains one)."""
    return {value[i : i + length] for i in range(len(value) - length + 1)}


def assert_no_fragment(text: str, value: str, length: int = FRAGMENT_LEN) -> None:
    """Fail if `text` contains any `length`-character substring of `value`.

    The failure message names the offset only, never the fragment, so a failing test cannot itself
    print the value.
    """
    for fragment in fragments(value, length):
        offset = text.find(fragment)
        assert offset == -1, f"a {length}-character fragment of a probe value leaked at offset {offset}"


def assert_no_leak(value: str, *haystacks: str | Iterable[str] | os.PathLike, length: int = FRAGMENT_LEN) -> None:
    """Fail if any haystack contains a `length`-character substring of `value`.

    Args:
        value: The probe value that must not appear.
        haystacks: Captured text, an argv list (joined with spaces) or a file path (read as text).
        length: The fragment length checked.
    """
    for index, haystack in enumerate(haystacks):
        if isinstance(haystack, os.PathLike):
            with open(haystack, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        elif isinstance(haystack, str):
            text = haystack
        else:
            text = " ".join(str(item) for item in haystack)
        try:
            assert_no_fragment(text, value, length)
        except AssertionError as exc:
            raise AssertionError(f"haystack #{index}: {exc}") from None


def assert_names_repo(text: str, repo_path: str, branch: str | None = None) -> None:
    """Fail unless redacted output still names the repository path (and the branch, when given)."""
    assert repo_path in text, "the redacted output no longer names the repository"
    if branch is not None:
        assert branch in text, "the redacted output no longer names the branch"
