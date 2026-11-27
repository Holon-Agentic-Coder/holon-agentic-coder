from __future__ import annotations

import os
import shutil
import subprocess

_OPENSSL_TIMEOUT_SECONDS = 60
_CA_VALIDITY_DAYS = 397
_CHECKEND_VALID_EXIT_CODE = 0
_CHECKEND_EXPIRING_EXIT_CODE = 1
_OWNER_ONLY_KEY_MODE = 0o600

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
    openssl_path = shutil.which("openssl")
    if openssl_path is None:
        raise RuntimeError(
            f"openssl binary not found on PATH but is required to manage the Holon Root CA. {_OPENSSL_INSTALL_HINT}"
        )
    return openssl_path


def _run_openssl(*args: str) -> subprocess.CompletedProcess[str]:
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
    result = _run_openssl("x509", "-in", ca_cert_path, "-noout")

    if result.returncode != 0:
        raise RuntimeError(
            f"Root CA certificate at {ca_cert_path} is not a parseable X.509 certificate "
            f"(openssl: {result.stderr.strip() or 'unknown error'}). "
            f"Delete {os.path.dirname(ca_cert_path)} and re-run to regenerate a fresh Root CA. {_OPENSSL_INSTALL_HINT}"
        )


def _expires_within(ca_cert_path: str, window_seconds: int) -> bool:
    result = _run_openssl("x509", "-in", ca_cert_path, "-noout", "-checkend", str(window_seconds))

    if result.returncode == _CHECKEND_VALID_EXIT_CODE:
        return False
    if result.returncode == _CHECKEND_EXPIRING_EXIT_CODE:
        return True

    raise RuntimeError(
        f"Could not check the expiry of the Root CA certificate at {ca_cert_path} "
        f"(openssl exit {result.returncode}: {result.stderr.strip() or 'unknown error'}). "
        f"Delete {os.path.dirname(ca_cert_path)} and re-run to regenerate a fresh Root CA. {_OPENSSL_INSTALL_HINT}"
    )


def _precreate_owner_only_key_file(ca_key_path: str) -> None:
    # why: pre-create with 0o600 so the private key is never world-readable, even briefly, regardless of umask.
    key_fd = os.open(ca_key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _OWNER_ONLY_KEY_MODE)
    os.close(key_fd)


def _generate(openssl_path: str, ca_cert_path: str, ca_key_path: str, cert_dir: str) -> None:
    _precreate_owner_only_key_file(ca_key_path)

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
