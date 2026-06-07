"""Unit tests for guardianctl's IPC client framing (cli.ipc_call).

Exercises the 4-byte length-prefixed protocol against a throwaway Unix-socket
server, plus the connection-error branches - none of which had any coverage.
"""

import json
import os
import socket
import tempfile
import threading

import pytest

from guardianctl import cli


class _CannedServer:
    """Minimal Unix-socket server speaking guardianctl's length-prefixed protocol."""

    def __init__(self, path, response: bytes):
        self.path = path
        self.response = response
        self.received = None
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(path)
        self._srv.listen(1)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        try:
            conn, _ = self._srv.accept()
            with conn:
                length = int.from_bytes(conn.recv(4), "big")
                self.received = conn.recv(length).decode()
                conn.sendall(len(self.response).to_bytes(4, "big"))
                conn.sendall(self.response)
        except OSError:
            pass

    def close(self):
        self._srv.close()


@pytest.fixture
def canned_server(monkeypatch):
    tmp = tempfile.mkdtemp()
    sock_path = os.path.join(tmp, "daemon.sock")
    servers = []

    def _start(response):
        srv = _CannedServer(sock_path, response.encode())
        servers.append(srv)
        monkeypatch.setattr(cli, "IPC_SOCKET", sock_path)
        return srv

    yield _start
    for srv in servers:
        srv.close()


def test_ipc_call_roundtrip(canned_server):
    srv = canned_server(json.dumps({"kids": ["kid1", "kid2"]}))
    result = cli.ipc_call("list_kids")
    assert srv.received == "list_kids"
    assert json.loads(result) == {"kids": ["kid1", "kid2"]}


def test_ipc_call_appends_argument(canned_server):
    srv = canned_server(json.dumps({"ok": True}))
    cli.ipc_call("get_quota", "kid1")
    assert srv.received == "get_quota kid1"


def test_ipc_call_socket_missing_returns_json_error(monkeypatch):
    monkeypatch.setattr(cli, "IPC_SOCKET", "/nonexistent/guardian-test.sock")
    result = json.loads(cli.ipc_call("list_kids"))
    assert "error" in result
    assert "not found" in result["error"].lower()
