"""Entry point to the barkapay API."""

from __future__ import annotations

import datetime
import email.utils
import platform
import random
import re
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlencode, urlsplit

from ._util import (
    as_dict,
    generate_request_id,
    json_decode,
    json_encode,
    normalize_country,
    query_value,
)
from ._version import __version__
from .errors import ApiError, ConfigurationError, NetworkError
from .http import Request, Response, Transport, UrllibTransport, assert_safe_url
from .resources import BalanceResource, Operators, PaymentLinks, Payments, Transfers

__all__ = ["PayHub"]

_DEFAULT_BASE_URL = "https://hub.barkapay.com"
#: The hub gives a gateway 30 s to answer; waiting a little longer lets the
#: SDK receive that verdict instead of giving up on an unknown outcome.
_DEFAULT_TIMEOUT_S = 40.0
_MAX_RETRIES_CAP = 10

_BASE_DELAY_S = 0.5
_MAX_DELAY_S = 8.0
#: A ``Retry-After`` longer than this is not slept through: the error is
#: raised with ``retry_after`` set and the caller decides.
_MAX_RETRY_AFTER_S = 60.0

#: Methods that are safe to send twice.
_SAFE_METHODS = frozenset({"GET", "HEAD"})
#: "Not now" answers. Only replayed for safe methods — see ``_can_retry``.
_TRANSIENT_STATUSES = frozenset({502, 503, 504})

# key_id:secret — printable ASCII, no spaces (so it can never break out of
# the Authorization header).
_TOKEN_RE = re.compile(r"[\x21-\x7e]+")


class PayHub:
    """Entry point to the barkapay API.

    Example::

        client = PayHub(api_key="key_id:secret", country="bf")
        payment = client.payments.create({
            "operator": "ORANGE",
            "phone_number": "74123456",
            "amount": 10000,
            "otp": "123456",
            "order": {"id": "ORDER-2026-001"},
        })

    A client holds no per-request state: create it once and share it between
    threads.
    """

    VERSION = __version__

    def __init__(
        self,
        api_key: str | None = None,
        *,
        key_id: str | None = None,
        secret: str | None = None,
        country: str = "",
        base_url: str = _DEFAULT_BASE_URL,
        max_retries: int = 2,
        timeout: float = _DEFAULT_TIMEOUT_S,
        transport: Transport | None = None,
    ) -> None:
        """
        :param api_key: the merchant key as ``"key_id:secret"`` (what you copy
            from the dashboard). Do NOT prefix it with ``Bearer`` — the SDK does that.
        :param key_id: alternatively, the key id…
        :param secret: …and the secret, passed separately.
        :param country: default country code (``"bf"``, ``"bj"``, ``"sn"``);
            overridable per call.
        :param base_url: API base URL. Must be ``https://`` (``http://`` is
            accepted for localhost only).
        :param max_retries: automatic retries of requests that are safe to
            repeat: any request answered 429 (it was not executed) and
            read-only (GET) requests that hit 502/503/504 or a network error.
            A request that creates or changes something is never replayed
            after a 5xx or a network error. ``0`` disables retries.
        :param timeout: seconds allowed for the connection and for each read.
        :param transport: a custom :class:`~payhub.http.Transport` (e.g. a fake in tests).
        """
        if isinstance(max_retries, bool) or not isinstance(max_retries, int):
            raise ConfigurationError("`max_retries` must be an integer.")

        self._api = _ApiClient(
            transport=transport if transport is not None else UrllibTransport(timeout),
            authorization=_build_authorization(api_key, key_id, secret),
            base_url=_normalize_base_url(base_url),
            default_country=_normalize_default_country(country),
            max_retries=min(max(0, max_retries), _MAX_RETRIES_CAP),
            user_agent=f"payhub-python/{__version__} python/{platform.python_version()}",
        )

        self.payments = Payments(self._api)
        self.payment_links = PaymentLinks(self._api)
        self.transfers = Transfers(self._api)
        self.balance = BalanceResource(self._api)
        self.operators = Operators(self._api)

    def __repr__(self) -> str:
        # Never include the credential.
        return f"PayHub(country={self._api.default_country!r}, base_url={self._api.base_url!r})"

    def ping(self, country: str | None = None) -> dict[str, Any]:
        """Liveness check (also confirms your key works)."""
        env = self._api.request("GET", "ping", country=country)
        return as_dict(env, "data") or {}

    def me(self, country: str | None = None) -> dict[str, Any]:
        """Current merchant + API application details (scopes, permissions)."""
        env = self._api.request("GET", "me", country=country)
        return as_dict(env, "data") or {}


class _Credential:
    """Holds the ``Authorization`` header value; never prints it."""

    __slots__ = ("_header",)

    def __init__(self, header: str) -> None:
        self._header = header

    def header(self) -> str:
        return self._header

    def __repr__(self) -> str:
        return "<credential [redacted]>"

    __str__ = __repr__


def _build_authorization(
    api_key: object,
    key_id: object,
    secret: object,
) -> _Credential:
    # Error messages below never echo the value that was passed in.
    if isinstance(api_key, str) and api_key.strip() != "":
        token = api_key.strip()
        # Forgive an accidental "Bearer " prefix so we never send "Bearer Bearer …".
        if token[:7].lower() == "bearer ":
            token = token[7:].strip()
        key_part, separator, secret_part = token.partition(":")
        if separator == "" or key_part.strip() == "" or secret_part.strip() == "":
            raise ConfigurationError(
                'Invalid `api_key`: expected "key_id:secret" (both parts, colon-separated).'
            )
        token = f"{key_part.strip()}:{secret_part.strip()}"
    elif (
        isinstance(key_id, str)
        and isinstance(secret, str)
        and key_id.strip() != ""
        and secret.strip() != ""
    ):
        token = f"{key_id.strip()}:{secret.strip()}"
    else:
        raise ConfigurationError(
            'Provide `api_key` ("key_id:secret") or both `key_id` and `secret`.'
        )

    if not _TOKEN_RE.fullmatch(token):
        raise ConfigurationError(
            "The API key contains spaces, control or non-ASCII characters. "
            "Copy it again from the dashboard."
        )
    return _Credential(f"Bearer {token}")


def _normalize_base_url(base_url: object) -> str:
    if not isinstance(base_url, str) or base_url.strip() == "":
        raise ConfigurationError("`base_url` must be a non-empty string.")
    url = base_url.strip().rstrip("/")
    assert_safe_url(url)
    parts = urlsplit(url)
    if parts.query or parts.fragment:
        raise ConfigurationError("`base_url` must not contain a query string or a fragment.")
    return url


def _normalize_default_country(country: object) -> str:
    if country is None or (isinstance(country, str) and country.strip() == ""):
        return ""
    return normalize_country(country)


def _parse_retry_after(value: str | None, now: float) -> float | None:
    """``Retry-After`` as seconds from now (delta-seconds or HTTP-date)."""
    if value is None:
        return None
    text = value.strip()
    if text == "":
        return None
    if re.fullmatch(r"[0-9]{1,9}(\.[0-9]{1,6})?", text):
        return float(text)
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        # "-0000": the date is UTC without a stated zone.
        when = when.replace(tzinfo=datetime.timezone.utc)
    return max(0.0, when.timestamp() - now)


class _ApiClient:
    """Internal transport: turns a logical call (method, path, country, body)
    into a signed HTTP request against ``{base_url}/{country}/v1/…``, retries
    what is safe to retry, and unwraps the standard response envelope (raising
    a typed :class:`ApiError` on error).

    Immutable after construction, hence safe to share between threads.
    """

    def __init__(
        self,
        transport: Transport,
        authorization: _Credential,
        base_url: str,
        default_country: str,
        max_retries: int,
        user_agent: str,
    ) -> None:
        self._transport = transport
        self._authorization = authorization
        self._base_url = base_url
        self._default_country = default_country
        self._max_retries = max_retries
        self._user_agent = user_agent
        # Seams for the test-suite (no real sleeping, deterministic jitter).
        self._sleep: Callable[[float], None] = time.sleep
        self._uniform: Callable[[float, float], float] = random.uniform

    def __repr__(self) -> str:
        return f"<payhub API client country={self._default_country!r} base_url={self._base_url!r}>"

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def default_country(self) -> str:
        return self._default_country

    def request(
        self,
        method: str,
        path: str,
        *,
        country: str | None = None,
        body: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        method = method.upper()
        request_id = generate_request_id()
        req = self._build_request(method, path, country, body, query, request_id)

        attempt = 0
        while True:
            try:
                response = self._transport(req)
            except NetworkError:
                # The request may have reached the API: only a read is replayed.
                if method in _SAFE_METHODS and attempt < self._max_retries:
                    self._sleep(self._backoff(attempt))
                    attempt += 1
                    continue
                raise

            headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
            retry_after = _parse_retry_after(headers.get("retry-after"), time.time())

            if attempt < self._max_retries and self._can_retry(method, response.status_code):
                delay = self._retry_delay(attempt, retry_after)
                if delay is not None:
                    self._sleep(delay)
                    attempt += 1
                    continue

            return self._handle(response, headers, retry_after, request_id)

    def _build_request(
        self,
        method: str,
        path: str,
        country: str | None,
        body: Mapping[str, Any] | None,
        query: Mapping[str, Any] | None,
        request_id: str,
    ) -> Request:
        if country is not None:
            cc = normalize_country(country)
        elif self._default_country != "":
            cc = self._default_country
        else:
            raise ConfigurationError(
                "No country given for this call and no default set on the client."
            )

        url = f"{self._base_url}/{cc}/v1/{path.lstrip('/')}"
        if query is not None:
            pairs = {str(k): query_value(v) for k, v in query.items() if v is not None}
            if pairs:
                url += "?" + urlencode(pairs)

        headers: dict[str, str] = {
            "Authorization": self._authorization.header(),
            "Accept": "application/json",
            "User-Agent": self._user_agent,
            "X-Request-Id": request_id,
        }

        payload: str | None = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload = json_encode(body)

        return Request(method, url, headers, payload)

    @staticmethod
    def _can_retry(method: str, status: int) -> bool:
        # 429: refused by the rate limiter before anything ran — safe for
        # every method. 502/503/504: the request may have been executed (a
        # 503 on a create can even carry a recorded FAILED payment), so only
        # reads are replayed. Money is never re-sent blindly.
        if status == 429:
            return True
        return method in _SAFE_METHODS and status in _TRANSIENT_STATUSES

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff with jitter: 0.25-0.5 s, 0.5-1 s, 1-2 s… (max 8 s)."""
        ceiling = min(_MAX_DELAY_S, _BASE_DELAY_S * (2**attempt))
        return self._uniform(ceiling / 2, ceiling)

    def _retry_delay(self, attempt: int, retry_after: float | None) -> float | None:
        """Seconds to wait before the next attempt; ``None`` = do not retry."""
        backoff = self._backoff(attempt)
        if retry_after is None:
            return backoff
        if retry_after > _MAX_RETRY_AFTER_S:
            return None
        # Never earlier than the API asked for (plus a little jitter so
        # parallel workers do not all come back on the same tick).
        return max(backoff, retry_after + self._uniform(0.0, 0.25))

    def _handle(
        self,
        response: Response,
        headers: Mapping[str, str],
        retry_after: float | None,
        request_id: str,
    ) -> dict[str, Any]:
        envelope = json_decode(response.body)
        status = response.status_code
        is_success = (
            200 <= status < 300
            and envelope is not None
            and envelope.get("status") not in ("error", "failed")
        )
        if is_success and envelope is not None:
            return envelope

        details: dict[str, Any] = dict(envelope) if envelope is not None else {}
        if not isinstance(details.get("request_id"), str):
            details["request_id"] = headers.get("x-request-id") or request_id

        if 200 <= status < 300 and envelope is None:
            # A 2xx whose body is not the JSON envelope is not an answer from
            # the API (proxy page, truncated body…): never build an empty
            # Payment out of it.
            details["code"] = "invalid_response"
            details["message"] = (
                f"Unexpected response from the API (HTTP {status}, body is not a JSON object)."
            )

        raise ApiError.from_envelope(status, details, headers=headers, retry_after=retry_after)
