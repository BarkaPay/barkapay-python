"""Official Python client for the barkapay API — mobile money collections,
payment links and disbursements behind one contract.

Quick start::

    import payhub

    client = payhub.PayHub(api_key="key_id:secret", country="bf")
    payment = client.payments.create({
        "operator": "ORANGE",
        "phone_number": "74123456",
        "amount": 10000,
        "otp": "123456",
        "order": {"id": "ORDER-2026-001"},
    })
    print(payment.public_id, payment.status)
"""

from __future__ import annotations

from . import webhook
from ._version import __version__
from .client import PayHub
from .enums import PaymentLinkStatus, PaymentStatus, ReasonCode, TransferStatus
from .errors import (
    ApiError,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    ConflictError,
    InsufficientBalanceError,
    NetworkError,
    NotFoundError,
    PayHubError,
    PaymentFailedError,
    RateLimitError,
    ServerError,
    ServiceUnavailableError,
    SignatureVerificationError,
    TransferFailedError,
    ValidationError,
)
from .http import Request, Response, Transport, UrllibTransport
from .models import (
    Balance,
    Pagination,
    Payment,
    PaymentLink,
    PaymentLinkAmount,
    PaymentLinkList,
    PaymentLinkPaymentList,
    PaymentLinkStats,
    PaymentLinkSummary,
    PaymentList,
    Transfer,
    TransferList,
)

__all__ = [
    "ApiError",
    "AuthenticationError",
    "AuthorizationError",
    "Balance",
    "ConfigurationError",
    "ConflictError",
    "InsufficientBalanceError",
    "NetworkError",
    "NotFoundError",
    "Pagination",
    "PayHub",
    "PayHubError",
    "Payment",
    "PaymentFailedError",
    "PaymentLink",
    "PaymentLinkAmount",
    "PaymentLinkList",
    "PaymentLinkPaymentList",
    "PaymentLinkStats",
    "PaymentLinkStatus",
    "PaymentLinkSummary",
    "PaymentList",
    "PaymentStatus",
    "RateLimitError",
    "ReasonCode",
    "Request",
    "Response",
    "ServerError",
    "ServiceUnavailableError",
    "SignatureVerificationError",
    "Transfer",
    "TransferFailedError",
    "TransferList",
    "TransferStatus",
    "Transport",
    "UrllibTransport",
    "ValidationError",
    "__version__",
    "webhook",
]
