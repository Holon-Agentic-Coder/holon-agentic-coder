"""A seeded source of fake credentials and fake temp names for the credential-leak matrices.

Every random input of `test_url_credential_redaction.py` and `test_malformed_config_never_crashes.py` is drawn from
a generator this module derives from one seed, so a failing case can be rerun exactly:

- the default seed is `DEFAULT_SEED`, used in CI;
- `CREDENTIAL_TEST_SEED=<integer>` overrides it for an exploratory or reproducing run;
- `CREDENTIAL_TEST_SEED=random` is the stress mode: one fresh seed is drawn per process and still reported.

Assertion messages that list failing case ids append `seed_note()`, so a failure prints the seed next to the case id
(never a value). `random.Random`, not `secrets`, is used on purpose: these are fake values whose only requirement is
that the same seed gives the same values again.
"""

import functools
import hashlib
import os
import random
import secrets
from collections.abc import Iterator

SEED_ENV = "CREDENTIAL_TEST_SEED"
DEFAULT_SEED = 0
STRESS_MODE = "random"
# `tempfile._RandomNameSequence` draws 8 characters from this alphabet; the seeded sequence keeps its shape.
TEMP_NAME_CHARACTERS = "abcdefghijklmnopqrstuvwxyz0123456789_"
TEMP_NAME_LENGTH = 8


@functools.cache
def resolve_seed() -> int:
    """Return the seed for this process: `CREDENTIAL_TEST_SEED`, a fresh one in stress mode, or `DEFAULT_SEED`.

    Memoised, so every test in one process (one xdist worker) sees the same seed, in stress mode too. The module
    resolves it once on import (at collection), because several tests empty `os.environ` before drawing.

    Raises:
        ValueError: The variable holds neither an integer nor `random`.
    """
    raw = os.environ.get(SEED_ENV, "").strip()
    if not raw:
        return DEFAULT_SEED
    if raw.lower() == STRESS_MODE:
        return secrets.randbits(32)
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{SEED_ENV} must be an integer or {STRESS_MODE!r}") from None


def generator(label: str) -> random.Random:
    """Return a fresh generator for `label` (a test or matrix name), derived from the resolved seed.

    The derivation hashes `"<seed>:<label>"` with SHA-256 rather than the salted built-in `hash`, so the draws of one
    label do not depend on collection order, on which worker runs the test, or on `PYTHONHASHSEED`.
    """
    digest = hashlib.sha256(f"{resolve_seed()}:{label}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def seed_note() -> str:
    """Return the text appended to a failure message so the failing run can be reproduced."""
    seed = resolve_seed()
    return f"seed={seed} (set {SEED_ENV}={seed} to reproduce)"


def name_sequence(rng: random.Random) -> Iterator[str]:
    """Yield 8-character temp names in `tempfile`'s own alphabet, drawn from `rng`, without end.

    The seeded stand-in for `tempfile._RandomNameSequence()`, so the temp names a case receives are reproducible.
    """
    while True:
        yield "".join(rng.choice(TEMP_NAME_CHARACTERS) for _ in range(TEMP_NAME_LENGTH))


# Resolve now, while the environment is the one the run was started with; a later call returns this value.
resolve_seed()
