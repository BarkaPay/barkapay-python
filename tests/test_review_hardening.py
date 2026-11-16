"""Regression tests for the points raised by the pre-release review."""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import sys
import time
from collections.abc import Callable
from typing import Any

import pytest

from payhub import (
    ApiError,
    ConfigurationError,
    NetworkError,
    Pagination,
    PayHub,
    Payment,
    RateLimitError,
    UrllibTransport,
    webhook,
)
from payhub.http import Response
from tests import samples
from tests.support import FakeTransport, json_response, make_client, ok

KEY = "pk_live_abc123:sk_live_SUPERSECRET"
THROTTLE_BODY = {"status": "error", "code": "too_many_requests", "message": "Too many requests."}

Write = Callable[[PayHub], Any]
WRITES: dict[str, Write] = {
    "payments.create": lambda c: c.payments.create({"operator": "ORANGE"}),
    "payments.confirm_otp": lambda c: c.payments.confirm_otp(samples.PAYMENT_ID, "123456"),
    "transfers.create": lambda c: c.transfers.create({"operator": "ORANGE"}),
    "payment_links.create": lambda c: c.payment_links.create({"title": "x"}),
    "payment_links.cancel": lambda c: c.payment_links.cancel(samples.LINK_ID),
}
SUCCESS = ok(
    {
        "payment": samples.payment(),
        "transfer": samples.transfer(),
        "payment_link": samples.payment_link(),
    }
)


# --- a write is replayed on 429 only when it is provably the rate limiter ----------


@pytest.mark.parametrize("name", sorted(WRITES))
def test_a_429_that_carries_a_recorded_operation_is_never_replayed(name: str) -> None:
    answer = json_response(
        429,
        {
            "status": "failed",
            "code": "too_many_requests",
            "message": "x",
            "data": {"transfer": {"public_id": "T1", "status": "UNCERTAIN"}},
        },
    )
    client, transport, sleeps = make_client([answer, SUCCESS], max_retries=3)
    with pytest.raises(RateLimitError) as exc_info:
        WRITES[name](client)
    assert len(transport.requests) == 1 and sleeps == []
    assert exc_info.value.transfer is not None and exc_info.value.transfer.public_id == "T1"


@pytest.mark.parametrize("name", sorted(WRITES))
@pytest.mark.parametrize(
    "answer",
    [
        Response(429, "<html>Too many requests</html>"),  # not the API's envelope
        Response(429, ""),
        json_response(429, {"status": "failed", "code": "operator_unavailable", "message": "x"}),
        json_response(429, {"status": "error", "message": "no code"}),
    ],
)
def test_a_429_that_is_not_the_api_rate_limiter_is_not_replayed_for_a_write(
    name: str, answer: Response
) -> None:
    client, transport, sleeps = make_client([answer, SUCCESS], max_retries=3)
    with pytest.raises(ApiError) as exc_info:
        WRITES[name](client)
    assert exc_info.value.http_status == 429
    assert len(transport.requests) == 1 and sleeps == []


def test_a_read_is_still_retried_on_any_429() -> None:
    client, transport, _ = make_client(
        [Response(429, "<html>Too many requests</html>"), ok({"payments": []})], max_retries=1
    )
    client.payments.list()
    assert len(transport.requests) == 2


@pytest.mark.parametrize("name", sorted(WRITES))
def test_a_write_is_not_resent_after_a_long_wait(name: str) -> None:
    throttled = json_response(429, THROTTLE_BODY, {"Retry-After": "30"})
    client, transport, sleeps = make_client([throttled, SUCCESS], max_retries=3)
    with pytest.raises(RateLimitError) as exc_info:
        WRITES[name](client)
    # Not slept through: the caller gets the error and the delay at once.
    assert len(transport.requests) == 1 and sleeps == []
    assert exc_info.value.retry_after == 30.0


@pytest.mark.parametrize("name", sorted(WRITES))
def test_a_write_is_resent_after_a_short_rate_limit_wait(name: str) -> None:
    throttled = json_response(429, THROTTLE_BODY, {"Retry-After": "4"})
    client, transport, sleeps = make_client([throttled, SUCCESS], max_retries=3)
    WRITES[name](client)
    assert len(transport.requests) == 2
    assert len(sleeps) == 1 and 4.0 <= sleeps[0] <= 4.25


def test_retry_after_is_slept_through_up_to_ten_seconds_for_reads_and_writes() -> None:
    for wait, write_sends, read_sends in ((10, 2, 2), (11, 1, 1), (60, 1, 1)):
        throttled = json_response(429, THROTTLE_BODY, {"Retry-After": str(wait)})

        client, transport, _ = make_client([throttled, SUCCESS], max_retries=1)
        with contextlib.suppress(RateLimitError):
            client.transfers.create({"operator": "ORANGE"})
        assert len(transport.requests) == write_sends, f"write, Retry-After {wait}"

        client, transport, _ = make_client([throttled, ok({})], max_retries=1)
        with contextlib.suppress(RateLimitError):
            client.ping()
        assert len(transport.requests) == read_sends, f"read, Retry-After {wait}"


def test_sleep_is_looked_up_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    # A test-suite that patches time.sleep after building the client (as it
    # could with 1.0.0) must not really sleep.
    transport = FakeTransport([json_response(429, THROTTLE_BODY), ok({})])
    client = PayHub(api_key=KEY, country="bf", transport=transport, max_retries=1)
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", slept.append)
    client.ping()
    assert len(slept) == 1 and 0.25 <= slept[0] <= 0.5


# --- a header can never make a call fail ----------------------------------------------


@pytest.mark.parametrize(
    "header",
    [
        "Mon, 01 Jan 2024 00:00:00 +99999999999999999999",
        "Mon, 99 Foo 99999 99:99:99 GMT",
        "9" * 400,
        "\x00",
    ],
)
def test_a_garbled_retry_after_never_breaks_a_successful_write(header: str) -> None:
    answer = json_response(
        201,
        {"status": "success", "data": {"transfer": samples.transfer()}},
        {"Retry-After": header},
    )
    client, _, _ = make_client([answer])
    assert client.transfers.create({"operator": "MOOV"}).public_id == samples.TRANSFER_ID


def test_a_garbled_retry_after_on_an_error_is_ignored() -> None:
    answer = json_response(
        429, THROTTLE_BODY, {"Retry-After": "Mon, 01 Jan 2024 00:00:00 +99999999999999999999"}
    )
    client, _, _ = make_client([answer])
    with pytest.raises(RateLimitError) as exc_info:
        client.ping()
    assert exc_info.value.retry_after is None


# --- the key is not left in traceback frames -----------------------------------------


def test_the_key_is_not_in_any_frame_when_params_cannot_be_encoded() -> None:
    client = PayHub(api_key=KEY, country="bf", transport=FakeTransport([]))
    try:
        client.payments.create({"amount": object()})
    except ConfigurationError:
        traceback = sys.exc_info()[2]
    else:  # pragma: no cover
        raise AssertionError("expected ConfigurationError")

    leaked = []
    while traceback is not None:
        for name, value in traceback.tb_frame.f_locals.items():
            if "SUPERSECRET" in repr(value):
                leaked.append((traceback.tb_frame.f_code.co_name, name))
        traceback = traceback.tb_next
    assert leaked == []


# --- base_url and timeout ---------------------------------------------------------------


@pytest.mark.parametrize(
    "base_url",
    [
        "https://hub.barkapay.com?",
        "https://hub.barkapay.com/#",
        "https://hub.barkapay.com/?x=1",
        "https://hub.barkapay.com/café",
        "https://hub.barkapay.com/​hidden",
        "https://hüb.example",
    ],
)
@pytest.mark.parametrize("custom_transport", [False, True])
def test_base_url_edge_cases_are_refused(base_url: str, custom_transport: bool) -> None:
    transport = FakeTransport([]) if custom_transport else None
    with pytest.raises(ConfigurationError):
        PayHub(api_key=KEY, country="bf", base_url=base_url, transport=transport)


def test_plain_http_is_for_custom_transports_only() -> None:
    # A custom transport owns its connection (docker / in-process mocks)…
    transport = FakeTransport([ok({})])
    PayHub(api_key=KEY, country="bf", base_url="http://mock-api:8080", transport=transport).ping()
    assert transport.requests[0].url == "http://mock-api:8080/bf/v1/ping"
    # …the built-in one never sends the key in clear text over the network.
    with pytest.raises(ConfigurationError):
        PayHub(api_key=KEY, country="bf", base_url="http://mock-api:8080")


@pytest.mark.parametrize(
    "base_url",
    [
        "http://hub.barkapay.com",
        "HTTP://Hub.BarkaPay.com/",
        "http://barkapay.com",
        "http://hub.barkapay.com.",
    ],
)
@pytest.mark.parametrize("custom_transport", [False, True])
def test_plain_http_never_reaches_a_barkapay_host(base_url: str, custom_transport: bool) -> None:
    transport = FakeTransport([]) if custom_transport else None
    with pytest.raises(ConfigurationError):
        PayHub(api_key=KEY, country="bf", base_url=base_url, transport=transport)


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.ping(),
        lambda c: c.me(),
        lambda c: c.balance.get(),
        lambda c: c.operators.info(),
        lambda c: c.operators.availability(),
    ],
)
def test_a_success_envelope_without_data_is_an_error_not_an_empty_dict(call: Any) -> None:
    client, _, _ = make_client([json_response(200, {"status": "success", "code": "success"})])
    with pytest.raises(ApiError) as exc_info:
        call(client)
    assert exc_info.value.code == "invalid_response"


def test_a_network_error_carries_the_request_id_that_was_sent() -> None:
    client, transport, _ = make_client([NetworkError("connection reset")])
    with pytest.raises(NetworkError) as exc_info:
        client.transfers.create({"operator": "ORANGE"})
    sent = transport.requests[0].headers["X-Request-Id"]
    assert exc_info.value.request_id == sent


@pytest.mark.parametrize(
    "base_url", ["file:///etc", "ftp://mock-api", "data:text/plain,x", "ws://x"]
)
def test_only_http_schemes_even_with_a_custom_transport(base_url: str) -> None:
    with pytest.raises(ConfigurationError):
        PayHub(api_key=KEY, country="bf", base_url=base_url, transport=FakeTransport([]))


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_timeouts_are_refused(timeout: float) -> None:
    with pytest.raises(ConfigurationError):
        UrllibTransport(timeout=timeout)
    with pytest.raises(ConfigurationError):
        PayHub(api_key=KEY, country="bf", timeout=timeout)


# --- a 2xx that cannot be used ------------------------------------------------------------


def test_invalid_response_keeps_the_real_status_headers_and_request_id() -> None:
    answer = json_response(
        201,
        {"status": "success", "code": "resource_created", "request_id": "rid-body"},
        {"X-Request-Id": "rid-header", "X-RateLimit-Remaining": "12"},
    )
    client, _, _ = make_client([answer])
    with pytest.raises(ApiError) as exc_info:
        client.transfers.create({"operator": "MOOV"})
    error = exc_info.value
    assert type(error) is ApiError
    assert error.code == "invalid_response"
    assert error.http_status == 201
    assert error.request_id == "rid-body"
    assert error.headers["x-ratelimit-remaining"] == "12"
    assert "accepted" in str(error) and "before sending it again" in str(error)


@pytest.mark.parametrize(
    "data",
    [
        {"transfer": {}},
        {"transfer": {"status": "CREATED"}},
        {"transfer": {"public_id": ""}},
        {"transfer": {"public_id": 12345}},
        {"transfer": "T-1"},
    ],
)
def test_an_object_without_a_public_id_is_not_passed_off_as_a_transfer(
    data: dict[str, Any],
) -> None:
    client, _, _ = make_client([json_response(201, {"status": "success", "data": data})])
    with pytest.raises(ApiError) as exc_info:
        client.transfers.create({"operator": "MOOV"})
    assert exc_info.value.code == "invalid_response"


@pytest.mark.parametrize("rows", [["x", 5], [samples.payment(), None], [[]]])
def test_rows_that_are_not_objects_are_not_silently_dropped(rows: list[Any]) -> None:
    calls: list[Write] = [lambda c: c.payments.list(), lambda c: c.payments.get("ORDER-1")]
    for call in calls:
        client, _, _ = make_client([ok({"payments": rows})])
        with pytest.raises(ApiError) as exc_info:
            call(client)
        assert exc_info.value.code == "invalid_response"


@pytest.mark.parametrize("answer", [Response(204, ""), Response(200, ""), Response(200, "  \n")])
def test_an_empty_2xx_is_fine_where_nothing_is_returned(answer: Response) -> None:
    client, transport, _ = make_client([answer])
    assert client.payments.resend_otp(samples.PAYMENT_ID) is None  # type: ignore[func-returns-value]
    assert len(transport.requests) == 1


def test_an_empty_2xx_is_still_an_error_where_an_object_is_expected() -> None:
    client, _, _ = make_client([Response(204, "")])
    with pytest.raises(ApiError) as exc_info:
        client.payments.create({"operator": "ORANGE"})
    assert exc_info.value.code == "invalid_response" and exc_info.value.http_status == 204


def test_an_order_id_named_links_is_not_sent_to_the_links_route() -> None:
    client, transport, _ = make_client([ok({"payment_links": []})])
    with pytest.raises(ConfigurationError):
        client.payments.get("links")
    assert transport.requests == []


# --- webhooks -------------------------------------------------------------------------------


def test_an_unsigned_request_is_false_whatever_its_body() -> None:
    assert webhook.verify(None, None, "whsec_x") is False
    assert webhook.verify({"parsed": True}, None, "whsec_x") is False  # type: ignore[arg-type]
    assert webhook.verify(b"{}", "", "whsec_x") is False


def test_a_missing_body_with_a_signature_is_false_not_an_exception() -> None:
    ts = int(time.time())
    for_other_body = hmac.new(b"whsec_x", f"{ts}.{{}}".encode(), hashlib.sha256).hexdigest()
    assert webhook.verify(None, f"t={ts},v1={for_other_body}", "whsec_x") is False


def test_text_with_lone_surrogates_is_false_not_an_exception() -> None:
    ts = int(time.time())
    assert webhook.verify("{}\udcff", f"t={ts},v1={'a' * 64}", "whsec_x") is False


def test_a_stray_comma_in_the_header_is_tolerated() -> None:
    ts = int(time.time())
    body = b'{"a":1}'
    digest = hmac.new(b"whsec_x", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    assert webhook.verify(body, f"t={ts},v1={digest},", "whsec_x") is True
    assert webhook.verify(body, f"t={ts},,v1={digest}", "whsec_x") is True


def test_a_binary_secret_is_used_exactly_as_given() -> None:
    ts = int(time.time())
    body = b"{}"
    secret = b" \x00binary key\n"
    digest = hmac.new(secret, f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    assert webhook.verify(body, f"t={ts},v1={digest}", secret) is True
    assert webhook.verify(body, f"t={ts},v1={digest}", secret.strip()) is False


# --- models ----------------------------------------------------------------------------------


def test_page_count_uses_integer_arithmetic() -> None:
    huge = Pagination(current_page=1, last_page=None, per_page=3, total=10**400)
    assert huge.page_count == (10**400 + 2) // 3
    exact = Pagination(current_page=1, last_page=None, per_page=1, total=2**60 + 1)
    assert exact.page_count == 2**60 + 1
    assert Pagination(1, None, 25, 26).page_count == 2
    assert Pagination(1, None, 25, 25).page_count == 1


@pytest.mark.parametrize(
    ("fees", "expected"),
    [(1e-07, "0.0000001"), (0.5, "0.5"), (1e16, "10000000000000000"), (3, "3")],
)
def test_numeric_money_is_rendered_in_plain_notation(fees: float, expected: str) -> None:
    assert Payment.from_dict({"public_id": "p", "fees": fees}).fees == expected


def test_non_finite_money_is_dropped() -> None:
    assert Payment.from_dict({"public_id": "p", "fees": float("nan")}).fees is None
