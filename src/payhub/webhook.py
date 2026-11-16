"""Verify the ``Barkapay-Signature`` header on incoming webhooks.

The header is ``t=<unix_ts>,v1=<hex>`` where
``v1 = HMAC-SHA256(secret, "{t}.{raw body}")``, keyed with the signing secret
of your webhook endpoint (``whsec_…``).

Always verify against the RAW request body — the exact bytes received, before
any JSON parsing. A body that was decoded and re-encoded (different key order,
spacing or escaping) no longer matches the signature.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import time
from typing import Any, Union

from ._util import json_decode
from .errors import ConfigurationError, SignatureVerificationError

#: Name of the HTTP header that carries the signature.
SIGNATURE_HEADER = "Barkapay-Signature"

#: Default replay window, in seconds.
DEFAULT_TOLERANCE = 300

_Payload = Union[str, bytes, bytearray, memoryview, None]
_Secret = Union[str, bytes]

_MAX_HEADER_LENGTH = 4096
_TIMESTAMP_RE = re.compile(r"[0-9]{1,12}")
_HEX_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")

__all__ = ["DEFAULT_TOLERANCE", "SIGNATURE_HEADER", "parse", "verify"]


def verify(
    payload: _Payload,
    signature_header: str | None,
    secret: _Secret,
    tolerance: float = DEFAULT_TOLERANCE,
) -> bool:
    """Return ``True`` if the signature is valid, ``False`` otherwise.

    :param payload: the RAW request body (``bytes`` preferred, or ``str``;
        ``None`` is read as an empty body).
    :param signature_header: the ``Barkapay-Signature`` header value
        (``None`` / empty when the header is missing → ``False``).
    :param secret: your endpoint signing secret (``whsec_…``).
    :param tolerance: replay window in seconds — a delivery whose timestamp
        is further than this from now is rejected. Must be positive (default
        300).
    :raises ConfigurationError: if ``secret`` is empty, ``tolerance`` is
        not positive or ``payload`` is not the raw body — integration mistakes,
        never reported as a plain ``False``.
    """
    try:
        _assert(payload, signature_header, secret, tolerance)
        return True
    except SignatureVerificationError:
        return False


def parse(
    payload: _Payload,
    signature_header: str | None,
    secret: _Secret,
    tolerance: float = DEFAULT_TOLERANCE,
) -> dict[str, Any]:
    """Verify and decode the webhook body in one step.

    :raises SignatureVerificationError: if the signature is missing, invalid
        or stale, or if the body is not a JSON object.
    :raises ConfigurationError: if ``secret`` is empty, ``tolerance`` is
        not positive or ``payload`` is not the raw body.
    """
    _assert(payload, signature_header, secret, tolerance)

    data = json_decode(_raw_body(payload))
    if data is None:
        raise SignatureVerificationError("Webhook payload is not a JSON object.")
    return data


def _secret_key(secret: object) -> bytes:
    # A configuration mistake must be loud, not a silent "False": an empty
    # secret would make every signature forgeable.
    if isinstance(secret, (bytes, bytearray)):
        key = bytes(secret)  # binary key: used exactly as given
    elif isinstance(secret, str):
        # Stray whitespace (a trailing newline from a secrets file) is not
        # part of a whsec_… secret.
        key = secret.strip().encode("utf-8")
    else:
        raise ConfigurationError("The webhook signing secret must be a string.")
    if key.strip() == b"":
        raise ConfigurationError(
            "The webhook signing secret is empty: refusing to verify. "
            "Set the whsec_… secret of your endpoint."
        )
    return key


def _raw_body(payload: object) -> bytes:
    if payload is None:
        return b""  # a request without a body (it cannot carry a valid signature)
    if isinstance(payload, str):
        try:
            return payload.encode("utf-8")
        except UnicodeEncodeError:
            # Lone surrogates: the text is not what was sent on the wire.
            raise SignatureVerificationError(
                "The webhook payload is not valid text; pass the raw request bytes."
            ) from None
    if isinstance(payload, (bytes, bytearray, memoryview)):
        return bytes(payload)
    # A parsed body (dict) can never match the signature: a programming
    # mistake, reported loudly instead of a puzzling "invalid signature".
    raise ConfigurationError(
        "The webhook payload must be the RAW request body (bytes or str), not a parsed object."
    )


def _assert(payload: _Payload, header: str | None, secret: _Secret, tolerance: float) -> None:
    key = _secret_key(secret)
    if (
        isinstance(tolerance, bool)
        or not isinstance(tolerance, (int, float))
        or not math.isfinite(tolerance)
        or tolerance <= 0
    ):
        # 0, a negative value, NaN or infinity would switch the replay window off.
        raise ConfigurationError(
            "`tolerance` must be a positive, finite number of seconds (default 300)."
        )

    # The header first: an unsigned request is "invalid signature", whatever
    # its body looks like.
    parsed = _parse_header(header)
    if parsed is None:
        raise SignatureVerificationError(f"Malformed or missing {SIGNATURE_HEADER} header.")

    timestamp, signatures = parsed
    raw = _raw_body(payload)

    if abs(time.time() - timestamp) > tolerance:
        raise SignatureVerificationError("Webhook timestamp is outside the tolerance window.")

    message = str(timestamp).encode("ascii") + b"." + raw
    expected = hmac.new(key, message, hashlib.sha256).hexdigest().encode("ascii")

    # Constant-time comparison against every candidate (no early exit).
    matched = False
    for candidate in signatures:
        if hmac.compare_digest(expected, candidate.lower().encode("ascii")):
            matched = True
    if not matched:
        raise SignatureVerificationError("Webhook signature mismatch.")


def _parse_header(header: object) -> tuple[int, list[str]] | None:
    """Strictly parse ``t=<digits>,v1=<64 hex>[,v1=<64 hex>…]``.

    Returns ``None`` for anything else: missing or duplicated ``t``, no
    ``v1``, a ``v1`` that is not a SHA-256 hex digest, an element without
    ``=``. Unknown keys (a future ``v2``) are ignored.
    """
    if isinstance(header, (bytes, bytearray)):
        try:
            header = bytes(header).decode("ascii")
        except UnicodeDecodeError:
            return None
    if not isinstance(header, str) or header == "" or len(header) > _MAX_HEADER_LENGTH:
        return None

    timestamp: int | None = None
    signatures: list[str] = []

    for part in header.split(","):
        part = part.strip()
        if part == "":
            continue  # tolerate a stray comma
        key, separator, value = part.partition("=")
        if separator == "":
            return None
        if key == "t":
            if timestamp is not None or not _TIMESTAMP_RE.fullmatch(value):
                return None
            timestamp = int(value)
        elif key == "v1":
            if not _HEX_SHA256_RE.fullmatch(value):
                return None
            signatures.append(value)

    if timestamp is None or timestamp <= 0 or not signatures:
        return None

    return timestamp, signatures
