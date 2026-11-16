"""Webhook verification: what rifle (the webhook dispatcher) really sends,
and everything a forged or broken delivery can throw at the verifier."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from payhub import ConfigurationError, SignatureVerificationError, webhook

SECRET = "whsec_test_secret"
# Body exactly as rifle serialises it (unescaped slashes and unicode).
BODY = json.dumps(
    {
        "public_id": "0198c2f1-9f6a-7c4e-b1d2-3a4b5c6d7e8f",
        "amount": 10000,
        "phone_number": "74123456",
        "country": "BF",
        "status": "SUCCESSFUL",
        "order_id": "ORDER/2026-001",
        "provider_transaction_id": "MP261116.1234.A12345",
        "order_data": {"client": "Koné"},
        "reason": "Payment completed.",
        "app_label": "shop",
        "reason_code": None,
        "provider_reason": None,
        "link_public_id": None,
        "type": "payment",
        "fees": "250.00",
        "event_id": "dlv:0198c2f1-9f6a-7c4e-b1d2-3a4b5c6d7e8f:payment.succeeded:whk_1",
        "timestamp": "2026-11-16T08:00:05+00:00",
    },
    ensure_ascii=False,
    separators=(",", ":"),
).encode("utf-8")


def _digest(body: bytes, timestamp: int, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def _header(body: bytes = BODY, timestamp: int | None = None, secret: str = SECRET) -> str:
    ts = int(time.time()) if timestamp is None else timestamp
    return f"t={ts},v1={_digest(body, ts, secret)}"


def test_the_header_name_is_barkapay_signature() -> None:
    assert webhook.SIGNATURE_HEADER == "Barkapay-Signature"
    assert webhook.DEFAULT_TOLERANCE == 300


def test_a_genuine_delivery_verifies_and_parses() -> None:
    header = _header()
    assert webhook.verify(BODY, header, SECRET) is True

    event = webhook.parse(BODY, header, SECRET)
    assert event["type"] == "payment"
    assert event["status"] == "SUCCESSFUL"
    assert event["order_data"] == {"client": "Koné"}
    assert event["event_id"].startswith("dlv:")


@pytest.mark.parametrize("wrap", [bytes, bytearray, memoryview, lambda b: b.decode("utf-8")])
def test_raw_body_is_accepted_as_bytes_like_or_str(wrap: object) -> None:
    payload = wrap(BODY)  # type: ignore[operator]
    assert webhook.verify(payload, _header(), SECRET) is True


def test_a_reencoded_body_no_longer_matches() -> None:
    # The classic trap: verifying json.dumps(request.json) instead of the raw bytes.
    reencoded = json.dumps(json.loads(BODY)).encode()
    assert reencoded != BODY
    assert webhook.verify(reencoded, _header(), SECRET) is False


def test_a_parsed_body_is_a_loud_integration_error() -> None:
    with pytest.raises(ConfigurationError):
        webhook.verify(json.loads(BODY), _header(), SECRET)
    with pytest.raises(ConfigurationError):
        webhook.parse(json.loads(BODY), _header(), SECRET)


def test_tampered_body_wrong_secret_and_stale_timestamp_are_rejected() -> None:
    assert webhook.verify(BODY.replace(b"10000", b"99999"), _header(), SECRET) is False
    assert webhook.verify(BODY, _header(), "whsec_other") is False
    assert webhook.verify(BODY, _header(timestamp=int(time.time()) - 301), SECRET) is False


def test_a_timestamp_from_the_future_is_rejected_too() -> None:
    assert webhook.verify(BODY, _header(timestamp=int(time.time()) + 600), SECRET) is False
    assert webhook.verify(BODY, _header(timestamp=int(time.time()) + 60), SECRET) is True


def test_tolerance_is_configurable() -> None:
    old = _header(timestamp=int(time.time()) - 500)
    assert webhook.verify(BODY, old, SECRET) is False
    assert webhook.verify(BODY, old, SECRET, tolerance=600) is True
    assert webhook.verify(BODY, old, SECRET, tolerance=600.5) is True
    assert webhook.verify(BODY, old, SECRET, tolerance=0) is True  # explicit opt-out


@pytest.mark.parametrize(
    "tolerance", [-1, -0.5, True, "300", None, float("nan"), float("inf"), float("-inf")]
)
def test_an_invalid_tolerance_never_silently_disables_the_replay_window(tolerance: object) -> None:
    old = _header(timestamp=int(time.time()) - 100_000)
    with pytest.raises(ConfigurationError):
        webhook.verify(BODY, old, SECRET, tolerance=tolerance)  # type: ignore[arg-type]


@pytest.mark.parametrize("secret", ["", "   ", "\n", b"", None, 123])
def test_an_empty_secret_is_refused_instead_of_accepting_forgeries(secret: object) -> None:
    ts = int(time.time())
    forged = f"t={ts},v1={hmac.new(b'', f'{ts}.'.encode() + BODY, hashlib.sha256).hexdigest()}"
    with pytest.raises(ConfigurationError):
        webhook.verify(BODY, forged, secret)  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        webhook.parse(BODY, forged, secret)  # type: ignore[arg-type]


def test_secret_may_be_bytes_and_stray_whitespace_is_ignored() -> None:
    assert webhook.verify(BODY, _header(), SECRET.encode()) is True
    assert webhook.verify(BODY, _header(), SECRET + "\n") is True


def test_several_v1_signatures_are_supported() -> None:
    ts = int(time.time())
    good = _digest(BODY, ts)
    other = _digest(BODY, ts, "whsec_previous")
    assert webhook.verify(BODY, f"t={ts},v1={other},v1={good}", SECRET) is True
    assert webhook.verify(BODY, f"t={ts},v1={good},v1={other}", SECRET) is True
    assert webhook.verify(BODY, f"t={ts},v1={other},v1={other}", SECRET) is False


def test_unknown_scheme_keys_are_ignored_and_spacing_is_tolerated() -> None:
    ts = int(time.time())
    good = _digest(BODY, ts)
    assert webhook.verify(BODY, f"t={ts}, v1={good}, v2=whatever", SECRET) is True
    assert webhook.verify(BODY, f"v1={good},t={ts}", SECRET) is True
    assert webhook.verify(BODY, f"t={ts},v1={good.upper()}", SECRET) is True
    assert webhook.verify(BODY, f"t={ts},v2={good}", SECRET) is False  # no v1 at all


def _valid_parts() -> tuple[int, str]:
    ts = int(time.time())
    return ts, _digest(BODY, ts)


@pytest.mark.parametrize(
    "make_header",
    [
        lambda ts, sig: "",
        lambda ts, sig: "not-a-signature",
        lambda ts, sig: f"t={ts}",
        lambda ts, sig: f"v1={sig}",
        lambda ts, sig: f"t={ts},v1=",
        lambda ts, sig: f"t={ts},v1={sig[:-2]}",  # truncated digest
        lambda ts, sig: f"t={ts},v1={sig}00",  # too long
        lambda ts, sig: f"t={ts},v1={'z' * 64}",  # not hex
        lambda ts, sig: f"t={ts},v1=é{sig[1:]}",  # non-ASCII
        lambda ts, sig: f"t={ts},t={ts},v1={sig}",  # duplicated timestamp
        lambda ts, sig: f"t={ts},t={ts - 100000},v1={sig}",
        lambda ts, sig: f"t=,v1={sig}",
        lambda ts, sig: f"t=-{ts},v1={sig}",
        lambda ts, sig: f"t=+{ts},v1={sig}",
        lambda ts, sig: f"t={ts}.0,v1={sig}",
        lambda ts, sig: f"t=0x{ts:x},v1={sig}",
        lambda ts, sig: f"t=²,v1={sig}",  # str.isdigit() is True, int() raises
        lambda ts, sig: f"t=١٢٣,v1={sig}",  # non-ASCII digits
        lambda ts, sig: f"t=0,v1={sig}",
        lambda ts, sig: f"t={'9' * 13},v1={sig}",
        lambda ts, sig: f"t={ts},v1={sig},garbage",
        lambda ts, sig: f"t={ts};v1={sig}",
        lambda ts, sig: (
            f"t={ts},v1={sig}" + "," + "v1=" + "a" * 64 + ("," + "x=1") * 2000
        ),  # oversized
    ],
)
def test_malformed_headers_are_rejected_without_raising(make_header: object) -> None:
    ts, sig = _valid_parts()
    header = make_header(ts, sig)  # type: ignore[operator]
    assert webhook.verify(BODY, header, SECRET) is False
    with pytest.raises(SignatureVerificationError):
        webhook.parse(BODY, header, SECRET)


@pytest.mark.parametrize("header", [None, 12345, ["t=1"], b"\xff\xfe"])
def test_a_missing_or_non_text_header_is_rejected_without_raising(header: object) -> None:
    assert webhook.verify(BODY, header, SECRET) is False  # type: ignore[arg-type]


def test_header_given_as_ascii_bytes_is_accepted() -> None:
    assert webhook.verify(BODY, _header().encode("ascii"), SECRET) is True  # type: ignore[arg-type]


def test_parse_requires_a_json_object_even_with_a_valid_signature() -> None:
    for body in (b"[1,2,3]", b"not json", b"", b'"text"'):
        with pytest.raises(SignatureVerificationError):
            webhook.parse(body, _header(body), SECRET)


def test_verification_uses_a_constant_time_comparison(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr("payhub.webhook.hmac.compare_digest", spy)
    ts = int(time.time())
    good = _digest(BODY, ts)
    assert webhook.verify(BODY, f"t={ts},v1={good},v1={'a' * 64}", SECRET) is True
    # Every candidate is compared, even after a match (no early exit).
    assert len(calls) == 2


def test_signature_matches_the_dispatcher_algorithm() -> None:
    # hash_hmac('sha256', $t . '.' . $body, $secret) — fixed vector.
    body = b'{"type":"payment","status":"SUCCESSFUL"}'
    signature = hmac.new(b"whsec_vector", b"1763280000." + body, hashlib.sha256).hexdigest()
    header = f"t=1763280000,v1={signature}"
    assert webhook.verify(body, header, "whsec_vector", tolerance=0) is True
    assert webhook.parse(body, header, "whsec_vector", tolerance=0)["status"] == "SUCCESSFUL"
