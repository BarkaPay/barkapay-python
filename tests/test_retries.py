"""Retry policy: a request is replayed only when replaying it cannot move
money twice."""

from __future__ import annotations

import email.utils
import time
from collections.abc import Callable
from typing import Any

import pytest

from payhub import (
    ConfigurationError,
    NetworkError,
    PayHub,
    RateLimitError,
    ServerError,
    ServiceUnavailableError,
)
from payhub.http import Response
from tests import samples
from tests.support import FakeTransport, json_response, make_client, ok

BUSY = json_response(503, {"status": "error", "code": "service_unavailable", "message": "busy"})
THROTTLED = json_response(
    429, {"status": "error", "code": "too_many_requests", "message": "slow down"}
)

Call = Callable[[PayHub], Any]

# Every call that creates or changes something.
WRITES: dict[str, Call] = {
    "payments.create": lambda c: c.payments.create({"operator": "ORANGE"}),
    "payments.confirm_otp": lambda c: c.payments.confirm_otp(samples.PAYMENT_ID, "123456"),
    "payments.resend_otp": lambda c: c.payments.resend_otp(samples.PAYMENT_ID),
    "transfers.create": lambda c: c.transfers.create({"operator": "ORANGE"}),
    "payment_links.create": lambda c: c.payment_links.create({"title": "x"}),
    "payment_links.update": lambda c: c.payment_links.update(samples.LINK_ID, {"title": "x"}),
    "payment_links.cancel": lambda c: c.payment_links.cancel(samples.LINK_ID),
    "payment_links.pause": lambda c: c.payment_links.pause(samples.LINK_ID),
    "payment_links.resume": lambda c: c.payment_links.resume(samples.LINK_ID),
}

READS: dict[str, Call] = {
    "ping": lambda c: c.ping(),
    "me": lambda c: c.me(),
    "balance.get": lambda c: c.balance.get(),
    "operators.info": lambda c: c.operators.info(),
    "operators.availability": lambda c: c.operators.availability(),
    "payments.list": lambda c: c.payments.list(),
    "payments.get": lambda c: c.payments.get(samples.PAYMENT_ID),
    "transfers.list": lambda c: c.transfers.list(),
    "transfers.get": lambda c: c.transfers.get(samples.TRANSFER_ID),
    "payment_links.list": lambda c: c.payment_links.list(),
    "payment_links.get": lambda c: c.payment_links.get(samples.LINK_ID),
    "payment_links.list_payments": lambda c: c.payment_links.list_payments(samples.LINK_ID),
}

EVERYTHING = ok(
    {
        "payment": samples.payment(),
        "payments": [samples.payment()],
        "transfer": samples.transfer(),
        "transfers": [samples.transfer()],
        "payment_link": samples.payment_link(),
        "payment_links": [samples.payment_link()],
    }
)


# --- writes are never replayed after an ambiguous outcome ----------------------


@pytest.mark.parametrize("name", sorted(WRITES))
@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_a_write_is_never_replayed_after_a_5xx(name: str, status: int) -> None:
    failure = json_response(status, {"status": "error", "code": "service_unavailable"})
    client, transport, sleeps = make_client([failure, EVERYTHING], max_retries=3)

    with pytest.raises((ServiceUnavailableError, ServerError)):
        WRITES[name](client)

    assert len(transport.requests) == 1
    assert sleeps == []


@pytest.mark.parametrize("name", sorted(WRITES))
def test_a_write_is_never_replayed_after_a_network_error(name: str) -> None:
    client, transport, sleeps = make_client(
        [NetworkError("The read operation timed out"), EVERYTHING], max_retries=3
    )

    with pytest.raises(NetworkError):
        WRITES[name](client)

    assert len(transport.requests) == 1
    assert sleeps == []


def test_a_503_on_create_keeps_the_recorded_failed_payment() -> None:
    # The gateway records the attempt as FAILED and answers 503: replaying the
    # POST would create a second payment.
    client, transport, _ = make_client(
        [
            json_response(
                503,
                {
                    "status": "failed",
                    "code": "operator_unavailable",
                    "message": "Operator service unavailable.",
                    "data": {"payment": samples.failed_payment(reason_code="operator_unavailable")},
                },
            )
        ],
        max_retries=2,
    )
    with pytest.raises(ServiceUnavailableError) as exc_info:
        client.payments.create({"operator": "ORANGE"})

    assert len(transport.requests) == 1
    payment = exc_info.value.payment
    assert payment is not None
    assert payment.public_id == samples.PAYMENT_ID
    assert exc_info.value.reason_code == "operator_unavailable"


# --- a 429 was not executed: safe for every method ------------------------------


@pytest.mark.parametrize("name", sorted({**WRITES, **READS}))
def test_a_429_is_retried_for_every_method(name: str) -> None:
    client, transport, sleeps = make_client([THROTTLED, EVERYTHING], max_retries=1)
    {**WRITES, **READS}[name](client)
    assert len(transport.requests) == 2
    assert len(sleeps) == 1


def test_retry_after_is_honoured_never_earlier() -> None:
    throttled = json_response(
        429,
        {"status": "error", "code": "too_many_requests", "message": "slow down"},
        {"Retry-After": "17", "X-RateLimit-Limit": "30", "X-RateLimit-Remaining": "0"},
    )
    client, transport, sleeps = make_client([throttled, ok({"payments": []})], max_retries=2)
    client.payments.list()
    assert len(transport.requests) == 2
    assert len(sleeps) == 1
    assert 17.0 <= sleeps[0] <= 17.25


def test_retry_after_http_date_is_understood() -> None:
    header = email.utils.formatdate(time.time() + 20, usegmt=True)
    throttled = json_response(
        429, {"status": "error", "code": "too_many_requests"}, {"retry-after": header}
    )
    client, _, sleeps = make_client([throttled, ok({})], max_retries=1)
    client.ping()
    assert 17.0 <= sleeps[0] <= 21.0


def test_a_long_retry_after_is_not_slept_through() -> None:
    throttled = json_response(
        429,
        {"status": "error", "code": "too_many_requests", "message": "quota"},
        {"Retry-After": "3600", "X-RateLimit-Limit": "30", "X-RateLimit-Remaining": "0"},
    )
    client, transport, sleeps = make_client([throttled, ok({})], max_retries=3)
    with pytest.raises(RateLimitError) as exc_info:
        client.ping()
    assert len(transport.requests) == 1
    assert sleeps == []
    assert exc_info.value.retry_after == 3600.0
    assert exc_info.value.headers["x-ratelimit-limit"] == "30"
    assert exc_info.value.headers["x-ratelimit-remaining"] == "0"


def test_rate_limit_error_after_retries_are_exhausted() -> None:
    client, transport, sleeps = make_client([THROTTLED] * 3, max_retries=2)
    with pytest.raises(RateLimitError) as exc_info:
        client.payments.create({"operator": "ORANGE"})
    assert len(transport.requests) == 3
    assert len(sleeps) == 2
    assert exc_info.value.retry_after is None


@pytest.mark.parametrize("header", ["soon", "-5", "", "1e3", "12abc"])
def test_garbage_retry_after_falls_back_to_backoff(header: str) -> None:
    throttled = json_response(
        429, {"status": "error", "code": "too_many_requests"}, {"Retry-After": header}
    )
    client, _, sleeps = make_client([throttled, ok({})], max_retries=1)
    client.ping()
    assert sleeps == [0.5]


# --- reads are retried on transient failures ------------------------------------


@pytest.mark.parametrize("name", sorted(READS))
@pytest.mark.parametrize("status", [502, 503, 504])
def test_a_read_is_retried_after_a_transient_5xx(name: str, status: int) -> None:
    failure = Response(status, "<html>upstream</html>")
    client, transport, sleeps = make_client([failure, EVERYTHING], max_retries=1)
    READS[name](client)
    assert len(transport.requests) == 2
    assert len(sleeps) == 1


@pytest.mark.parametrize("name", sorted(READS))
def test_a_read_is_retried_after_a_network_error(name: str) -> None:
    client, transport, sleeps = make_client(
        [NetworkError("connection reset"), NetworkError("timed out"), EVERYTHING], max_retries=2
    )
    READS[name](client)
    assert len(transport.requests) == 3
    assert len(sleeps) == 2


def test_a_plain_500_is_not_retried_even_for_a_read() -> None:
    client, transport, sleeps = make_client(
        [
            json_response(500, {"status": "error", "code": "internal_error", "message": "boom"}),
            ok({}),
        ],
        max_retries=2,
    )
    with pytest.raises(ServerError):
        client.ping()
    assert len(transport.requests) == 1
    assert sleeps == []


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 424])
def test_client_errors_are_never_retried(status: int) -> None:
    client, transport, sleeps = make_client(
        [json_response(status, {"status": "failed", "code": "x", "message": "no"}), ok({})],
        max_retries=3,
    )
    with pytest.raises(Exception):  # noqa: B017 - the class is covered by test_error_mapping
        client.ping()
    assert len(transport.requests) == 1
    assert sleeps == []


def test_network_error_surfaces_after_read_retries_are_exhausted() -> None:
    client, transport, _ = make_client([NetworkError("down")] * 3, max_retries=2)
    with pytest.raises(NetworkError):
        client.ping()
    assert len(transport.requests) == 3


def test_max_retries_zero_disables_every_retry() -> None:
    client, transport, sleeps = make_client([BUSY, ok({})], max_retries=0)
    with pytest.raises(ServiceUnavailableError):
        client.ping()
    client2, transport2, _ = make_client([THROTTLED, ok({})], max_retries=0)
    with pytest.raises(RateLimitError):
        client2.ping()
    assert len(transport.requests) == 1
    assert len(transport2.requests) == 1
    assert sleeps == []


def test_503_retry_after_from_the_api_is_honoured_on_reads() -> None:
    busy = json_response(
        503, {"status": "error", "code": "service_unavailable"}, {"Retry-After": "30"}
    )
    client, _, sleeps = make_client([busy, ok({})], max_retries=1)
    client.ping()
    assert 30.0 <= sleeps[0] <= 30.25


# --- backoff ---------------------------------------------------------------------


def test_backoff_is_exponential_capped_and_jittered() -> None:
    client, _, _ = make_client([])
    api = client._api

    api._uniform = lambda low, high: high
    assert [api._backoff(n) for n in range(7)] == [0.5, 1.0, 2.0, 4.0, 8.0, 8.0, 8.0]

    api._uniform = lambda low, high: low
    assert [api._backoff(n) for n in range(5)] == [0.25, 0.5, 1.0, 2.0, 4.0]


def test_backoff_uses_real_randomness_by_default() -> None:
    api = PayHub(api_key="k:s", country="bf", transport=FakeTransport([]))._api
    draws = {api._backoff(3) for _ in range(50)}
    assert all(2.0 <= value <= 4.0 for value in draws)
    assert len(draws) > 1


def test_max_retries_is_validated_and_capped() -> None:
    with pytest.raises(ConfigurationError):
        PayHub(api_key="k:s", country="bf", max_retries="2")  # type: ignore[arg-type]

    def effective(max_retries: int) -> int:
        client = PayHub(
            api_key="k:s", country="bf", max_retries=max_retries, transport=FakeTransport([])
        )
        return client._api._max_retries

    assert effective(-4) == 0
    assert effective(999) == 10


def test_retry_after_date_without_a_zone_is_read_as_utc() -> None:
    from payhub.client import _parse_retry_after

    now = 1_763_280_000.0  # 2025-11-16T08:00:00Z
    assert _parse_retry_after("Sun, 16 Nov 2025 08:00:30 GMT", now) == 30.0
    assert _parse_retry_after("Sun, 16 Nov 2025 08:00:30 -0000", now) == 30.0
    assert _parse_retry_after("Sun, 16 Nov 2025 07:00:00 GMT", now) == 0.0  # in the past
    assert _parse_retry_after("12", now) == 12.0
    assert _parse_retry_after("1.5", now) == 1.5
    assert _parse_retry_after(None, now) is None
