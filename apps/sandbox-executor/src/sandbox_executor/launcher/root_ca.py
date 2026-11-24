from __future__ import annotations

import logging
import os

from sandbox_executor.launcher import root_ca_openssl

logger = logging.getLogger(__name__)

_SECONDS_PER_DAY = 24 * 60 * 60
_CA_RENEWAL_WINDOW_DAYS = 30
_CA_RENEWAL_WINDOW_SECONDS = _CA_RENEWAL_WINDOW_DAYS * _SECONDS_PER_DAY
_OWNER_ONLY_KEY_MODE = 0o600
_OWNER_ONLY_DIR_MODE = 0o700
_DEFAULT_CERT_DIR = "~/.holon/certs"
_CA_CERT_FILENAME = "holon-root-ca.crt"
_CA_KEY_FILENAME = "holon-root-ca.key"


def _harden_key_permissions(ca_key_path: str) -> None:
    os.chmod(ca_key_path, _OWNER_ONLY_KEY_MODE)


def _remove_cached_ca(ca_cert_path: str, ca_key_path: str) -> None:
    for path in (ca_cert_path, ca_key_path):
        try:
            os.remove(path)
        except FileNotFoundError:
            logger.debug("Stale Root CA artifact %s was already gone", path)


def _ensure_root_ca(cert_dir: str | None = None) -> tuple[str, str, bool]:
    openssl_path = root_ca_openssl._openssl_binary()

    if cert_dir is None:
        cert_dir = os.path.expanduser(_DEFAULT_CERT_DIR)

    os.makedirs(cert_dir, mode=_OWNER_ONLY_DIR_MODE, exist_ok=True)
    ca_cert_path = os.path.join(cert_dir, _CA_CERT_FILENAME)
    ca_key_path = os.path.join(cert_dir, _CA_KEY_FILENAME)

    if os.path.exists(ca_cert_path) and os.path.exists(ca_key_path):
        root_ca_openssl._assert_valid_cert(ca_cert_path)
        if root_ca_openssl._expires_within(ca_cert_path, _CA_RENEWAL_WINDOW_SECONDS):
            logger.warning(
                "Cached Root CA at %s expires within %s days; regenerating it instead of reusing a stale trust anchor.",
                ca_cert_path,
                _CA_RENEWAL_WINDOW_SECONDS // _SECONDS_PER_DAY,
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
    ca_cert_path, ca_key_path, _ = _ensure_root_ca(cert_dir)
    return ca_cert_path, ca_key_path
