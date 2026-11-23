"""The ``openssl`` calls behind the host Root CA: probing the binary, generating, validating and checking expiry.

``launcher.root_ca`` owns the cache lifecycle and calls these helpers through this module's attributes.

Actual behaviour:

* ``openssl`` is probed with :func:`shutil.which` before anything is written. When it is
  missing, a :class:`RuntimeError` carrying an actionable install hint is raised.
* The certificate is generated with ``openssl req -x509`` under a 60 second timeout.
  ``subprocess.CalledProcessError`` and ``subprocess.TimeoutExpired`` are translated into
  :class:`RuntimeError` including the captured stderr.
* The generated certificate carries explicit CA extensions (``basicConstraints=critical,CA:TRUE``
  and ``keyUsage=critical,keyCertSign,cRLSign``). A CA certificate without ``keyUsage`` is refused
  as a trust anchor by several TLS stacks (BoringSSL/Node, Go, OpenSSL in strict ``purpose``
  modes), which would break the whole trust bootstrap.
* ``openssl x509 -noout`` returns 0 for an *expired* certificate, so expiry is checked separately
  with ``openssl x509 -checkend``.
"""

from __future__ import annotations

import os
import shutil
import subprocess

_OPENSSL_TIMEOUT_SECONDS = 60

#: Lifetime of a freshly generated Root CA, in days. Bounded on purpose: together with the renewal
#: window in ``launcher.root_ca`` it guarantees a cached CA is rotated instead of being silently
#: reused forever.
_CA_VALIDITY_DAYS = 397

#: Explicit CA extensions. ``keyUsage`` is mandatory in practice: without it several TLS stacks
#: refuse the certificate as a trust anchor even though it is a perfectly valid CA.
_CA_EXTENSIONS = (
    "-addext",
    "basicConstraints=critical,CA:TRUE",
    "-addext",
    "keyUsage=critical,keyCertSign,cRLSign",
    "-addext",
    "subjectKeyIdentifier=hash",
)

_OPENSSL_INSTALL_HINT = (
    "Install OpenSSL and retry: macOS 'brew install openssl', Debian/Ubuntu 'apt-get install openssl'."
)


def _openssl_binary() -> str:
    """Return the absolute path of the ``openssl`` binary or raise with an install hint."""
    openssl_path = shutil.which("openssl")
    if openssl_path is None:
        raise RuntimeError(
            f"openssl binary not found on PATH but is required to manage the Holon Root CA. {_OPENSSL_INSTALL_HINT}"
        )
    return openssl_path


def _run_openssl(*args: str) -> subprocess.CompletedProcess[str]:
    """Run ``openssl`` with captured output and no implicit raising."""
    openssl_path = shutil.which("openssl") or "openssl"
    try:
        return subprocess.run(
            [openssl_path, *args],
            capture_output=True,
            text=True,
            timeout=_OPENSSL_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise RuntimeError(f"Could not run 'openssl {' '.join(args)}': {exc}") from exc


def _assert_valid_cert(ca_cert_path: str) -> None:
    """Assert that ``ca_cert_path`` is a parseable X.509 certificate.

    Raises:
        RuntimeError: If ``openssl`` cannot parse the artifact (or times out doing so).
    """
    result = _run_openssl("x509", "-in", ca_cert_path, "-noout")

    if result.returncode != 0:
        raise RuntimeError(
            f"Root CA certificate at {ca_cert_path} is not a parseable X.509 certificate "
            f"(openssl: {result.stderr.strip() or 'unknown error'}). "
            f"Delete {os.path.dirname(ca_cert_path)} and re-run to regenerate a fresh Root CA. {_OPENSSL_INSTALL_HINT}"
        )


def _expires_within(ca_cert_path: str, window_seconds: int) -> bool:
    """True when ``ca_cert_path`` expires within ``window_seconds``.

    ``openssl x509 -noout`` exits 0 for an expired certificate, so expiry needs its own probe:
    ``-checkend`` exits 0 when the certificate stays valid beyond the window and 1 when it does
    not. Any other exit status is an openssl-level failure and is reported, not swallowed.

    Raises:
        RuntimeError: If ``openssl`` cannot evaluate the expiry of the artifact.
    """
    result = _run_openssl("x509", "-in", ca_cert_path, "-noout", "-checkend", str(window_seconds))

    if result.returncode == 0:
        return False
    if result.returncode == 1:
        return True

    raise RuntimeError(
        f"Could not check the expiry of the Root CA certificate at {ca_cert_path} "
        f"(openssl exit {result.returncode}: {result.stderr.strip() or 'unknown error'}). "
        f"Delete {os.path.dirname(ca_cert_path)} and re-run to regenerate a fresh Root CA. {_OPENSSL_INSTALL_HINT}"
    )


def _generate(openssl_path: str, ca_cert_path: str, ca_key_path: str, cert_dir: str) -> None:
    """Run ``openssl req -x509`` to create a fresh CA pair, translating failures into RuntimeError."""
    # Pre-create the key file with 0o600 so the private key is never world-readable, even briefly,
    # regardless of the caller's umask.
    key_fd = os.open(ca_key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.close(key_fd)

    try:
        subprocess.run(
            [
                openssl_path,
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-keyout",
                ca_key_path,
                "-out",
                ca_cert_path,
                "-days",
                str(_CA_VALIDITY_DAYS),
                "-nodes",
                "-subj",
                "/CN=Holon Agent Root CA/O=Holon Agentic Coder",
                *_CA_EXTENSIONS,
            ],
            capture_output=True,
            text=True,
            timeout=_OPENSSL_TIMEOUT_SECONDS,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise RuntimeError(
            f"OpenSSL failed to generate the Holon Root CA in {cert_dir} (exit {exc.returncode}): {stderr or exc}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"OpenSSL timed out after {_OPENSSL_TIMEOUT_SECONDS}s generating the Holon Root CA in {cert_dir}."
        ) from exc
