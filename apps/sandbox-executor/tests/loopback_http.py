"""A real HTTP server on the loopback interface, for tests that exercise a real fetch against a hostile answer."""

import http.server
import socketserver
import threading
import unittest


class LoopbackHTTPServer(http.server.ThreadingHTTPServer):
    """An HTTP server on 127.0.0.1 for the tests of a real fetch; it never waits for a name lookup or a handler.

    ``HTTPServer.server_bind`` asks ``socket.getfqdn`` for the name of its own address, a reverse lookup that can take
    tens of seconds on a runner. Each request is answered on a daemon thread, so a handler that is still writing to a
    connection the client closed does not hold the shutdown back.
    """

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def serve_on_loopback(case: unittest.TestCase, handler: type) -> int:
    """Serve ``handler`` on a free loopback port until ``case`` ends; return the port."""
    server = LoopbackHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    case.addCleanup(server.server_close)
    case.addCleanup(server.shutdown)
    return server.server_port
