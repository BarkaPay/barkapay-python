"""The built-in urllib transport.

The socket-level tests talk to throw-away servers bound to 127.0.0.1 — they
exercise the real standard-library behaviour and never leave the machine.
"""

from __future__ import annotations

import json
import socket
import ssl
import threading
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from payhub import ApiError, ConfigurationError, NetworkError, PayHub, UrllibTransport
from payhub.http import MAX_RESPONSE_BYTES, Request, assert_safe_url, is_loopback_host

KEY = "pk_live_abc123:sk_live_SUPERSECRET"


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.lock = threading.Lock()


def _serve(handler_factory: Any) -> tuple[HTTPServer, _Recorder]:
    recorder = _Recorder()

    class Handler(BaseHTTPRequestHandler):
        def _record(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            with recorder.lock:
                recorder.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": body,
                    }
                )

        def _reply(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _handle(self) -> None:
            self._record()
            handler_factory(self)

        do_GET = do_POST = do_PATCH = do_DELETE = _handle

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    ).start()
    return server, recorder


@pytest.fixture
def servers() -> Iterator[list[HTTPServer]]:
    started: list[HTTPServer] = []
    yield started
    for server in started:
        server.shutdown()
        server.server_close()


def _handlers(opener: urllib.request.OpenerDirector) -> list[Any]:
    return list(getattr(opener, "handlers", []))


def _url(server: HTTPServer) -> str:
    return f"http://127.0.0.1:{server.server_port}"


ENVELOPE = json.dumps(
    {"status": "success", "code": "success", "data": {"version": "2.0.0"}}
).encode()


# --- URL policy -----------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://hub.barkapay.com/bf/v1/ping",
        "https://hub.barkapay.com:8443/bf/v1/ping?page=1",
        "http://localhost:8080/bf/v1/ping",
        "http://127.0.0.1/bf/v1/ping",
        "http://127.8.9.10:1/x",
        "http://[::1]:9000/bf/v1/ping",
        "HTTPS://HUB.BARKAPAY.COM/bf/v1/ping",
    ],
)
def test_safe_urls_are_accepted(url: str) -> None:
    assert_safe_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://hub.barkapay.com/bf/v1/ping",
        "http://localhost.evil.example/bf/v1/ping",
        "http://127.0.0.1.evil.example/x",
        "http://192.168.1.10/x",
        "http://0.0.0.0/x",
        "file:///etc/passwd",
        "file://localhost/etc/passwd",
        "ftp://localhost/x",
        "data:text/plain,hi",
        "gopher://localhost/x",
        "https://user:pass@hub.barkapay.com/x",
        "https://user@hub.barkapay.com/x",
        "https:///bf/v1/ping",
        "/bf/v1/ping",
        "https://hub.barkapay.com/bf/v1/ping\r\nX-Injected: 1",
        "https://hub.barkapay.com/ bf",
        "https://hub.barkapay.com:port/x",
    ],
)
def test_unsafe_urls_are_refused(url: str) -> None:
    with pytest.raises(ConfigurationError):
        assert_safe_url(url)
    with pytest.raises(ConfigurationError):
        UrllibTransport()(Request("GET", url, {"Authorization": f"Bearer {KEY}"}))


def test_loopback_detection() -> None:
    assert is_loopback_host("localhost") and is_loopback_host("LOCALHOST.")
    assert is_loopback_host("127.0.0.1") and is_loopback_host("::1")
    assert not is_loopback_host("localhost.example.com")
    assert not is_loopback_host("10.0.0.1")
    assert not is_loopback_host("")


# --- TLS ------------------------------------------------------------------------


def test_default_tls_context_verifies_certificates_and_hostnames() -> None:
    transport = UrllibTransport()
    handlers = [
        h for h in _handlers(transport._https_opener) if isinstance(h, urllib.request.HTTPSHandler)
    ]
    assert len(handlers) == 1
    context = handlers[0]._context  # type: ignore[attr-defined]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_tls_verification_survives_a_global_unverified_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The usual "fix" for certificate errors found on the web.
    monkeypatch.setattr(ssl, "_create_default_https_context", ssl._create_unverified_context)
    transport = UrllibTransport()
    handler = next(
        h for h in _handlers(transport._https_opener) if isinstance(h, urllib.request.HTTPSHandler)
    )
    context = handler._context  # type: ignore[attr-defined]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_an_unverified_context_is_refused() -> None:
    unverified = ssl.create_default_context()
    unverified.check_hostname = False
    unverified.verify_mode = ssl.CERT_NONE
    with pytest.raises(ConfigurationError):
        UrllibTransport(context=unverified)

    no_hostname = ssl.create_default_context()
    no_hostname.check_hostname = False
    with pytest.raises(ConfigurationError):
        UrllibTransport(context=no_hostname)


def test_a_verifying_custom_context_is_accepted() -> None:
    UrllibTransport(context=ssl.create_default_context())


def test_a_self_signed_server_is_rejected_as_a_network_error(servers: list[HTTPServer]) -> None:
    # A TLS endpoint whose certificate cannot be verified: here, a plain TCP
    # listener answering garbage to the TLS handshake.
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def answer() -> None:
        try:
            conn, _ = listener.accept()
            conn.recv(1024)
            conn.sendall(b"HTTP/1.1 200 OK\r\n\r\n")
            conn.close()
        except OSError:
            pass

    threading.Thread(target=answer, daemon=True).start()
    try:
        with pytest.raises(NetworkError):
            UrllibTransport(timeout=5)(
                Request("GET", f"https://localhost:{listener.getsockname()[1]}/bf/v1/ping")
            )
    finally:
        listener.close()


@pytest.mark.parametrize("timeout", [0, -1, True, "30", None])
def test_timeout_must_be_a_positive_number(timeout: object) -> None:
    with pytest.raises(ConfigurationError):
        UrllibTransport(timeout=timeout)  # type: ignore[arg-type]


def test_default_timeouts() -> None:
    assert UrllibTransport().timeout == 40.0
    assert UrllibTransport(12).timeout == 12.0
    client = PayHub(api_key=KEY, country="bf", timeout=7.5)
    assert isinstance(client._api._transport, UrllibTransport)
    assert client._api._transport.timeout == 7.5


# --- no dangerous handlers ---------------------------------------------------------


def test_openers_have_no_redirect_file_ftp_or_data_handlers() -> None:
    transport = UrllibTransport()
    forbidden = (
        urllib.request.HTTPRedirectHandler,
        urllib.request.FileHandler,
        urllib.request.FTPHandler,
        urllib.request.DataHandler,
        urllib.request.HTTPCookieProcessor,
    )
    for opener in (transport._https_opener, transport._loopback_opener):
        assert not any(isinstance(h, forbidden) for h in _handlers(opener))
    # Loopback traffic never goes through a proxy.
    assert not any(
        isinstance(h, urllib.request.ProxyHandler) for h in _handlers(transport._loopback_opener)
    )


# --- real exchanges on loopback ------------------------------------------------------


def test_round_trip_sends_headers_and_body_and_reads_the_envelope(
    servers: list[HTTPServer],
) -> None:
    server, recorder = _serve(lambda h: h._reply(201, ENVELOPE, {"X-Request-Id": "rid-1"}))
    servers.append(server)

    response = UrllibTransport(timeout=5)(
        Request(
            "POST",
            f"{_url(server)}/bf/v1/payments",
            {
                "Authorization": f"Bearer {KEY}",
                "Content-Type": "application/json",
                "User-Agent": "payhub-python/test",
            },
            '{"amount":10000,"note":"Café"}',
        )
    )

    assert response.status_code == 201
    assert json.loads(response.body)["data"]["version"] == "2.0.0"
    assert response.headers["x-request-id"] == "rid-1"
    seen = recorder.requests[0]
    assert seen["method"] == "POST"
    assert seen["path"] == "/bf/v1/payments"
    assert seen["headers"]["authorization"] == f"Bearer {KEY}"
    assert seen["headers"]["content-type"] == "application/json"
    assert seen["headers"]["user-agent"] == "payhub-python/test"
    assert seen["body"] == '{"amount":10000,"note":"Café"}'.encode()


@pytest.mark.parametrize("status", [400, 401, 404, 405, 422, 424, 429, 500, 503])
def test_error_statuses_come_back_as_responses_with_their_body(
    servers: list[HTTPServer], status: int
) -> None:
    body = json.dumps({"status": "failed", "code": "x", "message": "m"}).encode()
    server, _ = _serve(lambda h: h._reply(status, body, {"Retry-After": "3"}))
    servers.append(server)

    response = UrllibTransport(timeout=5)(Request("GET", f"{_url(server)}/bf/v1/ping"))
    assert response.status_code == status
    assert json.loads(response.body)["code"] == "x"
    assert response.headers["retry-after"] == "3"


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_redirects_are_never_followed_and_the_key_never_reaches_another_host(
    servers: list[HTTPServer], status: int, method: str
) -> None:
    thief, stolen = _serve(lambda h: h._reply(200, ENVELOPE))
    servers.append(thief)
    api, seen = _serve(
        lambda h: h._reply(status, b"", {"Location": f"http://localhost:{thief.server_port}/steal"})
    )
    servers.append(api)

    client = PayHub(api_key=KEY, country="bf", base_url=_url(api), max_retries=0, timeout=5)
    with pytest.raises(ApiError) as exc_info:
        if method == "GET":
            client.ping()
        else:
            client.payments.create({"operator": "ORANGE"})

    assert exc_info.value.http_status == status
    assert len(seen.requests) == 1
    assert stolen.requests == []  # the Authorization header went nowhere else


def test_methods_patch_and_delete_are_sent_as_such(servers: list[HTTPServer]) -> None:
    server, recorder = _serve(lambda h: h._reply(200, ENVELOPE))
    servers.append(server)
    transport = UrllibTransport(timeout=5)
    transport(Request("PATCH", f"{_url(server)}/bf/v1/payments/links/PL-1", {}, '{"title":"x"}'))
    transport(Request("DELETE", f"{_url(server)}/bf/v1/payments/links/PL-1"))
    transport(Request("POST", f"{_url(server)}/bf/v1/payments/links/PL-1/pause"))
    assert [r["method"] for r in recorder.requests] == ["PATCH", "DELETE", "POST"]
    assert recorder.requests[0]["body"] == b'{"title":"x"}'
    assert recorder.requests[1]["body"] == b""


def test_connection_refused_is_a_network_error(servers: list[HTTPServer]) -> None:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()  # nothing listens here any more
    with pytest.raises(NetworkError):
        UrllibTransport(timeout=2)(Request("GET", f"http://127.0.0.1:{port}/bf/v1/ping"))


def test_a_read_timeout_is_a_network_error() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        with pytest.raises(NetworkError):
            UrllibTransport(timeout=0.3)(
                Request("GET", f"http://127.0.0.1:{listener.getsockname()[1]}/bf/v1/ping")
            )
    finally:
        listener.close()


def test_a_truncated_response_is_a_network_error() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def answer() -> None:
        try:
            conn, _ = listener.accept()
            conn.recv(4096)
            conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 500\r\n\r\n{"status":')
            conn.close()
        except OSError:
            pass

    threading.Thread(target=answer, daemon=True).start()
    try:
        with pytest.raises(NetworkError):
            UrllibTransport(timeout=5)(
                Request("GET", f"http://127.0.0.1:{listener.getsockname()[1]}/bf/v1/ping")
            )
    finally:
        listener.close()


def test_an_oversized_response_is_refused(servers: list[HTTPServer]) -> None:
    huge = b"x" * (MAX_RESPONSE_BYTES + 10)
    server, _ = _serve(lambda h: h._reply(200, huge))
    servers.append(server)
    with pytest.raises(NetworkError):
        UrllibTransport(timeout=10)(Request("GET", f"{_url(server)}/bf/v1/ping"))


def test_invalid_utf8_in_a_body_does_not_crash(servers: list[HTTPServer]) -> None:
    server, _ = _serve(lambda h: h._reply(502, b"\xff\xfe<html>"))
    servers.append(server)
    response = UrllibTransport(timeout=5)(Request("GET", f"{_url(server)}/bf/v1/ping"))
    assert response.status_code == 502
    assert "<html>" in response.body


def test_end_to_end_through_the_client(servers: list[HTTPServer]) -> None:
    def handler(h: Any) -> None:
        if h.path == "/bf/v1/balance":
            h._reply(
                200,
                json.dumps(
                    {
                        "status": "success",
                        "code": "success",
                        "data": {
                            "country": "BF",
                            "currency": "XOF",
                            "available": "1500.00",
                            "total": "1500.00",
                            "holds": "0.00",
                        },
                    }
                ).encode(),
            )
        else:
            h._reply(404, json.dumps({"status": "error", "code": "resource_not_found"}).encode())

    server, recorder = _serve(handler)
    servers.append(server)
    client = PayHub(api_key=KEY, country="bf", base_url=_url(server), timeout=5)

    assert client.balance.get().available == "1500.00"
    seen = recorder.requests[0]["headers"]
    assert seen["authorization"] == f"Bearer {KEY}"
    assert seen["accept"] == "application/json"
    assert seen["user-agent"].startswith("payhub-python/1.1.1 python/")
    assert len(seen["x-request-id"]) == 36
