"""Resource namespaces exposed on the client (``client.payments`` etc.)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ._util import as_dict, as_list_of_dict, path_segment
from .errors import ApiError, ConfigurationError, NotFoundError
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
    from .client import _ApiClient


class Payments:
    """Collections: ``client.payments``."""

    def __init__(self, api: _ApiClient) -> None:
        self._api = api

    def create(self, params: Mapping[str, Any], country: str | None = None) -> Payment:
        """Create a payment (collect money from a payer).

        ``params``: ``operator``, ``phone_number``, ``amount``,
        ``order`` (``{"id": …, "unique": bool, "data": {…}}``) and ``otp`` for
        operators that take the payer's code up-front (``operators.info()``
        tells which ones).

        Branch on the returned ``status``: ``SUCCESSFUL`` (settled),
        ``AWAITING_OTP`` (call :meth:`confirm_otp`) or ``PROCESSING_OPERATOR``
        (the verdict comes by webhook; open ``redirect_url`` when set).

        A refusal is raised as :class:`~payhub.errors.PaymentFailedError`
        (HTTP 424) carrying the ``FAILED`` payment in ``.payment``.

        This call is never retried automatically after a network error or a
        5xx. To make your own retry safe, send ``order.unique: true``: a
        second create with the same ``order.id`` is refused with a 409.

        To let the payer choose the operator on a hosted page instead, create
        a payment link: ``client.payment_links.create(...)``.
        """
        env = self._api.request("POST", "payments", country=country, body=params)
        return Payment.from_dict(_resource(env, "payment"))

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
        env = self._api.request("GET", "payments", country=country, query=filters)
        return _payment_list(env)

    def get(self, id: str, country: str | None = None) -> Payment:
        """Look up a payment by its ``public_id`` (UUID) or by your ``order.id``.

        Several payments can share one ``order.id`` (unless created with
        ``order.unique``): this returns the first match. Use
        ``list({"order_id": …})`` to see them all — and for an ``order.id``
        that contains a ``/``, which cannot be looked up by path.
        """
        segment = path_segment(id)
        env = self._api.request("GET", f"payments/{segment}", country=country)
        rows = _rows(env, "payments")
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
        env = self._api.request(
            "POST",
            f"payments/{segment}/confirm-otp",
            country=country,
            body={"otp": _otp(otp)},
        )
        return Payment.from_dict(_resource(env, "payment"))

    def resend_otp(self, public_id: str, country: str | None = None) -> None:
        """Send the OTP again to the payer of an ``AWAITING_OTP`` payment."""
        segment = path_segment(public_id, "public_id")
        self._api.request(
            "POST",
            f"payments/{segment}/resend-otp",
            country=country,
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
        env = self._api.request("POST", "payments/links", country=country, body=params)
        return PaymentLink.from_dict(_resource(env, "payment_link"))

    def list(
        self,
        params: Mapping[str, Any] | None = None,
        country: str | None = None,
    ) -> PaymentLinkList:
        """List your payment links, newest first.

        ``params``: ``status`` (one value, or several: ``["active", "paid"]``),
        ``usage``, ``source`` (``api`` / ``ui``), ``per_page`` (max 100), ``page``.
        """
        env = self._api.request("GET", "payments/links", country=country, query=params)
        rows = _rows(env, "payment_links")
        return PaymentLinkList([PaymentLink.from_dict(row) for row in rows], _pagination(env))

    def get(self, id: str, country: str | None = None) -> PaymentLink:
        """Retrieve a payment link by ``public_id`` (``PL-…``) or slug."""
        segment = path_segment(id)
        env = self._api.request("GET", f"payments/links/{segment}", country=country)
        return PaymentLink.from_dict(_resource(env, "payment_link"))

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
        env = self._api.request("PATCH", f"payments/links/{segment}", country=country, body=params)
        return PaymentLink.from_dict(_resource(env, "payment_link"))

    def cancel(self, id: str, country: str | None = None) -> PaymentLink:
        """Cancel a link for good (active or paused → ``canceled``)."""
        segment = path_segment(id)
        env = self._api.request("DELETE", f"payments/links/{segment}", country=country)
        return PaymentLink.from_dict(_resource(env, "payment_link"))

    def pause(self, id: str, country: str | None = None) -> PaymentLink:
        """Pause an active link: the checkout refuses new payments at once.

        A link that is not active is returned unchanged — check ``status``.
        """
        segment = path_segment(id)
        env = self._api.request("POST", f"payments/links/{segment}/pause", country=country)
        return PaymentLink.from_dict(_resource(env, "payment_link"))

    def resume(self, id: str, country: str | None = None) -> PaymentLink:
        """Resume a paused link.

        A link that is not paused is returned unchanged — check ``status``.
        """
        segment = path_segment(id)
        env = self._api.request("POST", f"payments/links/{segment}/resume", country=country)
        return PaymentLink.from_dict(_resource(env, "payment_link"))

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
        env = self._api.request(
            "GET", f"payments/links/{segment}/payments", country=country, query=params
        )
        rows = _rows(env, "payments")
        summary = as_dict(as_dict(env, "data") or {}, "payment_link")
        return PaymentLinkPaymentList(
            [Payment.from_dict(row) for row in rows],
            _pagination(env),
            PaymentLinkSummary.from_dict(summary) if summary is not None else None,
        )


class Transfers:
    """Disbursements: ``client.transfers``."""

    def __init__(self, api: _ApiClient) -> None:
        self._api = api

    def create(self, params: Mapping[str, Any], country: str | None = None) -> Transfer:
        """Create a transfer (send money to a recipient).

        ``params``: ``operator``, ``phone_number``, ``amount``,
        ``order`` (``{"id": …, "unique": bool, "data": {…}}``),
        ``ignore_double_spend_risk`` (bypass the 60 s duplicate guard).

        A status of ``UNCERTAIN`` means the operator did not answer clearly:
        the payout may have been executed — do NOT send it again, the
        ``transfer.*`` webhook carries the verdict. A definitive refusal is
        raised as :class:`~payhub.errors.TransferFailedError` (HTTP 424).

        This call is never retried automatically after a network error or a
        5xx. After such an error the outcome is unknown: look the transfer up
        (``get(order_id)``) before sending again, and prefer
        ``order.unique: true`` so a duplicate is refused with a 409.
        """
        env = self._api.request("POST", "transfers", country=country, body=params)
        return Transfer.from_dict(_resource(env, "transfer"))

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
        env = self._api.request("GET", "transfers", country=country, query=filters)
        return _transfer_list(env)

    def get(self, id: str, country: str | None = None) -> Transfer:
        """Look up a transfer by its ``public_id`` (UUID) or by your ``order.id``.

        Several transfers can share one ``order.id`` (unless created with
        ``order.unique``): this returns the first match. Use
        ``list({"order_id": …})`` to see them all — and for an ``order.id``
        that contains a ``/``, which cannot be looked up by path.
        """
        segment = path_segment(id)
        env = self._api.request("GET", f"transfers/{segment}", country=country)
        rows = _rows(env, "transfers")
        if rows:
            return Transfer.from_dict(rows[0])
        raise NotFoundError(f'No transfer found for "{id}".', "resource_not_found", 404)


class BalanceResource:
    """Wallet balance: ``client.balance``."""

    def __init__(self, api: _ApiClient) -> None:
        self._api = api

    def get(self, country: str | None = None) -> Balance:
        """The merchant's wallet balance for the country."""
        env = self._api.request("GET", "balance", country=country)
        data = as_dict(env, "data")
        if data is None:
            raise _unexpected(env, "`data` is missing")
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
        env = self._api.request("GET", "operators/availability", country=country)
        return as_dict(env, "data") or {}

    def info(self, country: str | None = None) -> dict[str, Any]:
        """Operator capabilities (OTP requirement, amount bounds, instructions).

        This is the authoritative list of operators for the country:
        ``{"operators": {country: {operator: {"otp_required", "min_amount",
        "max_amount", "pay_delay", "payment_instructions"}}}, "supported_countries": […]}``
        """
        env = self._api.request("GET", "operators/info", country=country)
        return as_dict(env, "data") or {}


def _otp(otp: object) -> str:
    # The code is an opaque string ("012345" is not 12345). An int is accepted
    # for backward compatibility, but its leading zeros are already lost.
    if isinstance(otp, bool) or not isinstance(otp, (str, int)):
        raise ConfigurationError("`otp` must be the code as a string, e.g. '012345'.")
    code = str(otp).strip()
    if code == "":
        raise ConfigurationError("`otp` must be the code as a string, e.g. '012345'.")
    return code


def _unexpected(envelope: Mapping[str, Any], what: str) -> ApiError:
    """A 2xx answer that does not carry what the endpoint must return."""
    raw_request_id = envelope.get("request_id")
    return ApiError(
        f"Unexpected response from the API: {what}.",
        "invalid_response",
        200,
        raw_request_id if isinstance(raw_request_id, str) else None,
        data=as_dict(envelope, "data"),
    )


def _resource(envelope: Mapping[str, Any], key: str) -> dict[str, Any]:
    """The object a successful call must return — never an empty stand-in."""
    resource = as_dict(as_dict(envelope, "data") or {}, key)
    if resource is None:
        raise _unexpected(envelope, f"`data.{key}` is missing")
    return resource


def _rows(envelope: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    """The list a successful call must return — a malformed answer is never
    passed off as "no result"."""
    rows = as_list_of_dict(as_dict(envelope, "data") or {}, key)
    if rows is None:
        raise _unexpected(envelope, f"`data.{key}` is missing")
    return rows


def _pagination(envelope: Mapping[str, Any]) -> Pagination | None:
    meta = as_dict(envelope, "meta")
    return Pagination.from_dict(meta) if meta is not None else None


def _payment_list(envelope: Mapping[str, Any]) -> PaymentList:
    rows = _rows(envelope, "payments")
    return PaymentList([Payment.from_dict(row) for row in rows], _pagination(envelope))


def _transfer_list(envelope: Mapping[str, Any]) -> TransferList:
    rows = _rows(envelope, "transfers")
    return TransferList([Transfer.from_dict(row) for row in rows], _pagination(envelope))
