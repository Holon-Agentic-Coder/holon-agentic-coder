"""Tests for the host Root CA owned by ``launcher.root_ca`` (cache reuse, renewal, key mode) and its openssl calls."""

import os
import stat
import subprocess
from types import SimpleNamespace

import pytest
from sandbox_executor.launcher import root_ca_openssl
from sandbox_executor.launcher.root_ca import generate_root_ca

_THIRTY_DAYS_SECONDS = 30 * 24 * 60 * 60


def _read_text(path: str) -> str:
    with open(path) as handle:
        return handle.read()


def _openssl_text(cert_path: str) -> str:
    """Return ``openssl x509 -noout -text`` output for ``cert_path``."""
    result = subprocess.run(["openssl", "x509", "-in", cert_path, "-noout", "-text"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _expires_within(cert_path: str, window_seconds: int = _THIRTY_DAYS_SECONDS) -> bool:
    """True when ``cert_path`` expires inside ``window_seconds`` (openssl -checkend semantics)."""
    result = subprocess.run(
        ["openssl", "x509", "-in", cert_path, "-noout", "-checkend", str(window_seconds)], capture_output=True
    )
    assert result.returncode in (0, 1), result
    return result.returncode == 1


def _make_ca_with_validity(cert_dir, days: int) -> tuple[str, str]:
    """Write a throwaway CA pair valid for ``days`` at the cached-CA filenames."""
    cert_path = cert_dir / "holon-root-ca.crt"
    key_path = cert_dir / "holon-root-ca.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(key_path),
            "-out",
            str(cert_path),
            "-days",
            str(days),
            "-nodes",
            "-subj",
            "/CN=Holon Agent Root CA/O=Holon Agentic Coder",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
        ],
        check=True,
        capture_output=True,
    )
    return str(cert_path), str(key_path)


def test_ca_generator(tmp_path):
    cert_path, key_path = generate_root_ca(cert_dir=str(tmp_path))
    assert os.path.exists(cert_path)
    assert os.path.exists(key_path)
    assert cert_path.endswith("holon-root-ca.crt")
    assert key_path.endswith("holon-root-ca.key")

    # Second call should reuse existing cert
    c2, k2 = generate_root_ca(cert_dir=str(tmp_path))
    assert c2 == cert_path
    assert k2 == key_path


def test_ca_generator_produces_parseable_cert_and_private_key_mode(tmp_path):
    cert_path, key_path = generate_root_ca(cert_dir=str(tmp_path))

    result = subprocess.run(["openssl", "x509", "-in", cert_path, "-noout", "-text"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    with open(cert_path) as handle:
        assert "BEGIN CERTIFICATE" in handle.read()

    assert stat.S_IMODE(os.stat(key_path).st_mode) == 0o600


def test_ca_generator_emits_ca_key_usage_basic_constraints_and_validity(tmp_path):
    """A CA without keyUsage is refused as a trust anchor by BoringSSL/Node, Go and strict OpenSSL."""
    cert_path, _ = generate_root_ca(cert_dir=str(tmp_path))
    text = _openssl_text(cert_path)

    assert "X509v3 Basic Constraints: critical" in text
    assert "CA:TRUE" in text
    assert "X509v3 Key Usage: critical" in text
    assert "Certificate Sign" in text
    assert "CRL Sign" in text
    assert "X509v3 Subject Key Identifier" in text
    # Not expiring inside the 30 day renewal window.
    assert _expires_within(cert_path) is False


def test_ca_generator_reuses_a_valid_cached_ca(tmp_path):
    cert_path, _ = generate_root_ca(cert_dir=str(tmp_path))
    cached_pem = _read_text(cert_path)

    cert_path_2, key_path_2 = generate_root_ca(cert_dir=str(tmp_path))

    assert cert_path_2 == cert_path
    assert _read_text(cert_path_2) == cached_pem
    assert stat.S_IMODE(os.stat(key_path_2).st_mode) == 0o600


def test_ca_generator_regenerates_near_expiry_cached_ca(tmp_path):
    """`openssl x509 -noout` exits 0 for expired certs, so expiry needs its own check + rotation."""
    cert_path, key_path = _make_ca_with_validity(tmp_path, days=5)
    stale_pem = _read_text(cert_path)
    assert _expires_within(cert_path) is True

    new_cert_path, new_key_path = generate_root_ca(cert_dir=str(tmp_path))

    assert new_cert_path == cert_path
    assert new_key_path == key_path
    assert _read_text(cert_path) != stale_pem
    assert _expires_within(cert_path) is False
    text = _openssl_text(cert_path)
    assert "X509v3 Key Usage: critical" in text
    assert "CA:TRUE" in text
    assert stat.S_IMODE(os.stat(key_path).st_mode) == 0o600


def test_ca_generator_raises_without_openssl(tmp_path, monkeypatch):
    monkeypatch.setattr(root_ca_openssl, "shutil", SimpleNamespace(which=lambda _: None))

    with pytest.raises(RuntimeError) as excinfo:
        generate_root_ca(cert_dir=str(tmp_path))

    assert "openssl" in str(excinfo.value).lower()
    assert "brew install openssl" in str(excinfo.value)
    # Nothing bogus may be cached when generation never started.
    assert os.listdir(str(tmp_path)) == []


def test_ca_generator_raises_on_openssl_failure_without_caching_junk(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["openssl"], stderr="req failed")

    monkeypatch.setattr(
        root_ca_openssl,
        "subprocess",
        SimpleNamespace(
            run=boom,
            CalledProcessError=subprocess.CalledProcessError,
            TimeoutExpired=subprocess.TimeoutExpired,
        ),
    )

    with pytest.raises(RuntimeError) as excinfo:
        generate_root_ca(cert_dir=str(tmp_path))

    assert "req failed" in str(excinfo.value)
    assert not os.path.exists(os.path.join(str(tmp_path), "holon-root-ca.crt"))


def test_ca_generator_detects_poisoned_cache(tmp_path):
    cert_path = tmp_path / "holon-root-ca.crt"
    key_path = tmp_path / "holon-root-ca.key"
    cert_path.write_text("-----BEGIN CERTIFICATE-----\nnot a real cert\n-----END CERTIFICATE-----\n")
    key_path.write_text("-----BEGIN PRIVATE KEY-----\nnope\n-----END PRIVATE KEY-----\n")

    with pytest.raises(RuntimeError) as excinfo:
        generate_root_ca(cert_dir=str(tmp_path))

    assert "not a parseable X.509 certificate" in str(excinfo.value)
    assert "Delete" in str(excinfo.value)
