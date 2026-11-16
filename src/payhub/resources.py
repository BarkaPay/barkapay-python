"""Resource namespaces exposed on the client (``client.payments`` etc.)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ._util import as_dict, path_segment
from .errors import ConfigurationError, NotFoundError
from .models import (
    Balance,
    Pagination,
    Payment,
    PaymentLink,
    PaymentLinkList,
    PaymentLinkPaymentList,
    PaymentLinkSummary,
    PaymentList,
    Transfer,
    TransferList,
)

if TYPE_CHECKING:
    from .client import _ApiClient, _ApiResult


class Payments:
    """Collections: ``client.payments``."""

    def __init__(self, api: _ApiClient) -> None:
        self._api = api

    def create(self, params: Mapping[str, Any], country: str | None = None) -> Payment:
        """Create a payment (collect money from a payer).

        ``params``: ``operator`` (upper case, e.g. ``"ORANGE"``),
        ``phone_number`` (national format, no country prefix), ``amount``,
        ``order`` (``{"id": …, "unique": bool, "data": {…}}``) and ``otp``
        (a 6-digit string) for operators that take the payer's code up-front
        (ORANGE in Burkina Faso and Sénégal).

        Branch on the returned ``status``: ``SUCCESSFUL`` (settled),
        ``AWAITING_OTP`` (call :meth:`confirm_otp`) or ``PROCESSING_OPERATOR``
        (the verdict comes by webhook; open ``redirect_url`` when set). The
        returned object is a short snapshot — :meth:`get` gives the full one.

        A refusal is raised as :class:`~payhub.errors.PaymentFailedError`
        (HTTP 424) carrying the ``FAILED`` payment in ``.payment``.

        This call is never retried automatically after a timeout, a network
        error or a 5xx. To make your own retry safe, send
        ``order.unique: true`` (a second create with the same ``order.id`` is
        refused with a 409) and check with ``list({"order_id": …})`` first.

        To let the payer choose the operator on a hosted page instead, create
        a payment link: ``client.payment_links.create(...)``.
        """
        result = self._api.request("POST", "payments", country=country, body=params)
        return Payment.from_dict(result.resource("payment"))

    def list(
        self,
        filters: Mapping[str, Any] | None = None,
        country: str | None = None,
    ) -> PaymentList:
        """List payments, newest first.

        ``filters``: ``status``, ``operator``, ``phone_number``, ``order_id``,
        ``channel`` (``api`` / ``hosted``), ``from_date`` + ``to_date``,
        ``amount`` + ``amount_operator``, ``per_page`` (max 200), ``page``.
        """
        result = self._api.request("GET", "payments", country=country, query=filters)
        return PaymentList(
            [Payment.from_dict(row) for row in result.rows("payments")], _pagination(result)
        )

    def get(self, id: str, country: str | None = None) -> Payment:
        """Look up a payment by its ``public_id`` (UUID) or by your ``order.id``.

        Several payments can share one ``order.id`` (unless created with
        ``order.unique``): this returns the first match. Use
        ``list({"order_id": …})`` to see them all — and for an ``order.id``
        that contains a ``/``, which cannot be looked up by path.
        """
        segment = path_segment(id)
        if segment == "links":
            # GET /payments/links is the payment-links list, not a lookup.
            raise ConfigurationError(
                '"links" cannot be looked up by path: use payments.list({"order_id": "links"}).'
            )
        result = self._api.request("GET", f"payments/{segment}", country=country)
        rows = result.rows("payments")
        if rows:
            return Payment.from_dict(rows[0])
        raise NotFoundError(f'No payment found for "{id}".', "resource_not_found", 404)

    def confirm_otp(self, public_id: str, otp: str, country: str | None = None) -> Payment:
        """Confirm a payment that is ``AWAITING_OTP`` with the code the payer
        received (two-step OTP operators — currently MOOV in Burkina Faso).

        Pass the code as a **string**: leading zeros are significant.
        A refused code raises :class:`~payhub.errors.PaymentFailedError`.
        """
        segment = path_segment(public_id, "public_id")
        result = self._api.request(
            "POST",
            f"payments/{segment}/confirm-otp",
            country=country,
            body={"otp": _otp(otp)},
        )
        return Payment.from_dict(result.resource("payment"))

    def resend_otp(self, public_id: str, country: str | None = None) -> None:
        """Send the OTP again to the payer of an ``AWAITING_OTP`` payment."""
        segment = path_segment(public_id, "public_id")
        self._api.request(
            "POST",
            f"payments/{segment}/resend-otp",
            country=country,
            allow_empty=True,
        )


class PaymentLinks:
    """Hosted checkout links: ``client.payment_links``.

    A link is a durable URL (``PaymentLink.url``) where your customer picks an
    operator and pays on a barkapay-hosted page. ``id`` is the link's
    ``public_id`` (``PL-…``) or its 13-character ``slug``.
    """

    def __init__(self, api: _ApiClient) -> None:
        self._api = api

    def create(self, params: Mapping[str, Any], country: str | None = None) -> PaymentLink:
        """Create a payment link.

        ``params``: ``title`` (required), ``amount`` (required —
        ``{"type": "fixed", "value": …, "currency": "XOF"}``,
        ``{"type": "range", "min": …, "max": …}`` or ``{"type": "open"}``),
        and optionally ``description``, ``usage`` (``single`` / ``multi``),
        ``order``, ``redirect`` (``success_url`` / ``cancel_url``),
        ``restrictions`` (``countries`` / ``operators``), ``payer``,
        ``collect``, ``notify_email_invoice``, ``expires_at``.
        """
        result = self._api.request("POST", "payments/links", country=country, body=params)
        return PaymentLink.from_dict(result.resource("payment_link"))

    def list(
        self,
        params: Mapping[str, Any] | None = None,
        country: str | None = None,
    ) -> PaymentLinkList:
        """List your payment links, newest first.

        ``params``: ``status`` (one value, or several: ``["active", "paid"]``),
        ``usage``, ``source`` (``api`` / ``ui``), ``per_page`` (max 100), ``page``.
        """
        result = self._api.request("GET", "payments/links", country=country, query=params)
        rows = result.rows("payment_links")
        return PaymentLinkList([PaymentLink.from_dict(row) for row in rows], _pagination(result))

    def get(self, id: str, country: str | None = None) -> PaymentLink:
        """Retrieve a payment link by ``public_id`` (``PL-…``) or slug."""
        segment = path_segment(id)
        result = self._api.request("GET", f"payments/links/{segment}", country=country)
        return PaymentLink.from_dict(result.resource("payment_link"))

    def update(
        self,
        id: str,
        params: Mapping[str, Any],
        country: str | None = None,
    ) -> PaymentLink:
        """Edit an active or paused link.

        Only ``title``, ``description``, ``expires_at`` and ``redirect`` can
        change (``None`` clears the last three); amount, currency and usage
        are immutable.
        """
        segment = path_segment(id)
        result = self._api.request(
            "PATCH", f"payments/links/{segment}", country=country, body=params
        )
        return PaymentLink.from_dict(result.resource("payment_link"))

    def cancel(self, id: str, country: str | None = None) -> PaymentLink:
        """Cancel a link for good (active or paused → ``canceled``)."""
        segment = path_segment(id)
        result = self._api.request("DELETE", f"payments/links/{segment}", country=country)
        return PaymentLink.from_dict(result.resource("payment_link"))

    def pause(self, id: str, country: str | None = None) -> PaymentLink:
        """Pause an active link: the checkout refuses new payments at once.

        A link that is not active is returned unchanged — check ``status``.
        """
        segment = path_segment(id)
        result = self._api.request("POST", f"payments/links/{segment}/pause", country=country)
        return PaymentLink.from_dict(result.resource("payment_link"))

    def resume(self, id: str, country: str | None = None) -> PaymentLink:
        """Resume a paused link.

        A link that is not paused is returned unchanged — check ``status``.
        """
        segment = path_segment(id)
        result = self._api.request("POST", f"payments/links/{segment}/resume", country=country)
        return PaymentLink.from_dict(result.resource("payment_link"))

    def list_payments(
        self,
        id: str,
        params: Mapping[str, Any] | None = None,
        country: str | None = None,
    ) -> PaymentLinkPaymentList:
        """List the payments made through a link, newest first — for the
        country you call only.

        ``params``: ``status``, ``operator``, ``phone_number``, ``from_date``,
        ``to_date``, ``per_page`` (max 100), ``page``.
        """
        segment = path_segment(id)
        result = self._api.request(
            "GET", f"payments/links/{segment}/payments", country=country, query=params
        )
        rows = result.rows("payments")
        summary = as_dict(result.data or {}, "payment_link")
        return PaymentLinkPaymentList(
            [Payment.from_dict(row) for row in rows],
            _pagination(result),
            PaymentLinkSummary.from_dict(summary) if summary is not None else None,
        )


class Transfers:
    """Disbursements: ``client.transfers``."""

    def __init__(self, api: _ApiClient) -> None:
        self._api = api

    def create(self, params: Mapping[str, Any], country: str | None = None) -> Transfer:
        """Create a transfer (send money to a recipient).

        ``params``: ``operator`` (upper case), ``phone_number`` (national
        format), ``amount``, ``order`` (``{"id": …, "unique": bool, "data": {…}}``),
        ``ignore_double_spend_risk`` (bypass the 60 s duplicate guard).

        A status of ``UNCERTAIN`` means the operator did not answer clearly:
        the payout may have been executed — do NOT send it again, the
        ``transfer.*`` webhook carries the verdict. A definitive refusal is
        raised as :class:`~payhub.errors.TransferFailedError` (HTTP 424).

        This call is never retried automatically after a timeout, a network
        error or a 5xx. After such an error the outcome is unknown: send
        ``order.unique: true`` (a duplicate is then refused with a 409) and
        check with ``list({"order_id": …})`` before sending again.
        """
        result = self._api.request("POST", "transfers", country=country, body=params)
        return Transfer.from_dict(result.resource("transfer"))

    def list(
        self,
        filters: Mapping[str, Any] | None = None,
        country: str | None = None,
    ) -> TransferList:
        """List transfers, newest first.

        ``filters``: ``status``, ``operator``, ``phone_number``, ``order_id``,
        ``from_date`` + ``to_date``, ``amount`` + ``amount_operator``,
        ``per_page`` (max 200), ``page``.
        """
        result = self._api.request("GET", "transfers", country=country, query=filters)
        return TransferList(
            [Transfer.from_dict(row) for row in result.rows("transfers")], _pagination(result)
        )

    def get(self, id: str, country: str | None = None) -> Transfer:
        """Look up a transfer by its ``public_id`` (UUID) or by your ``order.id``.

        Several transfers can share one ``order.id`` (unless created with
        ``order.unique``): this returns the first match. Use
        ``list({"order_id": …})`` to see them all — and for an ``order.id``
        that contains a ``/``, which cannot be looked up by path.
        """
        segment = path_segment(id)
        result = self._api.request("GET", f"transfers/{segment}", country=country)
        rows = result.rows("transfers")
        if rows:
            return Transfer.from_dict(rows[0])
        raise NotFoundError(f'No transfer found for "{id}".', "resource_not_found", 404)


class BalanceResource:
    """Wallet balance: ``client.balance``."""

    def __init__(self, api: _ApiClient) -> None:
        self._api = api

    def get(self, country: str | None = None) -> Balance:
        """The merchant's wallet balance for the country."""
        result = self._api.request("GET", "balance", country=country)
        data = result.data
        if data is None:
            raise result.unexpected("`data` is missing")
        return Balance.from_dict(data)


class Operators:
    """Operator directory: ``client.operators``."""

    def __init__(self, api: _ApiClient) -> None:
        self._api = api

    def availability(self, country: str | None = None) -> dict[str, Any]:
        """Real-time payment/transfer availability per operator.

        ``{"operational": bool, "services": {country: {operator: {"payment":
        {"status", "reason", "updated_at"}, "transfer": {…}}}}}``
        """
        return self._api.request("GET", "operators/availability", country=country).require_data()

    def info(self, country: str | None = None) -> dict[str, Any]:
        """Operator capabilities (OTP requirement, amount bounds, instructions).

        This is the authoritative list of operators for the country:
        ``{"operators": {country: {operator: {"otp_required", "min_amount",
        "max_amount", "pay_delay", "payment_instructions"}}}, "supported_countries": […]}``
        """
        return self._api.request("GET", "operators/info", country=country).require_data()


def _otp(otp: object) -> str:
    # The code is an opaque string ("012345" is not 12345). An int is accepted
    # for backward compatibility, but its leading zeros are already lost.
    if isinstance(otp, bool) or not isinstance(otp, (str, int)):
        raise ConfigurationError("`otp` must be the code as a string, e.g. '012345'.")
    code = str(otp).strip()
    if code == "":
        raise ConfigurationError("`otp` must be the code as a string, e.g. '012345'.")
    return code


def _pagination(result: _ApiResult) -> Pagination | None:
    meta = as_dict(result.envelope, "meta")
    return Pagination.from_dict(meta) if meta is not None else None
