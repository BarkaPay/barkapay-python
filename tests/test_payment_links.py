"""The ``payment_links`` resource: verbs, paths, bodies and hydration."""

from __future__ import annotations

from decimal import Decimal

import pytest

from payhub import (
    NotFoundError,
    Payment,
    PaymentLink,
    PaymentLinkList,
    PaymentLinkPaymentList,
    PaymentLinkStatus,
    PaymentList,
    PaymentStatus,
    ValidationError,
)
from payhub.http import Response
from tests import samples
from tests.support import json_response, make_client, ok, sent_json

BASE = "https://hub.barkapay.com/bf/v1/payments/links"


def _link_response(**overrides: object) -> Response:
    return ok({"payment_link": samples.payment_link(**overrides)})


def test_create_posts_the_params_and_returns_the_link() -> None:
    client, transport, _ = make_client(
        [
            json_response(
                201,
                {
                    "status": "success",
                    "code": "resource_created",
                    "message": "Payment link created.",
                    "data": {"payment_link": samples.payment_link()},
                },
            )
        ]
    )
    params = {
        "title": "Invoice INV-2026-001",
        "amount": {"type": "fixed", "value": 15000, "currency": "XOF"},
        "usage": "multi",
        "order": {"id": "INV-2026-001"},
        "redirect": {"success_url": "https://shop.example/thanks"},
    }

    link = client.payment_links.create(params)

    request = transport.requests[0]
    assert (request.method, request.url) == ("POST", BASE)
    assert sent_json(transport) == params
    assert isinstance(link, PaymentLink)
    assert link.public_id == samples.LINK_ID
    assert link.slug == samples.LINK_SLUG
    assert link.url == f"https://checkout.payhub.africa/{samples.LINK_SLUG}"
    assert link.status is PaymentLinkStatus.ACTIVE
    assert link.raw_status == "active"
    assert link.source == "api"
    assert link.usage == "multi"
    assert link.amount.type == "fixed"
    assert link.amount.value == "15000"
    assert link.amount.value_decimal == Decimal("15000")
    assert link.amount.min is None and link.amount.max_decimal is None
    assert link.amount.currency == "XOF"
    assert link.title == "Invoice INV-2026-001"
    assert link.order == {"id": "INV-2026-001"}
    assert link.restrictions == {"countries": ["BF"]}
    assert link.collect == {}
    assert link.notify_email_invoice is False
    assert link.expires_at is None
    assert link.stats.payments_count == 2
    assert link.stats.total_collected == "30000"
    assert link.stats.total_collected_decimal == Decimal("30000")
    assert link.paid_by_payment_public_id is None
    assert link.created_at == "2026-11-16T07:00:00+00:00"
    assert link.raw["notify"] == {"email_invoice": False}


def test_create_validation_error_exposes_the_fields() -> None:
    client, _, _ = make_client(
        [
            json_response(
                422,
                {
                    "status": "failed",
                    "code": "validation_error",
                    "message": "The title field is required. (and 2 more errors)",
                    "errors": {
                        "title": ["The title field is required."],
                        "amount": ["The amount field is required."],
                        "amount.type": ["The amount.type field is required."],
                    },
                },
            )
        ]
    )
    with pytest.raises(ValidationError) as exc_info:
        client.payment_links.create({})
    assert set(exc_info.value.errors) == {"title", "amount", "amount.type"}


def test_list_sends_filters_and_hydrates_the_page() -> None:
    client, transport, _ = make_client(
        [
            ok(
                {"payment_links": [samples.payment_link(), samples.payment_link(status="paid")]},
                {"current_page": 1, "per_page": 2, "total": 5},
            )
        ]
    )

    page = client.payment_links.list(
        {"status": ["active", "paid"], "usage": "multi", "per_page": 2}
    )

    request = transport.requests[0]
    assert request.method == "GET"
    assert request.url == f"{BASE}?status=active%2Cpaid&usage=multi&per_page=2"
    assert isinstance(page, PaymentLinkList)
    assert len(page) == 2
    assert [link.status for link in page] == [PaymentLinkStatus.ACTIVE, PaymentLinkStatus.PAID]
    assert page[1].status is PaymentLinkStatus.PAID
    assert page.meta is not None
    # The links list has no `last_page`: the helpers derive it.
    assert page.meta.last_page is None
    assert page.meta.page_count == 3
    assert page.meta.next_page == 2
    assert page.meta.has_more is True


def test_list_without_params() -> None:
    client, transport, _ = make_client(
        [ok({"payment_links": []}, {"current_page": 1, "per_page": 25, "total": 0})]
    )
    page = client.payment_links.list()
    assert transport.requests[0].url == BASE
    assert len(page) == 0
    assert page.meta is not None and page.meta.has_more is False


@pytest.mark.parametrize("identifier", [samples.LINK_ID, samples.LINK_SLUG])
def test_get_by_public_id_or_slug(identifier: str) -> None:
    client, transport, _ = make_client([_link_response()])
    link = client.payment_links.get(identifier)
    request = transport.requests[0]
    assert (request.method, request.url) == ("GET", f"{BASE}/{identifier}")
    assert request.body is None
    assert link.public_id == samples.LINK_ID


def test_get_unknown_link_raises_not_found() -> None:
    client, _, _ = make_client(
        [
            json_response(
                404,
                {
                    "status": "failed",
                    "code": "resource_not_found",
                    "message": "No payment link found for 'PL-nope'.",
                },
            )
        ]
    )
    with pytest.raises(NotFoundError):
        client.payment_links.get("PL-nope")


def test_update_patches_only_what_is_given_and_none_clears() -> None:
    client, transport, _ = make_client([_link_response(title="New title")])
    link = client.payment_links.update(samples.LINK_ID, {"title": "New title", "description": None})
    request = transport.requests[0]
    assert (request.method, request.url) == ("PATCH", f"{BASE}/{samples.LINK_ID}")
    assert request.body == '{"title":"New title","description":null}'
    assert request.headers["Content-Type"] == "application/json"
    assert link.title == "New title"


def test_cancel_sends_delete() -> None:
    client, transport, _ = make_client([_link_response(status="canceled")])
    link = client.payment_links.cancel(samples.LINK_ID)
    request = transport.requests[0]
    assert (request.method, request.url) == ("DELETE", f"{BASE}/{samples.LINK_ID}")
    assert request.body is None
    assert link.status is PaymentLinkStatus.CANCELED


def test_cancel_of_a_settled_link_is_a_validation_error() -> None:
    client, _, _ = make_client(
        [
            json_response(
                422,
                {
                    "status": "failed",
                    "code": "invalid_input",
                    "message": "A paid payment link cannot be canceled.",
                },
            )
        ]
    )
    with pytest.raises(ValidationError) as exc_info:
        client.payment_links.cancel(samples.LINK_ID)
    assert exc_info.value.code == "invalid_input"
    assert exc_info.value.errors == {}


def test_pause_and_resume() -> None:
    client, transport, _ = make_client(
        [_link_response(status="paused"), _link_response(status="active")]
    )
    paused = client.payment_links.pause(samples.LINK_ID)
    resumed = client.payment_links.resume(samples.LINK_SLUG)

    assert [(r.method, r.url) for r in transport.requests] == [
        ("POST", f"{BASE}/{samples.LINK_ID}/pause"),
        ("POST", f"{BASE}/{samples.LINK_SLUG}/resume"),
    ]
    assert all(r.body is None for r in transport.requests)
    assert paused.status is PaymentLinkStatus.PAUSED
    assert resumed.status is PaymentLinkStatus.ACTIVE


def test_list_payments_returns_payments_plus_the_link_summary() -> None:
    client, transport, _ = make_client(
        [
            ok(
                {
                    "payment_link": {
                        "public_id": samples.LINK_ID,
                        "slug": samples.LINK_SLUG,
                        "status": "active",
                        "stats": {"payments_count": 2, "total_collected": "30000"},
                    },
                    "payments": [
                        samples.payment(channel="hosted", link_public_id=samples.LINK_ID),
                        samples.payment(
                            channel="hosted",
                            link_public_id=samples.LINK_ID,
                            status="EXPIRED",
                            reason_code="expired",
                        ),
                    ],
                },
                {"current_page": 1, "last_page": 1, "per_page": 25, "total": 2},
            )
        ]
    )

    page = client.payment_links.list_payments(samples.LINK_ID, {"status": "SUCCESSFUL", "page": 1})

    request = transport.requests[0]
    assert request.method == "GET"
    assert request.url == f"{BASE}/{samples.LINK_ID}/payments?status=SUCCESSFUL&page=1"
    assert isinstance(page, PaymentLinkPaymentList)
    assert isinstance(page, PaymentList)  # usable wherever a payments page is
    assert len(page) == 2
    assert all(isinstance(p, Payment) for p in page)
    assert page[0].channel == "hosted"
    assert page[0].link_public_id == samples.LINK_ID
    assert page[1].status is PaymentStatus.EXPIRED
    assert page.meta is not None and page.meta.total == 2 and page.meta.has_more is False
    assert page.payment_link is not None
    assert page.payment_link.public_id == samples.LINK_ID
    assert page.payment_link.status is PaymentLinkStatus.ACTIVE
    assert page.payment_link.stats.payments_count == 2


def test_country_override_applies_to_links() -> None:
    client, transport, _ = make_client([_link_response()])
    client.payment_links.get(samples.LINK_ID, country="sn")
    assert transport.requests[0].url.startswith("https://hub.barkapay.com/sn/v1/payments/links/")


def test_unknown_link_status_is_tolerated() -> None:
    link = PaymentLink.from_dict(samples.payment_link(status="archived", brand_new_field=1))
    assert link.status is None
    assert link.raw_status == "archived"
    assert link.raw["brand_new_field"] == 1


def test_link_hydrates_from_a_minimal_payload() -> None:
    link = PaymentLink.from_dict({"public_id": "PL-1"})
    assert link.public_id == "PL-1"
    assert link.amount.type is None
    assert link.stats.payments_count is None
    assert link.collect == {}
    assert link.notify_email_invoice is None
