"""The host Root CA the sandbox trusts for the holon-coherence proxy: cache reuse, renewal and key permissions.

This module owns exactly one job: guarantee that a *valid, trusted-shape, non-expiring* self-signed
Root CA exists on the host so the sandbox can trust the proxy sidecar or an attached proxy. The
``openssl`` calls live in ``launcher.root_ca_openssl``.

The on-disk layout (``~/.holon/certs/holon-root-ca.crt`` and ``holon-root-ca.key``) matches
holon-coherence's own CA generator, so a CA created by either tool is reused by the other.

* The private key is created with mode ``0o600`` (owner read/write only).
* Every returned artifact is validated with ``openssl x509 -in <path> -noout``, both after fresh
  generation and when reusing an existing file, so a previously poisoned cache is reported instead
  of silently trusted.
* A cached CA that expires within the renewal window is deleted and regenerated instead of being
  reused forever.

There is deliberately no "fallback certificate" generator: unparseable PEM blobs would be cached
forever by the existence check and break every TLS client inside the sandbox with an opaque error.
Fail loudly instead.
"""

from __future__ import annotations

import logging
import os

from sandbox_executor.launcher import root_ca_openssl

logger = logging.getLogger(__name__)

#: A cached CA expiring inside this window is treated as stale and regenerated (30 days).
_CA_RENEWAL_WINDOW_SECONDS = 30 * 24 * 60 * 60

_CA_CERT_FILENAME = "holon-root-ca.crt"
_CA_KEY_FILENAME = "holon-root-ca.key"


def _harden_key_permissions(ca_key_path: str) -> None:
    """Restrict the CA private key to owner read/write (``0o600``)."""
    os.chmod(ca_key_path, 0o600)


def _remove_cached_ca(ca_cert_path: str, ca_key_path: str) -> None:
    """Delete a stale cached CA pair so the next generation step starts from a clean slate."""
    for path in (ca_cert_path, ca_key_path):
        try:
            os.remove(path)
        except FileNotFoundError:
            logger.debug("Stale Root CA artifact %s was already gone", path)


def _ensure_root_ca(cert_dir: str | None = None) -> tuple[str, str, bool]:
    """Internal implementation behind :func:`generate_root_ca`.

    Returns:
        tuple[str, str, bool]: ``(ca_cert_path, ca_key_path, generated)`` where ``generated`` is
        False only when a still-valid cached CA was reused.
    """
    openssl_path = root_ca_openssl._openssl_binary()

    if cert_dir is None:
        cert_dir = os.path.expanduser("~/.holon/certs")

    os.makedirs(cert_dir, mode=0o700, exist_ok=True)
    ca_cert_path = os.path.join(cert_dir, _CA_CERT_FILENAME)
    ca_key_path = os.path.join(cert_dir, _CA_KEY_FILENAME)

    if os.path.exists(ca_cert_path) and os.path.exists(ca_key_path):
        root_ca_openssl._assert_valid_cert(ca_cert_path)
        if root_ca_openssl._expires_within(ca_cert_path, _CA_RENEWAL_WINDOW_SECONDS):
            logger.warning(
                "Cached Root CA at %s expires within %s days; regenerating it instead of reusing a stale trust anchor.",
                ca_cert_path,
                _CA_RENEWAL_WINDOW_SECONDS // 86400,
            )
            _remove_cached_ca(ca_cert_path, ca_key_path)
        else:
            logger.info("Reusing existing Root CA certificate at %s", ca_cert_path)
            _harden_key_permissions(ca_key_path)
            return ca_cert_path, ca_key_path, False

    logger.info("Generating self-signed Root CA certificate at %s", cert_dir)
    root_ca_openssl._generate(openssl_path, ca_cert_path, ca_key_path, cert_dir)

    _harden_key_permissions(ca_key_path)
    root_ca_openssl._assert_valid_cert(ca_cert_path)

    return ca_cert_path, ca_key_path, True


def generate_root_ca(cert_dir: str | None = None) -> tuple[str, str]:
    """Ensure a valid, properly extended, non-expiring self-signed Root CA exists on the host.

    Args:
        cert_dir: Directory where certs should be stored. Defaults to ``~/.holon/certs``.

    Returns:
        tuple[str, str]: Paths to ``(ca_cert_path, ca_key_path)``. The certificate is guaranteed to
        be parseable by ``openssl x509``, to carry CA ``basicConstraints``/``keyUsage`` extensions,
        to stay valid for more than 30 days, and the key is mode ``0o600``.

    Raises:
        RuntimeError: If ``openssl`` is unavailable, generation fails or times out, or an existing
            cached certificate is not a parseable X.509 artifact.

    Side effects:
        Creates ``cert_dir`` (mode ``0o700`` when newly created) and writes or rotates the CA pair in it.
    """
    ca_cert_path, ca_key_path, _ = _ensure_root_ca(cert_dir)
    return ca_cert_path, ca_key_path
