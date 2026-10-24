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


# The URL-userinfo shape matrix shared by the redaction, CLI and pre-flight leak tests, so the echo
# redactor, the proxy warnings and env-file routing are all held to the same set of shapes.
URL_PASSWORD_FORMS: dict[str, str] = {
    "schemeless": "alice:{v}@proxy.corp:8080",
    "schemeless-path": "alice:{v}@proxy.corp:8080/v1",
    "schemeless-double-slash-path": "alice:{v}@proxy.corp:8080//v1",
    "scheme-relative": "//alice:{v}@proxy:1080",
    "scheme-relative-double-slash-path": "//alice:{v}@proxy:1080//v1",
    "http": "http://alice:{v}@proxy:3128",
    "https-double-slash-path": "https://alice:{v}@proxy:3128//v1",
    "socks5": "socks5://alice:{v}@proxy:1080",
}

# Separators placed between runtime-built parts of the password: each URL delimiter, `@`, `:` and `//`.
# In "all" the `@` comes last: a raw `@` followed later by a `/`, `?` or `#` is ambiguous with an `@`
# in the path (RFC 3986), and every userinfo rule here resolves it as the path.
URL_PASSWORD_SEPARATORS: dict[str, tuple[str, ...]] = {
    "plain": (),
    "slash": ("/",),
    "query": ("?",),
    "fragment": ("#",),
    "at": ("@",),
    "colon": (":",),
    "double-slash": ("//",),
    "all": ("/", "?", "#", "//", ":", "@"),
}

# A bare token as the whole userinfo; only an `http(s)://` authority marks it as a credential.
URL_TOKEN_FORMS: dict[str, str] = {
    "token-https": "https://{v}@proxy:3128",
    "token-http-double-slash-path": "http://{v}@proxy:3128//v1",
}

# Credential-free values that every redactor and the env-file routing must leave byte-identical.
CREDENTIAL_FREE_URLS: tuple[str, ...] = (
    "proxy.corp:8080",
    "proxy.corp:8080//v1",
    "proxy.corp:8080/x@y",
    "proxy.corp:8080//a@b",
    "//proxy:1080//v1",
    "http://proxy.corp:8080//v1",
    "https://github.com/myorg/myrepo/blob/main/a@b.md",
    "ssh://git@github.com/myorg/myrepo.git",
    "[::1]:8080",
    "host:8080/p?q=1#f",
)


def url_userinfo_cases() -> list[tuple[str, str, tuple[str, ...]]]:
    """Return `(case_id, form, separators)` for every password form x shape and every token form."""
    cases = [
        (f"{form_id}-{shape_id}", form, seps)
        for form_id, form in URL_PASSWORD_FORMS.items()
        for shape_id, seps in URL_PASSWORD_SEPARATORS.items()
    ]
    cases.extend((form_id, form, ()) for form_id, form in URL_TOKEN_FORMS.items())
    return cases


def make_url_probe(form: str, separators: tuple[str, ...]) -> tuple[str, list[str]]:
    """Build a URL value from `form` with a runtime password; return it and the password's parts.

    The parts are the runtime-built pieces between the separators, each checked for leaks on its
    own because a redactor that stops at a separator would leak only the pieces after it.
    """
    parts = [make_probe(chr(ord("a") + i)) for i in range(len(separators) + 1)]
    sample = parts[0] + "".join(sep + part for sep, part in zip(separators, parts[1:], strict=True))
    return form.format(v=sample), parts
