"""HTTP-level readiness probe for the mitmproxy sidecar."""

import socket
import time

PROXY_READY_TIMEOUT_SECONDS = 15.0
PROXY_POLL_INTERVAL_SECONDS = 0.5
PROXY_PROBE_ATTEMPT_TIMEOUT_SECONDS = 2.0
# Reserved TLD (RFC 6761): the readiness request can never reach a real upstream.
PROXY_READINESS_HOST = "holon-proxy-readiness.invalid"
PROXY_READINESS_REQUEST = (
    f"GET http://{PROXY_READINESS_HOST}/ HTTP/1.1\r\nHost: {PROXY_READINESS_HOST}\r\nConnection: close\r\n\r\n"
).encode("ascii")


def proxy_answers_http(host: str, port: int, timeout: float) -> bool:
    """Send one proxy-style request and report whether the reply starts with an HTTP status line."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as connection:
            connection.sendall(PROXY_READINESS_REQUEST)
            data = b""
            while len(data) < len(b"HTTP/"):
                chunk = connection.recv(64)
                if not chunk:
                    break
                data += chunk
    except OSError:
        return False
    return data.startswith(b"HTTP/")


def wait_for_proxy(host: str, port: int, timeout: float = PROXY_READY_TIMEOUT_SECONDS) -> bool:
    """Poll a proxy until it answers an HTTP request; return False if it never does by the deadline.

    A bare TCP connect is not enough: Docker's userland proxy accepts connections on the published port
    before mitmproxy inside the container has bound its listener. The probe therefore sends a proxy
    request for an unresolvable host and waits for an HTTP status line (a 502 or 407 still means serving).
    """
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if proxy_answers_http(host, port, min(PROXY_PROBE_ATTEMPT_TIMEOUT_SECONDS, max(remaining, 0.05))):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(PROXY_POLL_INTERVAL_SECONDS, remaining))
