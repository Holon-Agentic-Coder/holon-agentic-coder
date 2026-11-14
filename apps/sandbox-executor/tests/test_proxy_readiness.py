"""Hermetic loopback tests for the mitmproxy sidecar HTTP readiness probe."""

import socket
import threading
import time

import pytest
from sandbox_executor.token_reduction import proxy_readiness


class _LoopbackListener:
    """Loopback TCP listener whose handler decides how each accepted connection is answered."""

    def __init__(self, handler):
        self.handler = handler
        self.requests: list[bytes] = []
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            with connection:
                connection.settimeout(1.0)
                try:
                    self.requests.append(connection.recv(1024))
                    self.handler(connection, len(self.requests))
                except OSError:
                    pass

    def close(self):
        self.server.close()


@pytest.fixture
def loopback_listener(monkeypatch):
    monkeypatch.setattr(proxy_readiness, "PROXY_POLL_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(proxy_readiness, "PROXY_PROBE_ATTEMPT_TIMEOUT_SECONDS", 0.2)
    listeners = []

    def start(handler):
        listeners.append(_LoopbackListener(handler))
        return listeners[-1]

    yield start
    for listener in listeners:
        listener.close()


def _answer_502(connection, _index):
    connection.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")


def test_wait_for_proxy_retries_silent_close_until_deadline(loopback_listener):
    listener = loopback_listener(lambda connection, index: None)

    started = time.monotonic()
    assert proxy_readiness.wait_for_proxy("127.0.0.1", listener.port, timeout=0.4) is False
    elapsed = time.monotonic() - started

    assert len(listener.requests) > 1
    assert 0.4 <= elapsed < 3.0


def test_wait_for_proxy_immediate_502_is_ready(loopback_listener):
    listener = loopback_listener(_answer_502)

    assert proxy_readiness.wait_for_proxy("127.0.0.1", listener.port, timeout=2.0) is True
    assert listener.requests[0].startswith(b"GET http://holon-proxy-readiness.invalid/ HTTP/1.1\r\n")


def test_wait_for_proxy_becomes_ready_once_proxy_answers(loopback_listener):
    listener = loopback_listener(lambda connection, index: index > 2 and _answer_502(connection, index))

    assert proxy_readiness.wait_for_proxy("127.0.0.1", listener.port, timeout=3.0) is True
    assert len(listener.requests) >= 3


@pytest.mark.parametrize(
    "handler",
    [
        pytest.param(lambda connection, index: time.sleep(0.5), id="silent-hold"),
        pytest.param(lambda connection, index: connection.sendall(b"SSH-2.0-OpenSSH\r\n"), id="non-http"),
    ],
)
def test_wait_for_proxy_rejects_listener_without_http_answer(loopback_listener, handler):
    listener = loopback_listener(handler)

    started = time.monotonic()
    assert proxy_readiness.wait_for_proxy("127.0.0.1", listener.port, timeout=0.3) is False
    assert time.monotonic() - started < 3.0
