"""Hydration of the objects the API returns today (1.1 fields)."""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest

from barkapay import (
    Balance,
    Pagination,
    Payment,
    PaymentLinkStatus,
    PaymentStatus,
    ReasonCode,
    Transfer,
    TransferStatus,
)
from tests import samples
from tests.support import json_response, make_client, ok


def test_payment_exposes_every_field_of_the_resource() -> None:
    payment = Payment.from_dict(
        samples.payment(
            status="FAILED",
            channel="hosted",
            provider_reference="OTP-TRX-1",
            link_public_id=samples.LINK_ID,
            reason="Payer refused the transaction.",
            reason_code="payer_refused",
            provider_reason="Transaction annulée par le client",
            order_data={"cart": "42"},
        )
    )
    assert payment.public_id == samples.PAYMENT_ID
    assert payment.amount == 10000
    assert payment.amount_decimal == Decimal("10000")
    assert payment.fees == "250.00"
    assert payment.fees_decimal == Decimal("250.00")
    assert payment.currency == "XOF"
    assert payment.phone_number == "74123456"
    assert payment.country == "BF"
    assert payment.operator == "ORANGE"
    assert payment.channel == "hosted"
    assert payment.status is PaymentStatus.FAILED
    assert payment.raw_status == "FAILED"
    assert payment.order_id == "ORDER-2026-001"
    assert payment.provider_transaction_id == "MP261116.1234.A12345"
    assert payment.provider_reference == "OTP-TRX-1"
    assert payment.order_data == {"cart": "42"}
    assert payment.link_public_id == samples.LINK_ID
    assert payment.reason == "Payer refused the transaction."
    assert payment.reason_code == "payer_refused"
    assert payment.reason_code == ReasonCode.PAYER_REFUSED
    assert payment.provider_reason == "Transaction annulée par le client"
    assert payment.redirect_url is None
    assert payment.created_at == "2026-11-16T08:00:00.000000Z"


def test_wave_payment_exposes_the_redirect_url() -> None:
    client, _, _ = make_client(
        [
            json_response(
                202,
                {
                    "status": "success",
                    "code": "success",
                    "message": "Payment initiated — awaiting payer authorization in the Wave app.",
                    "data": {
                        "payment": {
                            "public_id": samples.PAYMENT_ID,
                            "amount": 2500,
                            "phone_number": "771234567",
                            "country": "SN",
                            "operator": "WAVE",
                            "order_id": "ORDER-7",
                            "fees": "25.00",
                            "currency": "XOF",
                            "status": "PROCESSING_OPERATOR",
                            "provider_reference": "cos-1a2b3c",
                            "redirect_url": "https://pay.wave.com/c/cos-1a2b3c",
                        }
                    },
                },
            )
        ],
        country="sn",
    )
    payment = client.payments.create({"operator": "WAVE", "amount": 2500})
    assert payment.status is PaymentStatus.PROCESSING_OPERATOR
    assert payment.redirect_url == "https://pay.wave.com/c/cos-1a2b3c"
    assert payment.provider_reference == "cos-1a2b3c"


def test_expired_payment_is_a_known_final_status() -> None:
    payment = Payment.from_dict(samples.payment(status="EXPIRED", reason_code="expired"))
    assert payment.status is PaymentStatus.EXPIRED
    assert PaymentStatus.EXPIRED.is_final()
    assert payment.reason_code == ReasonCode.EXPIRED


def test_uncertain_transfer_is_known_and_not_final() -> None:
    transfer = Transfer.from_dict(samples.transfer(status="UNCERTAIN"))
    assert transfer.status is TransferStatus.UNCERTAIN
    assert not TransferStatus.UNCERTAIN.is_final()
    assert not TransferStatus.UNRESOLVED.is_final()


def test_unknown_status_keeps_the_original_string() -> None:
    payment = Payment.from_dict({"public_id": "p", "status": "SOMETHING_NEW"})
    transfer = Transfer.from_dict({"public_id": "t", "status": "SOMETHING_NEW"})
    assert payment.status is None and payment.raw_status == "SOMETHING_NEW"
    assert transfer.status is None and transfer.raw_status == "SOMETHING_NEW"


def test_unknown_reason_code_is_kept_as_a_string() -> None:
    payment = Payment.from_dict(samples.payment(status="FAILED", reason_code="brand_new_reason"))
    assert payment.reason_code == "brand_new_reason"
    assert ReasonCode.try_from(payment.reason_code) is None


def test_transfer_exposes_every_field_of_the_resource() -> None:
    transfer = Transfer.from_dict(
        samples.transfer(status="FAILED", reason_code="recipient_invalid", provider_reason="x")
    )
    assert transfer.public_id == samples.TRANSFER_ID
    assert transfer.amount_decimal == Decimal("5000")
    assert transfer.fees_decimal == Decimal("50.00")
    assert transfer.provider_reference == "REF-112233"
    assert transfer.reason_code == "recipient_invalid"
    assert transfer.provider_reason == "x"
    assert transfer.raw_status == "FAILED"
    assert transfer.channel == "api"


@pytest.mark.parametrize(
    ("fees", "expected", "as_decimal"),
    [
        ("6.25", "6.25", Decimal("6.25")),
        (0, "0", Decimal("0")),
        (6.25, "6.25", Decimal("6.25")),
        (None, None, None),
        (True, None, None),
        ([], None, None),
    ],
)
def test_fees_given_as_a_number_are_not_dropped(
    fees: object, expected: str | None, as_decimal: Decimal | None
) -> None:
    payment = Payment.from_dict({"public_id": "p", "fees": fees})
    assert payment.fees == expected
    assert payment.fees_decimal == as_decimal


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (10000, Decimal("10000")),
        (10000.0, Decimal("10000.0")),
        (100.1, Decimal("100.1")),  # never Decimal("100.09999…")
        (0.07, Decimal("0.07")),
        ("0.00012345", Decimal("0.00012345")),
        (" 12.50 ", Decimal("12.50")),
        ("abc", None),
        ("NaN", None),
        (None, None),
        (True, None),
    ],
)
def test_amount_decimal_is_exact(amount: object, expected: Decimal | None) -> None:
    assert Payment.from_dict({"public_id": "p", "amount": amount}).amount_decimal == expected
    assert Transfer.from_dict({"public_id": "t", "amount": amount}).amount_decimal == expected


def test_balance_decimals() -> None:
    balance = Balance.from_dict(
        {
            "country": "BF",
            "currency": "XOF",
            "available": "150000.00",
            "total": "150250.50",
            "holds": "250.50",
        }
    )
    assert balance.available_decimal == Decimal("150000.00")
    assert balance.total_decimal == Decimal("150250.50")
    assert balance.holds_decimal == Decimal("250.50")
    assert balance.available_decimal + balance.holds_decimal == balance.total_decimal
    assert Balance.from_dict({}).available_decimal is None


def test_models_tolerate_unknown_fields_and_wrong_types() -> None:
    payment = Payment.from_dict(
        {
            "public_id": 12345,  # wrong type
            "amount": {"nested": True},
            "status": 7,
            "order_data": "not-a-dict",
            "tomorrow": {"new": "field"},
        }
    )
    assert payment.public_id == ""
    assert payment.amount is None
    assert payment.status is None and payment.raw_status is None
    assert payment.order_data is None
    assert payment.raw["tomorrow"] == {"new": "field"}


def test_models_are_frozen_and_raw_is_out_of_repr() -> None:
    payment = Payment.from_dict(samples.payment(secret_note="in raw only"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        payment.reason_code = "x"  # type: ignore[misc]
    assert "in raw only" not in repr(payment)


def test_models_can_still_be_built_positionally_as_in_1_0() -> None:
    payment = Payment(
        "p", 1, "0", "XOF", None, "BF", "ORANGE", "api", None, None, None, None, None, None, {}
    )
    assert payment.reason_code is None and payment.raw_status is None
    transfer = Transfer(
        "t", 1, "0", "XOF", None, "BF", "MOOV", "api", None, None, None, None, None, None, {}
    )
    assert transfer.provider_reference is None


def test_enums_print_their_wire_value_on_every_python_version() -> None:
    assert str(PaymentStatus.SUCCESSFUL) == "SUCCESSFUL"
    assert f"{TransferStatus.UNCERTAIN}" == "UNCERTAIN"
    assert f"{PaymentLinkStatus.ADMIN_BLOCKED}" == "admin_blocked"
    wire: str = "SUCCESSFUL"
    assert wire == PaymentStatus.SUCCESSFUL  # members compare equal to their string value
    assert PaymentStatus("EXPIRED") is PaymentStatus.EXPIRED


def test_reason_code_catalogue_matches_the_api() -> None:
    assert {code.value for code in ReasonCode} == {
        "payer_insufficient_funds",
        "payer_refused",
        "payer_unreachable",
        "invalid_otp",
        "otp_expired",
        "expired",
        "recipient_invalid",
        "operator_unavailable",
        "operator_timeout",
        "unconfirmed_after_window",
        "payment_not_completed",
        "amount_out_of_bounds",
        "duplicate",
        "provider_float_insufficient",
        "internal_error",
        "unknown",
    }


def test_status_catalogues_match_the_api() -> None:
    assert {s.value for s in PaymentStatus} == {
        "CREATED",
        "AWAITING_OTP",
        "QUEUED_FOR_PROCESSING",
        "PROCESSING_OPERATOR",
        "PROCESSING_SYSTEM",
        "SUCCESSFUL",
        "FAILED",
        "EXPIRED",
        "COMPLIANCE_FLAGGED",
        "CANCELED",
    }
    assert {s for s in PaymentStatus if s.is_final()} == {
        PaymentStatus.SUCCESSFUL,
        PaymentStatus.FAILED,
        PaymentStatus.EXPIRED,
        PaymentStatus.CANCELED,
    }
    assert {s.value for s in TransferStatus} == {
        "CREATED",
        "PROCESSING_OPERATOR",
        "PROCESSING_SYSTEM",
        "SUCCESSFUL",
        "FAILED",
        "COMPLIANCE_FLAGGED",
        "REFUNDED",
        "UNRESOLVED",
        "UNCERTAIN",
    }
    assert {s for s in TransferStatus if s.is_final()} == {
        TransferStatus.SUCCESSFUL,
        TransferStatus.FAILED,
        TransferStatus.REFUNDED,
    }
    assert {s.value for s in PaymentLinkStatus} == {
        "active",
        "paused",
        "paid",
        "expired",
        "canceled",
        "admin_blocked",
    }


# --- pagination -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("meta", "page_count", "next_page"),
    [
        ({"current_page": 1, "last_page": 3, "per_page": 2, "total": 5}, 3, 2),
        ({"current_page": 3, "last_page": 3, "per_page": 2, "total": 5}, 3, None),
        ({"current_page": 1, "per_page": 25, "total": 60}, 3, 2),  # no last_page (links)
        ({"current_page": 3, "per_page": 25, "total": 60}, 3, None),
        ({"current_page": 1, "per_page": 25, "total": 0}, 1, None),
        ({"count": 1, "lookup_field": "public_id"}, None, None),  # lookup meta
        ({}, None, None),
    ],
)
def test_pagination_helpers(
    meta: dict[str, object], page_count: int | None, next_page: int | None
) -> None:
    pagination = Pagination.from_dict(meta)
    assert pagination.page_count == page_count
    assert pagination.next_page == next_page
    assert pagination.has_more is (next_page is not None)


def test_walking_every_page_of_a_list() -> None:
    pages = [
        ok(
            {"payments": [samples.payment(public_id="p1"), samples.payment(public_id="p2")]},
            {"current_page": 1, "last_page": 3, "per_page": 2, "total": 5},
        ),
        ok(
            {"payments": [samples.payment(public_id="p3"), samples.payment(public_id="p4")]},
            {"current_page": 2, "last_page": 3, "per_page": 2, "total": 5},
        ),
        ok(
            {"payments": [samples.payment(public_id="p5")]},
            {"current_page": 3, "last_page": 3, "per_page": 2, "total": 5},
        ),
    ]
    client, transport, _ = make_client(pages)

    seen: list[str] = []
    page: int | None = 1
    while page is not None:
        batch = client.payments.list({"per_page": 2, "page": page})
        seen.extend(p.public_id for p in batch)
        page = batch.meta.next_page if batch.meta else None

    assert seen == ["p1", "p2", "p3", "p4", "p5"]
    assert [r.url.split("?")[1] for r in transport.requests] == [
        "per_page=2&page=1",
        "per_page=2&page=2",
        "per_page=2&page=3",
    ]
