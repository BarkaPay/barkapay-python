"""The exception hierarchy raised by the SDK.

Catch everything from the SDK with :class:`PayHubError`. API errors map the most
common cases to :class:`ApiError` subclasses so you can catch them precisely.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import Payment, Transfer


class PayHubError(Exception):
    """Base class for every exception the SDK raises."""


class ApiError(PayHubError):
    """Base class for every error returned by the API (any response with the
    standard error envelope or a non-2xx status).

    Attributes:
        code: machine-readable code from the envelope (e.g. ``validation_error``).
        http_status: HTTP status of the response.
        request_id: correlation id echoed by the API — quote it to support.
        errors: field → messages map (validation errors), else empty.
        data: the envelope's ``data`` block. A failed payment / transfer comes
            back here (see :attr:`payment` / :attr:`transfer`).
        retry_after: seconds the API asked you to wait (``Retry-After``), if any.
        headers: response headers, lower-cased (e.g. ``x-ratelimit-remaining``).
    """

    def __init__(
        self,
        message: str,
        code: str = "",
        http_status: int = 0,
        request_id: str | None = None,
        errors: Mapping[str, Any] | None = None,
        *,
        data: Mapping[str, Any] | None = None,
        retry_after: float | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.request_id = request_id
        self.errors: dict[str, Any] = dict(errors) if errors else {}
        self.data: dict[str, Any] = dict(data) if data else {}
        self.retry_after = retry_after
        self.headers: dict[str, str] = (
            {str(k).lower(): str(v) for k, v in headers.items()} if headers else {}
        )

    @property
    def payment(self) -> Payment | None:
        """The payment snapshot carried by a failure response, if any.

        A payment refused by the payer or the operator is answered with an
        error status (424 / 503 / 500) **and** the ``FAILED`` payment object —
        persist its ``public_id`` and read ``reason_code`` from it.
        """
        snapshot = self.data.get("payment")
        if not isinstance(snapshot, dict):
            return None
        from .models import Payment

        return Payment.from_dict(snapshot)

    @property
    def transfer(self) -> Transfer | None:
        """The transfer snapshot carried by a failure response, if any."""
        snapshot = self.data.get("transfer")
        if not isinstance(snapshot, dict):
            return None
        from .models import Transfer

        return Transfer.from_dict(snapshot)

    @property
    def reason_code(self) -> str | None:
        """Stable failure code of the failed payment / transfer, if any
        (e.g. ``payer_insufficient_funds``). See :class:`payhub.ReasonCode`."""
        for key in ("payment", "transfer"):
            snapshot = self.data.get(key)
            if isinstance(snapshot, dict):
                value = snapshot.get("reason_code")
                if isinstance(value, str):
                    return value
        return None

    @classmethod
    def from_envelope(
        cls,
        http_status: int,
        envelope: Mapping[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
        retry_after: float | None = None,
    ) -> ApiError:
        """Build the most specific exception for an error envelope."""
        raw_code = envelope.get("code")
        code = raw_code if isinstance(raw_code, str) else ""

        raw_message = envelope.get("message")
        message = (
            raw_message
            if isinstance(raw_message, str) and raw_message != ""
            else f"barkapay API error (HTTP {http_status})."
        )

        raw_request_id = envelope.get("request_id")
        request_id = raw_request_id if isinstance(raw_request_id, str) else None

        raw_errors = envelope.get("errors")
        errors = raw_errors if isinstance(raw_errors, dict) else {}

        raw_data = envelope.get("data")
        data = raw_data if isinstance(raw_data, dict) else {}

        error_cls = _class_for(code, http_status)
        return error_cls(
            message,
            code,
            http_status,
            request_id,
            errors,
            data=data,
            retry_after=retry_after,
            headers=headers,
        )


class AuthenticationError(ApiError):
    """401 — missing or invalid credentials."""


class AuthorizationError(ApiError):
    """403 / 410 / 451 — authenticated, but not allowed (scope, IP, account state)."""


class ValidationError(ApiError):
    """422 — request validation failed; ``errors`` holds the field → messages map.

    Also raised for ``400 invalid_input`` (unreadable body), where ``errors``
    is empty.
    """


class NotFoundError(ApiError):
    """404 — unknown endpoint, country, or resource."""


class ConflictError(ApiError):
    """409 — duplicate / already-exists (``order.unique`` collision, or the
    60 s duplicate-transfer guard)."""


class InsufficientBalanceError(ApiError):
    """400 ``insufficient_balance`` — your wallet cannot cover this transfer.
    Nothing was sent to the operator."""


class PaymentFailedError(ApiError):
    """424 ``payment_failed`` — the payer or the operator definitively refused
    this payment. The ``FAILED`` payment is in :attr:`payment`. Do not retry
    the same request blindly: the verdict is final for this attempt."""


class TransferFailedError(ApiError):
    """424 ``transfer_failed`` — the operator definitively refused this
    transfer. The ``FAILED`` transfer is in :attr:`transfer`; the reserved
    funds are released."""


class RateLimitError(ApiError):
    """429 — rate limit reached. The request was NOT executed; wait
    :attr:`retry_after` seconds before retrying."""


class ServiceUnavailableError(ApiError):
    """503 — operator or service temporarily unavailable.

    On a create call this can still carry a recorded ``FAILED`` payment /
    transfer (:attr:`payment` / :attr:`transfer`): a retry is a NEW attempt.
    """


class ServerError(ApiError):
    """5xx — unexpected server error on barkapay's side."""


class NetworkError(PayHubError):
    """The HTTP exchange did not complete (DNS, TLS, connection, timeout).

    For a request that creates money (payment, transfer) the outcome is then
    UNKNOWN — look the operation up by your ``order.id`` before trying again.
    """


class SignatureVerificationError(PayHubError):
    """A webhook signature could not be verified (bad signature, stale, malformed)."""


class ConfigurationError(PayHubError, ValueError):
    """The SDK was constructed or called with invalid arguments."""


_CODE_MAP: dict[str, type[ApiError]] = {
    "unauthenticated": AuthenticationError,
    "invalid_api_key": AuthenticationError,
    "incorrect_credentials": AuthenticationError,
    "unauthorized": AuthorizationError,
    "ip_not_allowed": AuthorizationError,
    "merchant_suspended": AuthorizationError,
    "merchant_closed": AuthorizationError,
    "validation_error": ValidationError,
    "invalid_input": ValidationError,
    "resource_not_found": NotFoundError,
    "duplicate_transaction": ConflictError,
    "resource_already_exists": ConflictError,
    "insufficient_balance": InsufficientBalanceError,
    "payment_failed": PaymentFailedError,
    "transfer_failed": TransferFailedError,
    "too_many_requests": RateLimitError,
    "service_unavailable": ServiceUnavailableError,
    "operator_unavailable": ServiceUnavailableError,
}

_STATUS_MAP: dict[int, type[ApiError]] = {
    401: AuthenticationError,
    403: AuthorizationError,
    404: NotFoundError,
    409: ConflictError,
    410: AuthorizationError,
    422: ValidationError,
    429: RateLimitError,
    451: AuthorizationError,
    503: ServiceUnavailableError,
}


def _class_for(code: str, http_status: int) -> type[ApiError]:
    mapped = _CODE_MAP.get(code)
    if mapped is not None:
        return mapped
    by_status = _STATUS_MAP.get(http_status)
    if by_status is not None:
        return by_status
    if http_status >= 500:
        return ServerError
    return ApiError
