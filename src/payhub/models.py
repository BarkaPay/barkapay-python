"""Typed data objects returned by the SDK.

Documented fields are typed; every object keeps the full payload in ``raw`` for
forward-compatibility, so a field the API adds tomorrow is never lost.

Money: the API returns mobile-money ``amount`` as a JSON number and ``fees`` /
balances as decimal strings. The attributes keep the API value untouched; the
``*_decimal`` properties give an exact :class:`~decimal.Decimal` for arithmetic.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ._util import (
    as_amount,
    as_dict,
    as_int,
    as_list_of_dict,
    as_money_str,
    as_str,
    to_decimal,
)
from .enums import PaymentLinkStatus, PaymentStatus, TransferStatus


@dataclass(frozen=True)
class Payment:
    """A collection (payment) as returned by the API."""

    public_id: str
    amount: int | float | str | None
    #: Decimal string, e.g. ``"6.25"``.
    fees: str | None
    currency: str | None
    phone_number: str | None
    country: str | None
    operator: str | None
    #: ``"api"`` (direct API payment) or ``"hosted"`` (paid through a payment link).
    channel: str | None
    #: ``None`` when the API returns a status this SDK version does not know
    #: yet — the original string is always in :attr:`raw_status`.
    status: PaymentStatus | None
    order_id: str | None
    #: The operator's transaction id, once it confirmed.
    provider_transaction_id: str | None
    order_data: dict[str, Any] | None
    #: Human-readable outcome sentence. Not contractual — branch on
    #: :attr:`status` and :attr:`reason_code`.
    reason: str | None
    created_at: str | None
    raw: dict[str, Any] = field(repr=False)
    #: Operator-side handle handed back at creation (OTP transaction id,
    #: checkout session id…). ``None`` until the operator answers.
    provider_reference: str | None = None
    #: Stable failure code (see :class:`payhub.ReasonCode`); ``None`` unless
    #: the payment failed or expired.
    reason_code: str | None = None
    #: The operator's own sentence, for support/debugging. Not contractual.
    provider_reason: str | None = None
    #: ``PL-…`` id of the payment link this payment was made through, if any.
    link_public_id: str | None = None
    #: URL the payer must open to authorise the payment (Wave, Sénégal).
    redirect_url: str | None = None
    #: The status string exactly as returned by the API.
    raw_status: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Payment:
        raw_status = as_str(data, "status")
        return cls(
            public_id=as_str(data, "public_id") or "",
            amount=as_amount(data, "amount"),
            fees=as_money_str(data, "fees"),
            currency=as_str(data, "currency"),
            phone_number=as_str(data, "phone_number"),
            country=as_str(data, "country"),
            operator=as_str(data, "operator"),
            channel=as_str(data, "channel"),
            status=PaymentStatus.try_from(raw_status),
            order_id=as_str(data, "order_id"),
            provider_transaction_id=as_str(data, "provider_transaction_id"),
            order_data=as_dict(data, "order_data"),
            reason=as_str(data, "reason"),
            created_at=as_str(data, "created_at"),
            raw=dict(data),
            provider_reference=as_str(data, "provider_reference"),
            reason_code=as_str(data, "reason_code"),
            provider_reason=as_str(data, "provider_reason"),
            link_public_id=as_str(data, "link_public_id"),
            redirect_url=as_str(data, "redirect_url"),
            raw_status=raw_status,
        )

    @property
    def amount_decimal(self) -> Decimal | None:
        """:attr:`amount` as an exact :class:`~decimal.Decimal`."""
        return to_decimal(self.amount)

    @property
    def fees_decimal(self) -> Decimal | None:
        """:attr:`fees` as an exact :class:`~decimal.Decimal`."""
        return to_decimal(self.fees)


@dataclass(frozen=True)
class Transfer:
    """A disbursement (transfer) as returned by the API."""

    public_id: str
    amount: int | float | str | None
    #: Decimal string, e.g. ``"6.25"``.
    fees: str | None
    currency: str | None
    phone_number: str | None
    country: str | None
    operator: str | None
    channel: str | None
    #: ``None`` when the API returns a status this SDK version does not know
    #: yet — the original string is always in :attr:`raw_status`.
    status: TransferStatus | None
    order_id: str | None
    provider_transaction_id: str | None
    order_data: dict[str, Any] | None
    #: Human-readable outcome sentence. Not contractual — branch on
    #: :attr:`status` and :attr:`reason_code`.
    reason: str | None
    created_at: str | None
    raw: dict[str, Any] = field(repr=False)
    #: Operator-side handle for this payout (lines it up with the operator
    #: statement). ``None`` until known.
    provider_reference: str | None = None
    #: Stable failure code (see :class:`payhub.ReasonCode`); ``None`` unless
    #: the transfer failed.
    reason_code: str | None = None
    #: The operator's own sentence, for support/debugging. Not contractual.
    provider_reason: str | None = None
    #: The status string exactly as returned by the API.
    raw_status: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Transfer:
        raw_status = as_str(data, "status")
        return cls(
            public_id=as_str(data, "public_id") or "",
            amount=as_amount(data, "amount"),
            fees=as_money_str(data, "fees"),
            currency=as_str(data, "currency"),
            phone_number=as_str(data, "phone_number"),
            country=as_str(data, "country"),
            operator=as_str(data, "operator"),
            channel=as_str(data, "channel"),
            status=TransferStatus.try_from(raw_status),
            order_id=as_str(data, "order_id"),
            provider_transaction_id=as_str(data, "provider_transaction_id"),
            order_data=as_dict(data, "order_data"),
            reason=as_str(data, "reason"),
            created_at=as_str(data, "created_at"),
            raw=dict(data),
            provider_reference=as_str(data, "provider_reference"),
            reason_code=as_str(data, "reason_code"),
            provider_reason=as_str(data, "provider_reason"),
            raw_status=raw_status,
        )

    @property
    def amount_decimal(self) -> Decimal | None:
        """:attr:`amount` as an exact :class:`~decimal.Decimal`."""
        return to_decimal(self.amount)

    @property
    def fees_decimal(self) -> Decimal | None:
        """:attr:`fees` as an exact :class:`~decimal.Decimal`."""
        return to_decimal(self.fees)


@dataclass(frozen=True)
class Balance:
    """The merchant wallet balance for a country.

    ``available`` is what you can disburse now (``total`` minus ``holds``).
    All three are decimal strings in the wallet ``currency``.
    """

    country: str | None
    currency: str | None
    available: str | None
    total: str | None
    holds: str | None
    #: Reserved for multi-asset wallets; ``None`` for mobile-money countries.
    balances: list[dict[str, Any]] | None
    raw: dict[str, Any] = field(repr=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Balance:
        return cls(
            country=as_str(data, "country"),
            currency=as_str(data, "currency"),
            available=as_money_str(data, "available"),
            total=as_money_str(data, "total"),
            holds=as_money_str(data, "holds"),
            balances=as_list_of_dict(data, "balances"),
            raw=dict(data),
        )

    @property
    def available_decimal(self) -> Decimal | None:
        """:attr:`available` as an exact :class:`~decimal.Decimal`."""
        return to_decimal(self.available)

    @property
    def total_decimal(self) -> Decimal | None:
        """:attr:`total` as an exact :class:`~decimal.Decimal`."""
        return to_decimal(self.total)

    @property
    def holds_decimal(self) -> Decimal | None:
        """:attr:`holds` as an exact :class:`~decimal.Decimal`."""
        return to_decimal(self.holds)


@dataclass(frozen=True)
class PaymentLinkAmount:
    """What a payment link charges."""

    #: ``"fixed"`` (``value``), ``"range"`` (``min`` to ``max``) or ``"open"``
    #: (the payer chooses).
    type: str | None
    #: Decimal string, set for ``fixed`` links.
    value: str | None
    #: Decimal string, set for ``range`` links.
    min: str | None
    #: Decimal string, set for ``range`` links.
    max: str | None
    currency: str | None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PaymentLinkAmount:
        return cls(
            type=as_str(data, "type"),
            value=as_money_str(data, "value"),
            min=as_money_str(data, "min"),
            max=as_money_str(data, "max"),
            currency=as_str(data, "currency"),
        )

    @property
    def value_decimal(self) -> Decimal | None:
        return to_decimal(self.value)

    @property
    def min_decimal(self) -> Decimal | None:
        return to_decimal(self.min)

    @property
    def max_decimal(self) -> Decimal | None:
        return to_decimal(self.max)


@dataclass(frozen=True)
class PaymentLinkStats:
    """Aggregates of a payment link. Only SUCCESSFUL payments are counted."""

    payments_count: int | None
    #: Decimal string, in the link currency.
    total_collected: str | None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PaymentLinkStats:
        return cls(
            payments_count=as_int(data, "payments_count"),
            total_collected=as_money_str(data, "total_collected"),
        )

    @property
    def total_collected_decimal(self) -> Decimal | None:
        return to_decimal(self.total_collected)


@dataclass(frozen=True)
class PaymentLink:
    """A payment link: a durable URL on the barkapay-hosted checkout where
    your customer picks an operator and pays."""

    #: ``PL-…`` identifier.
    public_id: str
    #: 13-character public slug (the last segment of :attr:`url`).
    slug: str | None
    #: The checkout URL to hand to your customer.
    url: str | None
    #: ``None`` when the API returns a status this SDK version does not know
    #: yet — the original string is always in :attr:`raw_status`.
    status: PaymentLinkStatus | None
    raw_status: str | None
    #: ``"api"`` or ``"ui"`` (created from the dashboard).
    source: str | None
    #: ``"single"`` (closed by its first successful payment) or ``"multi"``.
    usage: str | None
    amount: PaymentLinkAmount
    title: str | None
    description: str | None
    #: ``{"id": …, "data": {…}}`` — attached to every payment the link produces.
    order: dict[str, Any] | None
    #: ``{"success_url": …, "cancel_url": …}``.
    redirect: dict[str, Any] | None
    #: ``{"countries": […], "operators": […]}``.
    restrictions: dict[str, Any] | None
    #: Pre-filled payer details (``full_name``, ``email``, ``phone``, ``locale``).
    payer: dict[str, Any] | None
    #: Which extra fields the checkout asks for (``required`` / ``optional`` / ``off``).
    collect: dict[str, Any]
    #: ``{"email_invoice": bool}`` — whether the payer receives the invoice e-mail.
    notify: dict[str, Any]
    expires_at: str | None
    stats: PaymentLinkStats
    #: For a paid single-use link: the payment that closed it.
    paid_by_payment_public_id: str | None
    created_at: str | None
    raw: dict[str, Any] = field(repr=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PaymentLink:
        raw_status = as_str(data, "status")
        return cls(
            public_id=as_str(data, "public_id") or "",
            slug=as_str(data, "slug"),
            url=as_str(data, "url"),
            status=PaymentLinkStatus.try_from(raw_status),
            raw_status=raw_status,
            source=as_str(data, "source"),
            usage=as_str(data, "usage"),
            amount=PaymentLinkAmount.from_dict(as_dict(data, "amount") or {}),
            title=as_str(data, "title"),
            description=as_str(data, "description"),
            order=as_dict(data, "order"),
            redirect=as_dict(data, "redirect"),
            restrictions=as_dict(data, "restrictions"),
            payer=as_dict(data, "payer"),
            collect=as_dict(data, "collect") or {},
            notify=as_dict(data, "notify") or {},
            expires_at=as_str(data, "expires_at"),
            stats=PaymentLinkStats.from_dict(as_dict(data, "stats") or {}),
            paid_by_payment_public_id=as_str(data, "paid_by_payment_public_id"),
            created_at=as_str(data, "created_at"),
            raw=dict(data),
        )


@dataclass(frozen=True)
class Pagination:
    """Pagination metadata from a list response's ``meta`` block."""

    current_page: int | None
    #: Not returned by every list (payment links omit it) — see :attr:`page_count`.
    last_page: int | None
    per_page: int | None
    total: int | None

    @classmethod
    def from_dict(cls, meta: Mapping[str, Any]) -> Pagination:
        return cls(
            current_page=as_int(meta, "current_page"),
            last_page=as_int(meta, "last_page"),
            per_page=as_int(meta, "per_page"),
            total=as_int(meta, "total"),
        )

    @property
    def page_count(self) -> int | None:
        """Number of pages: ``last_page`` when the API gives it, otherwise
        derived from ``total`` and ``per_page``. ``None`` if unknown."""
        if self.last_page is not None:
            return self.last_page
        if self.total is None or self.per_page is None or self.per_page <= 0:
            return None
        return max(1, math.ceil(self.total / self.per_page))

    @property
    def next_page(self) -> int | None:
        """The next page number to request, or ``None`` on the last page
        (or when the position is unknown)."""
        pages = self.page_count
        if pages is None or self.current_page is None or self.current_page >= pages:
            return None
        return self.current_page + 1

    @property
    def has_more(self) -> bool:
        """``True`` when another page follows this one."""
        return self.next_page is not None


@dataclass(frozen=True)
class PaymentList:
    """An iterable page of :class:`Payment` plus optional :class:`Pagination`."""

    items: list[Payment]
    meta: Pagination | None

    def __iter__(self) -> Iterator[Payment]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> Payment:
        return self.items[index]


@dataclass(frozen=True)
class TransferList:
    """An iterable page of :class:`Transfer` plus optional :class:`Pagination`."""

    items: list[Transfer]
    meta: Pagination | None

    def __iter__(self) -> Iterator[Transfer]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> Transfer:
        return self.items[index]


@dataclass(frozen=True)
class PaymentLinkList:
    """An iterable page of :class:`PaymentLink` plus optional :class:`Pagination`."""

    items: list[PaymentLink]
    meta: Pagination | None

    def __iter__(self) -> Iterator[PaymentLink]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> PaymentLink:
        return self.items[index]


@dataclass(frozen=True)
class PaymentLinkSummary:
    """The short view of a link returned next to its payments."""

    public_id: str
    slug: str | None
    status: PaymentLinkStatus | None
    raw_status: str | None
    stats: PaymentLinkStats
    raw: dict[str, Any] = field(repr=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PaymentLinkSummary:
        raw_status = as_str(data, "status")
        return cls(
            public_id=as_str(data, "public_id") or "",
            slug=as_str(data, "slug"),
            status=PaymentLinkStatus.try_from(raw_status),
            raw_status=raw_status,
            stats=PaymentLinkStats.from_dict(as_dict(data, "stats") or {}),
            raw=dict(data),
        )


@dataclass(frozen=True)
class PaymentLinkPaymentList(PaymentList):
    """The payments made through one payment link, **in the country you
    called** (a multi-country link spreads its payments across countries —
    call each one for the full picture; ``payment_link.stats`` are global).
    """

    #: Short view of the link, with its global stats.
    payment_link: PaymentLinkSummary | None = None
