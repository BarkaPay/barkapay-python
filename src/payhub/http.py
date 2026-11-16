"""Transport layer.

The SDK ships a zero-dependency :class:`UrllibTransport` built on the standard
library. Inject your own callable (anything matching :class:`Transport`) to plug
in a different HTTP stack — or to fake the network in tests.

Security properties of :class:`UrllibTransport`:

- TLS certificates and host names are always verified, with a context the
  transport builds itself (a process-wide ``ssl._create_default_https_context``
  override does not weaken it); TLS 1.2 is the minimum.
- Only ``https://`` URLs are opened (plain ``http://`` is accepted for
  loopback hosts only, for local mock servers). ``file:``, ``ftp:`` and
  ``data:`` URLs are refused.
- Redirects are never followed: the API does not redirect, and following one
  would hand the ``Authorization`` header to another host.
"""

from __future__ import annotations

import http.client
import ipaddress
import ssl
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from .errors import ConfigurationError, NetworkError

__all__ = ["Request", "Response", "Transport", "UrllibTransport"]

_SENSITIVE_HEADERS = frozenset({"authorization", "proxy-authorization", "x-api-key", "cookie"})
_LOOPBACK_NAMES = frozenset({"localhost"})

#: Hard cap on a response body. API responses are a few kilobytes; anything
#: larger than this is not the API.
MAX_RESPONSE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True, repr=False)
class Request:
    """An outbound HTTP request — a plain value object.

    ``repr()`` masks credential headers, so logging a request never leaks the
    API key.
    """

    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: str | None = None

    def __repr__(self) -> str:
        safe_headers = {
            name: ("[redacted]" if name.lower() in _SENSITIVE_HEADERS else value)
            for name, value in self.headers.items()
        }
        return (
            f"Request(method={self.method!r}, url={self.url!r}, "
            f"headers={safe_headers!r}, body={self.body!r})"
        )


@dataclass(frozen=True)
class Response:
    """A raw HTTP response returned by a :class:`Transport`."""

    status_code: int
    body: str
    headers: Mapping[str, str] = field(default_factory=dict)


@runtime_checkable
class Transport(Protocol):
    """Callable contract for the transport.

    Implement it as a function ``def transport(request: Request) -> Response`` or
    as any object with a matching ``__call__``. Return a :class:`Response` for
    every HTTP status (including 4xx/5xx) and raise
    :class:`~payhub.errors.NetworkError` when no response was obtained. A
    transport must not follow redirects and must verify TLS certificates.
    """

    def __call__(self, request: Request) -> Response:  # pragma: no cover - protocol
        ...


def is_loopback_host(host: str) -> bool:
    """``True`` for ``localhost`` and literal loopback addresses."""
    name = host.strip().lower().rstrip(".")
    if name in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(name.strip("[]")).is_loopback
    except ValueError:
        return False


def assert_safe_url(url: str) -> None:
    """Refuse any URL the SDK must never send credentials to.

    Allowed: ``https://host[:port]/…``, and ``http://`` for loopback hosts
    only. Everything else — other schemes, embedded credentials, missing host
    — raises :class:`~payhub.errors.ConfigurationError`.
    """
    if not isinstance(url, str) or any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in url):
        raise ConfigurationError("The URL must not contain spaces or control characters.")
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError as exc:
        raise ConfigurationError(f"Malformed URL: {exc}") from exc

    scheme = parts.scheme.lower()
    if host == "":
        raise ConfigurationError("The URL must be absolute, e.g. https://hub.barkapay.com.")
    if parts.username is not None or parts.password is not None:
        raise ConfigurationError("The URL must not embed credentials (user:password@host).")
    if scheme == "https":
        return
    if scheme == "http" and is_loopback_host(host):
        return
    raise ConfigurationError(
        "Only https:// URLs are allowed (http:// is accepted for localhost only): "
        "the API key must never travel in clear text."
    )


def _default_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


class UrllibTransport:
    """Zero-dependency HTTP transport built on ``urllib.request``.

    Safe to share between threads: every call opens its own connection and the
    transport keeps no per-request state.

    :param timeout: seconds allowed for the connection and for each read.
    :param context: a custom :class:`ssl.SSLContext` (e.g. a private CA
        bundle). It must keep certificate and host-name verification enabled.
    """

    def __init__(self, timeout: float = 40.0, *, context: ssl.SSLContext | None = None) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ConfigurationError("`timeout` must be a positive number of seconds.")
        if context is None:
            context = _default_ssl_context()
        elif context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
            raise ConfigurationError(
                "The SSL context must verify certificates and host names "
                "(verify_mode=CERT_REQUIRED, check_hostname=True)."
            )
        self.timeout = float(timeout)

        # Hand-built openers, deliberately minimal:
        #   - no redirect handler and no error processor: every status,
        #     3xx included, comes back as a plain response and is never
        #     followed;
        #   - no file:/ftp:/data: handlers.
        self._https_opener = urllib.request.OpenerDirector()
        self._https_opener.add_handler(urllib.request.ProxyHandler())
        self._https_opener.add_handler(urllib.request.HTTPSHandler(context=context))

        # Loopback http:// (local mock servers): direct, never through a proxy.
        self._loopback_opener = urllib.request.OpenerDirector()
        self._loopback_opener.add_handler(urllib.request.HTTPHandler())

    def __call__(self, request: Request) -> Response:
        assert_safe_url(request.url)
        is_https = urlsplit(request.url).scheme.lower() == "https"
        opener = self._https_opener if is_https else self._loopback_opener

        data = request.body.encode("utf-8") if request.body is not None else None
        req = urllib.request.Request(
            url=request.url,
            data=data,
            method=request.method,
        )
        for name, value in request.headers.items():
            req.add_header(name, value)

        try:
            with opener.open(req, timeout=self.timeout) as resp:
                headers = _lower_headers(resp.headers.items())
                body = _read_capped(resp, headers.get("content-length"))
                return Response(int(resp.status), body, headers)
        except urllib.error.HTTPError as exc:
            # Not raised by the openers above (no error processor); kept so a
            # status-carrying error from the standard library still surfaces
            # as a Response the API client maps to a typed exception.
            with exc:
                try:
                    body = _read_capped(exc)
                except (OSError, http.client.HTTPException) as read_exc:
                    raise NetworkError(_describe(read_exc)) from read_exc
                headers = _lower_headers(exc.headers.items()) if exc.headers else {}
                return Response(int(exc.code), body, headers)
        except urllib.error.URLError as exc:
            raise NetworkError(_describe(exc.reason)) from exc
        except (OSError, http.client.HTTPException) as exc:
            # Timeouts, resets, truncated bodies, TLS failures.
            raise NetworkError(_describe(exc)) from exc


class _Readable(Protocol):
    def read(self, amt: int = ..., /) -> bytes: ...


def _read_capped(source: _Readable, declared_length: str | None = None) -> str:
    raw = source.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise NetworkError("The response body exceeds the maximum accepted size.")
    # A sized read returns quietly when the peer closes early: check the body
    # against Content-Length so a cut-off answer is never mistaken for one.
    if (
        declared_length is not None
        and declared_length.isascii()
        and declared_length.isdigit()
        and len(raw) < int(declared_length)
    ):
        raise NetworkError("The connection was closed before the full response arrived.")
    return raw.decode("utf-8", errors="replace") if raw else ""


def _describe(reason: object) -> str:
    text = str(reason).strip()
    return text if text else type(reason).__name__


def _lower_headers(items: Iterable[tuple[str, str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, value in items:
        result[str(name).lower()] = str(value)
    return result
