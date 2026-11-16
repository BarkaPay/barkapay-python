"""Internal helpers: defensive readers for decoded JSON, JSON (de)coding and
URL-safety checks.

These keep model hydration null-safe and tidy. Not part of the public API.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import uuid
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

from .errors import ConfigurationError

_COUNTRY_RE = re.compile(r"[a-z]{2,3}")
_MAX_SEGMENT_LENGTH = 255


def as_str(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None


def as_int(data: Mapping[str, Any], key: str) -> int | None:
    value = data.get(key)
    # ``bool`` is a subclass of ``int`` — exclude it to mirror PHP's is_int().
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None


def as_amount(data: Mapping[str, Any], key: str) -> int | float | str | None:
    """Amount exactly as returned by the API (a JSON number for mobile money,
    a decimal string elsewhere). Returned as-is to avoid lossy coercion; use
    :func:`to_decimal` for arithmetic."""
    value = data.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, str)):
        return value
    return None


def as_money_str(data: Mapping[str, Any], key: str) -> str | None:
    """A money field documented as a decimal string (``"6.25"``).

    Some responses carry the same field as a bare JSON number (``0``); it is
    normalised to its string form instead of being dropped.
    """
    value = data.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # Shortest round-trip digits, in plain notation (never "1e-07").
        number = to_decimal(value)
        return format(number, "f") if number is not None else None
    return None


def to_decimal(value: object) -> Decimal | None:
    """Exact :class:`~decimal.Decimal` for an API amount, ``None`` when the
    value is absent or not a finite number.

    Floats go through their shortest round-trip representation, so the JSON
    number ``100.1`` becomes ``Decimal("100.1")`` — never
    ``Decimal("100.099999…")``.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        return Decimal(value)
    elif isinstance(value, float):
        try:
            result = Decimal(repr(value))
        except InvalidOperation:
            return None
    elif isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except InvalidOperation:
            return None
    else:
        return None
    return result if result.is_finite() else None


def as_dict(data: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    value = data.get(key)
    return value if isinstance(value, dict) else None


def as_list_of_dict(data: Mapping[str, Any], key: str) -> list[dict[str, Any]] | None:
    value = data.get(key)
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, dict)]


def _json_default(value: object) -> Any:
    """Serialise the few non-JSON types that are natural in request params."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("amounts must be finite numbers")
        if value == value.to_integral_value():
            return int(value)
        # Emit a JSON number only when the text on the wire is EXACTLY the
        # decimal the caller gave — otherwise refuse instead of rounding money.
        as_float = float(value)
        if Decimal(repr(as_float)) != value:
            raise ValueError(f"Decimal {value} cannot be sent as an exact JSON number")
        return as_float
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "datetime values must be timezone-aware "
                "(e.g. datetime.now(timezone.utc)) so the API reads the instant you mean"
            )
        return value.isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def json_encode(value: Any) -> str:
    """Encode request params. ``NaN``/``Infinity`` are refused (not JSON)."""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
            default=_json_default,
        )
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"Request params cannot be encoded as JSON: {exc}") from exc


def json_decode(payload: str | bytes) -> dict[str, Any] | None:
    """Decode a JSON object into a dict. Returns ``None`` for empty input or
    anything that isn't a JSON object."""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    if payload.strip() == "":
        return None
    try:
        decoded = json.loads(payload)
    except (ValueError, TypeError, RecursionError):
        return None
    return decoded if isinstance(decoded, dict) else None


def query_value(value: object) -> str:
    """Render one query-string value the way the API expects it."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, _dt.datetime):
        return value.isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (list, tuple, set, frozenset)):
        # e.g. status=["active", "paid"] -> "active,paid"
        return ",".join(query_value(item) for item in value)
    return str(value)


def generate_request_id() -> str:
    """RFC-4122 v4 UUID, used as the ``X-Request-Id`` correlation id."""
    return str(uuid.uuid4())


def normalize_country(country: object) -> str:
    """Lower-case and validate a country code before it reaches the URL path."""
    if not isinstance(country, str):
        raise ConfigurationError("The country code must be a string such as 'bf'.")
    code = country.strip().lower()
    if not _COUNTRY_RE.fullmatch(code):
        raise ConfigurationError(
            "Invalid country code: expected a short lowercase code such as 'bf', 'bj' or 'sn'."
        )
    return code


def path_segment(value: object, name: str = "id") -> str:
    """Validate an identifier and percent-encode it as ONE URL path segment.

    An empty identifier would silently turn ``GET /payments/{id}`` into the
    *list* endpoint (and return somebody else's row); ``.``/``..`` and control
    characters would let a value walk to another route. All are refused.
    """
    if not isinstance(value, str):
        raise ConfigurationError(f"`{name}` must be a non-empty string.")
    if value.strip() == "":
        raise ConfigurationError(f"`{name}` must be a non-empty string.")
    if len(value) > _MAX_SEGMENT_LENGTH:
        raise ConfigurationError(f"`{name}` is too long (max {_MAX_SEGMENT_LENGTH} characters).")
    if value in (".", "..") or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise ConfigurationError(f"`{name}` contains characters that are not allowed in an id.")
    return quote(value, safe="")
