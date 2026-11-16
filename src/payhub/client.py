"""Entry point to the barkapay API."""

from __future__ import annotations

import datetime
import email.utils
import platform
import random
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

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
from .http import Request, Transport, UrllibTransport, assert_safe_url
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
#: Same, for a request that creates or changes something: money is not sent
#: from a call that has been asleep for a minute.
_MAX_WRITE_RETRY_AFTER_S = 10.0

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
        :param base_url: API base URL. With the built-in transport it must be
            ``https://`` (``http://`` is accepted for localhost only).
        :param max_retries: automatic retries of requests that are safe to
            repeat (at most 10): a request answered 429 by the rate limiter
            (it was not executed) and read-only (GET) requests that hit
            502/503/504 or a network error. A request that creates or changes
            something is never replayed after a timeout, a network error or
            a 5xx. ``0`` disables retries.
        :param timeout: seconds allowed for the connection and for each read
            (built-in transport only; a custom ``transport`` manages its own).
        :param transport: a custom :class:`~payhub.http.Transport` (e.g. a fake in tests).
        """
        if isinstance(max_retries, bool) or not isinstance(max_retries, int):
            raise ConfigurationError("`max_retries` must be an integer.")

        self._api = _ApiClient(
            transport=transport if transport is not None else UrllibTransport(timeout),
            authorization=_build_authorization(api_key, key_id, secret),
            base_url=_normalize_base_url(base_url, https_only=transport is None),
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
        return self._api.request("GET", "ping", country=country).data or {}

    def me(self, country: str | None = None) -> dict[str, Any]:
        """Current merchant + API application details (scopes, permissions)."""
        return self._api.request("GET", "me", country=country).data or {}


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


def _normalize_base_url(base_url: object, *, https_only: bool) -> str:
    """Validate ``base_url``.

    Always refused: anything that is not an absolute http(s) URL, embedded
    credentials, a query string or fragment, non-ASCII or control characters.
    With the built-in transport (``https_only``) clear-text ``http://`` is
    refused too, except for localhost. A custom transport owns its
    connection and may be pointed at a plain-http mock.
    """
    if not isinstance(base_url, str) or base_url.strip() == "":
        raise ConfigurationError("`base_url` must be a non-empty string.")
    url = base_url.strip().rstrip("/")
    if not url.isascii():
        raise ConfigurationError("`base_url` must contain ASCII characters only.")
    if "?" in url or "#" in url:
        raise ConfigurationError("`base_url` must not contain a query string or a fragment.")
    if https_only:
        assert_safe_url(url)
    else:
        assert_safe_url(url, allow_plain_http=True)
    return url


def _normalize_default_country(country: object) -> str:
    if country is None or (isinstance(country, str) and country.strip() == ""):
        return ""
    return normalize_country(country)


def _parse_retry_after(value: str | None, now: float) -> float | None:
    """``Retry-After`` as seconds from now (delta-seconds or HTTP-date).
    Anything unreadable is ``None`` — a header never makes a call fail."""
    if value is None:
        return None
    text = value.strip()
    if text == "":
        return None
    if re.fullmatch(r"[0-9]{1,9}(\.[0-9]{1,6})?", text):
        return float(text)
    try:
        when = email.utils.parsedate_to_datetime(text)
        if when.tzinfo is None:
            # "-0000": the date is UTC without a stated zone.
            when = when.replace(tzinfo=datetime.timezone.utc)
        return max(0.0, when.timestamp() - now)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


@dataclass(frozen=True)
class _ApiResult:
    """A successful (2xx) answer: the envelope, plus what is needed to report
    one that does not carry what the endpoint must return."""

    envelope: dict[str, Any]
    http_status: int
    headers: Mapping[str, str]
    request_id: str | None

    @property
    def data(self) -> dict[str, Any] | None:
        return as_dict(self.envelope, "data")

    def unexpected(self, what: str) -> ApiError:
        """The API answered 2xx — the request was accepted — but the body is
        not usable. For a create call this is NOT a refusal."""
        return ApiError(
            f"Unexpected response from the API (HTTP {self.http_status}): {what}. "
            "The request was accepted: after a create call, check with your "
            "order id before sending it again.",
            "invalid_response",
            self.http_status,
            self.request_id,
            data=self.data,
            headers=self.headers,
        )

    def resource(self, key: str) -> dict[str, Any]:
        """The object a successful call must return — never an empty stand-in."""
        resource = as_dict(self.data or {}, key)
        if resource is None:
            raise self.unexpected(f"`data.{key}` is missing")
        public_id = resource.get("public_id")
        if not isinstance(public_id, str) or public_id == "":
            raise self.unexpected(f"`data.{key}.public_id` is missing")
        return resource

    def rows(self, key: str) -> list[dict[str, Any]]:
        """The list a successful call must return — a malformed answer is
        never passed off as "no result"."""
        value = (self.data or {}).get(key)
        if not isinstance(value, list):
            raise self.unexpected(f"`data.{key}` is missing")
        rows: list[dict[str, Any]] = []
        for row in value:
            if not isinstance(row, dict):
                raise self.unexpected(f"`data.{key}` contains something that is not an object")
            rows.append(row)
        return rows


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
        # ``None`` = the standard library, looked up at call time.
        self._sleep: Callable[[float], None] | None = None
        self._uniform: Callable[[float, float], float] | None = None

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
        allow_empty: bool = False,
    ) -> _ApiResult:
        """Send one logical call.

        :param allow_empty: accept a 2xx with an empty body (endpoints that
            return nothing).
        """
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
                    self._pause(self._backoff(attempt))
                    attempt += 1
                    continue
                raise

            status = response.status_code
            headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
            envelope = json_decode(response.body)

            if (
                200 <= status < 300
                and envelope is not None
                and envelope.get("status") not in ("error", "failed")
            ):
                return _ApiResult(envelope, status, headers, _request_id(envelope, headers))

            if 200 <= status < 300 and allow_empty and response.body.strip() == "":
                return _ApiResult({}, status, headers, headers.get("x-request-id") or request_id)

            retry_after = _parse_retry_after(headers.get("retry-after"), time.time())

            if attempt < self._max_retries and self._can_retry(method, status, envelope):
                delay = self._retry_delay(attempt, retry_after, method)
                if delay is not None:
                    self._pause(delay)
                    attempt += 1
                    continue

            raise self._error(status, envelope, headers, retry_after, request_id)

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

        # Everything that can fail on caller input happens BEFORE the
        # credential enters this frame, so a traceback (or an error reporter
        # capturing local variables) never holds the key.
        payload = json_encode(body) if body is not None else None

        return Request(
            method,
            url,
            {
                "Authorization": self._authorization.header(),
                "Accept": "application/json",
                "User-Agent": self._user_agent,
                "X-Request-Id": request_id,
                **({"Content-Type": "application/json"} if payload is not None else {}),
            },
            payload,
        )

    @staticmethod
    def _can_retry(method: str, status: int, envelope: Mapping[str, Any] | None) -> bool:
        # Reads: 429 and "not now" answers are replayed.
        if method in _SAFE_METHODS:
            return status == 429 or status in _TRANSIENT_STATUSES
        # Writes: money is never re-sent blindly. The ONLY replayed answer is
        # the rate limiter's own refusal — issued before anything ran — and
        # only when it carries no trace of a recorded operation. 5xx answers
        # (a 503 on a create can carry a recorded FAILED payment) are final.
        if status != 429 or envelope is None:
            return False
        return envelope.get("code") == "too_many_requests" and not envelope.get("data")

    def _pause(self, seconds: float) -> None:
        (self._sleep or time.sleep)(seconds)

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff with jitter: 0.25-0.5 s, 0.5-1 s, 1-2 s… (max 8 s)."""
        ceiling = min(_MAX_DELAY_S, _BASE_DELAY_S * (2**attempt))
        return (self._uniform or random.uniform)(ceiling / 2, ceiling)

    def _retry_delay(self, attempt: int, retry_after: float | None, method: str) -> float | None:
        """Seconds to wait before the next attempt; ``None`` = do not retry."""
        backoff = self._backoff(attempt)
        if retry_after is None:
            return backoff
        limit = _MAX_RETRY_AFTER_S if method in _SAFE_METHODS else _MAX_WRITE_RETRY_AFTER_S
        if retry_after > limit:
            return None
        # Never earlier than the API asked for (plus a little jitter so
        # parallel workers do not all come back on the same tick).
        return max(backoff, retry_after + (self._uniform or random.uniform)(0.0, 0.25))

    @staticmethod
    def _error(
        status: int,
        envelope: Mapping[str, Any] | None,
        headers: Mapping[str, str],
        retry_after: float | None,
        request_id: str,
    ) -> ApiError:
        details: dict[str, Any] = dict(envelope) if envelope is not None else {}
        if not isinstance(details.get("request_id"), str):
            details["request_id"] = headers.get("x-request-id") or request_id

        if 200 <= status < 300 and envelope is None:
            # A 2xx whose body is not the JSON envelope is not an answer from
            # the API (proxy page, truncated body…): never build an empty
            # Payment out of it — and never call it a refusal either.
            details["code"] = "invalid_response"
            details["message"] = (
                f"Unexpected response from the API (HTTP {status}): the body is not a "
                "JSON object. The request was accepted: after a create call, check "
                "with your order id before sending it again."
            )

        return ApiError.from_envelope(status, details, headers=headers, retry_after=retry_after)


def _request_id(envelope: Mapping[str, Any], headers: Mapping[str, str]) -> str | None:
    value = envelope.get("request_id")
    if isinstance(value, str) and value != "":
        return value
    return headers.get("x-request-id")
