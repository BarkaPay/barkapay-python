"""Minimal webhook receiver (Flask). Mount it at your registered endpoint URL.

Run: BARKAPAY_WEBHOOK_SECRET=whsec_... flask --app examples/webhook run

The verification itself (`handle_webhook`) is framework-free: give it the RAW
request body and the `Barkapay-Signature` header.
"""

from __future__ import annotations

import os
from typing import Any

from barkapay import SignatureVerificationError, webhook

# Deliveries are at-least-once: remember the event ids you already processed
# (use your database or cache in production — this set is only for the demo).
_seen_event_ids: set[str] = set()


def handle_webhook(raw_body: bytes, signature_header: str | None, secret: str) -> tuple[str, int]:
    """Verify, deduplicate and dispatch one delivery. Returns (body, status)."""
    try:
        event = webhook.parse(raw_body, signature_header, secret)
    except SignatureVerificationError:
        return "invalid signature", 400

    event_id = str(event.get("event_id", ""))
    if event_id in _seen_event_ids:
        return "ok", 200  # a retry of something already handled
    _seen_event_ids.add(event_id)

    on_event(event)
    return "ok", 200


def on_event(event: dict[str, Any]) -> None:
    # The body has no event name: derive it from `type` + `status`.
    kind, status = event.get("type"), event.get("status")
    if kind == "payment" and status == "SUCCESSFUL":
        print(f"payment.succeeded {event['public_id']} order={event.get('order_id')}")
    elif kind == "payment" and status == "EXPIRED":
        print(f"payment.expired {event['public_id']}")
    elif kind == "payment":
        print(f"payment.failed {event['public_id']} reason_code={event.get('reason_code')}")
    elif kind == "transfer" and status == "SUCCESSFUL":
        print(f"transfer.succeeded {event['public_id']}")
    elif kind == "transfer":
        print(f"transfer.failed {event['public_id']} reason_code={event.get('reason_code')}")


def create_app() -> Any:
    from flask import Flask, request  # type: ignore[import-not-found]

    app = Flask(__name__)
    # Fail at start-up, not at the first delivery, if the secret is missing.
    secret = os.environ["BARKAPAY_WEBHOOK_SECRET"]

    def barkapay_webhook() -> tuple[str, int]:
        # request.get_data() = the raw bytes. Never request.json / request.form here.
        return handle_webhook(
            request.get_data(),
            request.headers.get(webhook.SIGNATURE_HEADER),
            secret,
        )

    app.add_url_rule("/webhooks/barkapay", view_func=barkapay_webhook, methods=["POST"])
    return app
