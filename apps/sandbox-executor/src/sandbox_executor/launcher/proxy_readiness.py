import socket
import time

PROXY_READY_TIMEOUT_SECONDS = 15.0
PROXY_POLL_INTERVAL_SECONDS = 0.5
PROXY_PROBE_ATTEMPT_TIMEOUT_SECONDS = 2.0
PROXY_READINESS_HOST = "holon-proxy-readiness.invalid"
PROXY_READINESS_REQUEST = (
    f"GET http://{PROXY_READINESS_HOST}/ HTTP/1.1\r\nHost: {PROXY_READINESS_HOST}\r\nConnection: close\r\n\r\n"
).encode("ascii")

_HTTP_STATUS_PREFIX = b"HTTP/"
_PROBE_RECV_CHUNK_BYTES = 64
_MIN_PROBE_TIMEOUT_SECONDS = 0.05


def proxy_answers_http(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout) as connection:
            connection.sendall(PROXY_READINESS_REQUEST)
            data = b""
            while len(data) < len(_HTTP_STATUS_PREFIX):
                chunk = connection.recv(_PROBE_RECV_CHUNK_BYTES)
                if not chunk:
                    break
                data += chunk
    except OSError:
        return False
    return data.startswith(_HTTP_STATUS_PREFIX)


def wait_for_proxy(host: str, port: int, timeout: float = PROXY_READY_TIMEOUT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        attempt_timeout = min(PROXY_PROBE_ATTEMPT_TIMEOUT_SECONDS, max(remaining, _MIN_PROBE_TIMEOUT_SECONDS))
        if proxy_answers_http(host, port, attempt_timeout):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(PROXY_POLL_INTERVAL_SECONDS, remaining))
