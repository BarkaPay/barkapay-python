"""Sample API payloads, shaped exactly like the live API responses
(fictional values)."""

from __future__ import annotations

from typing import Any

PAYMENT_ID = "0198c2f1-9f6a-7c4e-b1d2-3a4b5c6d7e8f"
TRANSFER_ID = "0198c2f2-1111-7222-8333-444455556666"
LINK_ID = "PL-0198c2f1-9f6a-7c4e-b1d2-3a4b5c6d7e8f"
LINK_SLUG = "9j32HfkT2mQx4"


def payment(**overrides: Any) -> dict[str, Any]:
    """A payment as serialized by GET /payments (PaymentResource)."""
    base: dict[str, Any] = {
        "public_id": PAYMENT_ID,
        "amount": 10000,
        "fees": "250.00",
        "currency": "XOF",
        "phone_number": "74123456",
        "country": "BF",
        "operator": "ORANGE",
        "channel": "api",
        "status": "SUCCESSFUL",
        "order_id": "ORDER-2026-001",
        "provider_transaction_id": "MP261116.1234.A12345",
        "provider_reference": None,
        "order_data": None,
        "link_public_id": None,
        "reason": "Payment completed.",
        "reason_code": None,
        "provider_reason": None,
        "created_at": "2026-11-16T08:00:00.000000Z",
    }
    base.update(overrides)
    return base


def failed_payment(**overrides: Any) -> dict[str, Any]:
    """The FAILED snapshot carried under ``data.payment`` by a 424."""
    base: dict[str, Any] = {
        "public_id": PAYMENT_ID,
        "amount": 10000,
        "phone_number": "74123456",
        "country": "BF",
        "operator": "ORANGE",
        "order_id": "ORDER-2026-001",
        "fees": "250.00",
        "currency": "XOF",
        "status": "FAILED",
        "reason": "Payer has insufficient funds.",
        "reason_code": "payer_insufficient_funds",
        "provider_reason": "Solde insuffisant",
    }
    base.update(overrides)
    return base


def transfer(**overrides: Any) -> dict[str, Any]:
    """A transfer as serialized by GET /transfers (TransferResource)."""
    base: dict[str, Any] = {
        "public_id": TRANSFER_ID,
        "amount": 5000,
        "fees": "50.00",
        "currency": "XOF",
        "phone_number": "70123456",
        "country": "BF",
        "operator": "MOOV",
        "channel": "api",
        "status": "SUCCESSFUL",
        "order_id": "PAYOUT-2026-001",
        "provider_transaction_id": "TX-998877",
        "provider_reference": "REF-112233",
        "order_data": None,
        "reason": "Transfer completed.",
        "reason_code": None,
        "provider_reason": None,
        "created_at": "2026-11-16T08:05:00.000000Z",
    }
    base.update(overrides)
    return base


def payment_link(**overrides: Any) -> dict[str, Any]:
    """A payment link as serialized by /payments/links (PayLinkResource)."""
    base: dict[str, Any] = {
        "public_id": LINK_ID,
        "slug": LINK_SLUG,
        "url": f"https://checkout.payhub.africa/{LINK_SLUG}",
        "status": "active",
        "source": "api",
        "usage": "multi",
        "amount": {
            "type": "fixed",
            "value": "15000",
            "min": None,
            "max": None,
            "currency": "XOF",
        },
        "title": "Invoice INV-2026-001",
        "description": None,
        "order": {"id": "INV-2026-001"},
        "redirect": None,
        "restrictions": {"countries": ["BF"]},
        "payer": None,
        "collect": {},
        "notify": {"email_invoice": False},
        "expires_at": None,
        "stats": {"payments_count": 2, "total_collected": "30000"},
        "paid_by_payment_public_id": None,
        "created_at": "2026-11-16T07:00:00+00:00",
    }
    base.update(overrides)
    return base


PAGE_META: dict[str, Any] = {"current_page": 1, "last_page": 3, "per_page": 2, "total": 5}
