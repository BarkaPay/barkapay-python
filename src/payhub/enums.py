"""Status and reason-code enumerations."""

from __future__ import annotations

from enum import Enum
from typing import TypeVar

_E = TypeVar("_E", bound="_StrEnum")


class _StrEnum(str, Enum):
    """A string enum whose members compare equal to their string values."""

    def __str__(self) -> str:
        # ``str()`` / f-strings give the wire value on every Python version
        # (3.11 changed the default for mixed-in enums).
        return str(self.value)

    @classmethod
    def try_from(cls: type[_E], value: str | None) -> _E | None:
        """Return the member for ``value`` or ``None`` for an unknown value.

        A value introduced by the API after this SDK was released never
        raises — it hydrates as ``None`` (the models keep the original string
        in ``raw_status``).
        """
        if value is None:
            return None
        try:
            return cls(value)
        except ValueError:
            return None


class PaymentStatus(_StrEnum):
    CREATED = "CREATED"
    AWAITING_OTP = "AWAITING_OTP"
    QUEUED_FOR_PROCESSING = "QUEUED_FOR_PROCESSING"
    PROCESSING_OPERATOR = "PROCESSING_OPERATOR"
    PROCESSING_SYSTEM = "PROCESSING_SYSTEM"
    SUCCESSFUL = "SUCCESSFUL"
    FAILED = "FAILED"
    #: Pure expiration — the payer never completed (OTP never confirmed,
    #: checkout session expired). Real refusals stay ``FAILED``.
    EXPIRED = "EXPIRED"
    COMPLIANCE_FLAGGED = "COMPLIANCE_FLAGGED"
    CANCELED = "CANCELED"

    def is_final(self) -> bool:
        """No further transitions: SUCCESSFUL, FAILED, EXPIRED or CANCELED."""
        return self in (
            PaymentStatus.SUCCESSFUL,
            PaymentStatus.FAILED,
            PaymentStatus.EXPIRED,
            PaymentStatus.CANCELED,
        )


class TransferStatus(_StrEnum):
    CREATED = "CREATED"
    PROCESSING_OPERATOR = "PROCESSING_OPERATOR"
    PROCESSING_SYSTEM = "PROCESSING_SYSTEM"
    SUCCESSFUL = "SUCCESSFUL"
    FAILED = "FAILED"
    COMPLIANCE_FLAGGED = "COMPLIANCE_FLAGGED"
    REFUNDED = "REFUNDED"
    #: Legacy spelling of ``UNCERTAIN``, kept for old rows.
    UNRESOLVED = "UNRESOLVED"
    #: The operator's answer is unknown (timeout, ambiguous reply). NOT final
    #: and NOT a failure: the payout may have been executed — never re-send
    #: it, wait for the ``transfer.*`` webhook.
    UNCERTAIN = "UNCERTAIN"

    def is_final(self) -> bool:
        """No further transitions: SUCCESSFUL, FAILED or REFUNDED."""
        return self in (TransferStatus.SUCCESSFUL, TransferStatus.FAILED, TransferStatus.REFUNDED)


class PaymentLinkStatus(_StrEnum):
    ACTIVE = "active"
    #: Merchant-reversible hold (``pause`` / ``resume``).
    PAUSED = "paused"
    #: Single-use link closed by its first successful payment.
    PAID = "paid"
    EXPIRED = "expired"
    CANCELED = "canceled"
    ADMIN_BLOCKED = "admin_blocked"


class ReasonCode(_StrEnum):
    """Stable failure codes carried by ``reason_code`` on a failed or expired
    payment / transfer (API responses and webhooks).

    The catalogue only grows: models expose ``reason_code`` as a plain string,
    compare it with these members (``payment.reason_code == ReasonCode.PAYER_REFUSED``).
    ``status`` is the verdict — never infer that money moved from a reason code.
    """

    PAYER_INSUFFICIENT_FUNDS = "payer_insufficient_funds"
    PAYER_REFUSED = "payer_refused"
    PAYER_UNREACHABLE = "payer_unreachable"
    INVALID_OTP = "invalid_otp"
    OTP_EXPIRED = "otp_expired"
    EXPIRED = "expired"
    RECIPIENT_INVALID = "recipient_invalid"
    OPERATOR_UNAVAILABLE = "operator_unavailable"
    OPERATOR_TIMEOUT = "operator_timeout"
    UNCONFIRMED_AFTER_WINDOW = "unconfirmed_after_window"
    PAYMENT_NOT_COMPLETED = "payment_not_completed"
    AMOUNT_OUT_OF_BOUNDS = "amount_out_of_bounds"
    DUPLICATE = "duplicate"
    PROVIDER_FLOAT_INSUFFICIENT = "provider_float_insufficient"
    INTERNAL_ERROR = "internal_error"
    UNKNOWN = "unknown"
