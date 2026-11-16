"""Error envelope → typed exception, as the live API answers."""

from __future__ import annotations

from typing import Any

import pytest

from barkapay import (
    ApiError,
    AuthenticationError,
    AuthorizationError,
    BarkaPayError,
    ConflictError,
    InsufficientBalanceError,
    NotFoundError,
    PaymentFailedError,
    PaymentStatus,
    RateLimitError,
    ReasonCode,
    ServerError,
    ServiceUnavailableError,
    TransferFailedError,
    TransferStatus,
    ValidationError,
)
from tests import samples
from tests.support import json_response, make_client


def _raise(
    status: int, envelope: dict[str, Any], headers: dict[str, str] | None = None
) -> ApiError:
    client, _, _ = make_client([json_response(status, envelope, headers)])
    with pytest.raises(ApiError) as exc_info:
        client.payments.create({"operator": "ORANGE"})
    return exc_info.value


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (401, "invalid_api_key", AuthenticationError),
        (401, "incorrect_credentials", AuthenticationError),
        (401, "unauthenticated", AuthenticationError),
        (403, "unauthorized", AuthorizationError),
        (403, "ip_not_allowed", AuthorizationError),
        (451, "merchant_suspended", AuthorizationError),
        (410, "merchant_closed", AuthorizationError),
        (422, "validation_error", ValidationError),
        (422, "invalid_input", ValidationError),
        (400, "invalid_input", ValidationError),  # malformed JSON body
        (404, "resource_not_found", NotFoundError),
        (409, "duplicate_transaction", ConflictError),
        (409, "resource_already_exists", ConflictError),
        (400, "insufficient_balance", InsufficientBalanceError),
        (424, "payment_failed", PaymentFailedError),
        (424, "transfer_failed", TransferFailedError),
        (429, "too_many_requests", RateLimitError),
        (503, "service_unavailable", ServiceUnavailableError),
        (503, "operator_unavailable", ServiceUnavailableError),
        (500, "internal_error", ServerError),
        (500, "external_service_error", ServerError),
    ],
)
def test_every_documented_code_maps_to_its_class(
    status: int, code: str, expected: type[ApiError]
) -> None:
    error = _raise(status, {"status": "failed", "code": code, "message": "m"})
    assert type(error) is expected
    assert isinstance(error, BarkaPayError)
    assert error.code == code
    assert error.http_status == status


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, AuthenticationError),
        (403, AuthorizationError),
        (404, NotFoundError),
        (409, ConflictError),
        (410, AuthorizationError),
        (422, ValidationError),
        (429, RateLimitError),
        (451, AuthorizationError),
        (503, ServiceUnavailableError),
        (500, ServerError),
        (502, ServerError),
        (504, ServerError),
        (400, ApiError),
        (405, ApiError),
        (418, ApiError),
    ],
)
def test_unknown_code_falls_back_on_the_http_status(status: int, expected: type[ApiError]) -> None:
    error = _raise(status, {"status": "error", "code": "brand_new_code", "message": "m"})
    assert type(error) is expected


def test_method_not_allowed_is_a_plain_api_error_not_a_server_error() -> None:
    # The API answers 405 with the code `internal_error`: the HTTP status wins.
    error = _raise(
        405,
        {
            "status": "error",
            "code": "internal_error",
            "message": "The PUT method is not supported for route v1/ping.",
        },
    )
    assert type(error) is ApiError
    assert error.http_status == 405
    assert error.code == "internal_error"


def test_payment_failed_carries_the_failed_payment() -> None:
    error = _raise(
        424,
        {
            "status": "failed",
            "code": "payment_failed",
            "message": "Payer has insufficient funds.",
            "request_id": "rid-424",
            "data": {"payment": samples.failed_payment()},
        },
    )
    assert isinstance(error, PaymentFailedError)
    assert str(error) == "Payer has insufficient funds."
    assert error.request_id == "rid-424"
    assert error.reason_code == "payer_insufficient_funds"
    assert error.reason_code == ReasonCode.PAYER_INSUFFICIENT_FUNDS
    assert error.transfer is None

    payment = error.payment
    assert payment is not None
    assert payment.public_id == samples.PAYMENT_ID
    assert payment.status is PaymentStatus.FAILED
    assert payment.reason_code == "payer_insufficient_funds"
    assert payment.provider_reason == "Solde insuffisant"
    assert payment.order_id == "ORDER-2026-001"
    assert error.data["payment"]["status"] == "FAILED"


def test_transfer_failed_carries_the_failed_transfer() -> None:
    client, _, _ = make_client(
        [
            json_response(
                424,
                {
                    "status": "failed",
                    "code": "transfer_failed",
                    "message": "Recipient account is invalid.",
                    "data": {
                        "transfer": {
                            "public_id": samples.TRANSFER_ID,
                            "amount": 5000,
                            "status": "FAILED",
                            "order_id": "PAYOUT-1",
                            "fees": "50.00",
                            "currency": "XOF",
                            "reason": "Recipient account is invalid.",
                            "reason_code": "recipient_invalid",
                            "provider_reason": None,
                        }
                    },
                },
            )
        ]
    )
    with pytest.raises(TransferFailedError) as exc_info:
        client.transfers.create({"operator": "MOOV"})

    error = exc_info.value
    assert error.payment is None
    assert error.reason_code == ReasonCode.RECIPIENT_INVALID
    transfer = error.transfer
    assert transfer is not None
    assert transfer.public_id == samples.TRANSFER_ID
    assert transfer.status is TransferStatus.FAILED
    assert transfer.reason_code == "recipient_invalid"


def test_error_without_data_has_empty_data_and_no_snapshot() -> None:
    error = _raise(404, {"status": "failed", "code": "resource_not_found", "message": "nope"})
    assert error.data == {}
    assert error.payment is None
    assert error.transfer is None
    assert error.reason_code is None
    assert error.retry_after is None


def test_validation_error_exposes_the_field_map() -> None:
    error = _raise(
        422,
        {
            "status": "failed",
            "code": "validation_error",
            "message": "The `operator` field is required.",
            "request_id": "rid-1",
            "errors": {"operator": ["The `operator` field is required."]},
        },
    )
    assert isinstance(error, ValidationError)
    assert error.errors == {"operator": ["The `operator` field is required."]}
    assert error.request_id == "rid-1"


def test_rate_limit_error_exposes_retry_after_and_headers() -> None:
    error = _raise(
        429,
        {"status": "error", "code": "too_many_requests", "message": "Too many requests."},
        {"Retry-After": "42", "X-RateLimit-Limit": "30", "X-RateLimit-Remaining": "0"},
    )
    assert isinstance(error, RateLimitError)
    assert error.retry_after == 42.0
    assert error.headers["x-ratelimit-limit"] == "30"
    assert error.headers["x-ratelimit-remaining"] == "0"


def test_request_id_falls_back_on_the_header_then_on_the_one_we_sent() -> None:
    client, transport, _ = make_client(
        [
            json_response(
                500, {"status": "error", "code": "internal_error"}, {"X-Request-Id": "h-1"}
            ),
            json_response(500, {"status": "error", "code": "internal_error"}),
        ]
    )
    with pytest.raises(ServerError) as first:
        client.ping()
    with pytest.raises(ServerError) as second:
        client.ping()
    assert first.value.request_id == "h-1"
    assert second.value.request_id == transport.requests[1].headers["X-Request-Id"]


def test_message_falls_back_when_the_envelope_has_none() -> None:
    error = _raise(500, {"status": "error"})
    assert str(error) == "barkapay API error (HTTP 500)."
    assert error.code == ""


def test_maintenance_answer_of_operator_availability_is_an_error_with_data() -> None:
    # During maintenance the endpoint answers 503 with a success-shaped body.
    client, _, _ = make_client(
        [
            json_response(
                503,
                {
                    "status": "success",
                    "code": "success",
                    "message": "The API is currently in maintenance mode.",
                    "data": {"operational": False, "services": {}},
                },
            )
        ]
    )
    with pytest.raises(ServiceUnavailableError) as exc_info:
        client.operators.availability()
    assert exc_info.value.data == {"operational": False, "services": {}}


def test_api_error_can_still_be_built_positionally_as_in_1_0() -> None:
    error = ApiError("boom", "some_code", 418, "rid", {"field": ["bad"]})
    assert (error.code, error.http_status, error.request_id) == ("some_code", 418, "rid")
    assert error.errors == {"field": ["bad"]}
    assert error.data == {} and error.headers == {} and error.retry_after is None


def test_get_with_an_empty_result_raises_not_found() -> None:
    client, _, _ = make_client(
        [json_response(200, {"status": "success", "data": {"payments": []}})]
    )
    with pytest.raises(NotFoundError):
        client.payments.get("ORDER-404")
