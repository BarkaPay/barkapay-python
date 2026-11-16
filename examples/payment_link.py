"""Create a payment link (hosted checkout) and follow its payments.

Run: BARKAPAY_API_KEY=key_id:secret python examples/payment_link.py
"""

from __future__ import annotations

import os
import sys

import barkapay
from barkapay import BarkaPayError


def main(client: barkapay.BarkaPay) -> barkapay.PaymentLink | None:
    try:
        link = client.payment_links.create(
            {
                "title": "Invoice INV-2026-001",
                "amount": {"type": "fixed", "value": 15000, "currency": "XOF"},
                "usage": "single",  # closed by its first successful payment
                "order": {"id": "INV-2026-001"},
                "redirect": {"success_url": "https://shop.example/thanks"},
            }
        )
        print(f"Send your customer to {link.url}")

        # Later: what was paid through this link (in this country)?
        page = client.payment_links.list_payments(link.public_id, {"status": "SUCCESSFUL"})
        for payment in page:
            print(f"  {payment.public_id}: {payment.amount} {payment.currency}")
        if page.payment_link is not None:
            print(f"Collected so far: {page.payment_link.stats.total_collected}")

        # A link can be paused, resumed, edited or canceled:
        #   client.payment_links.pause(link.public_id)
        #   client.payment_links.resume(link.public_id)
        #   client.payment_links.update(link.public_id, {"title": "New title"})
        #   client.payment_links.cancel(link.public_id)
        return link
    except BarkaPayError as exc:
        print(f"barkapay error: {exc}", file=sys.stderr)
        return None


if __name__ == "__main__":
    main(barkapay.BarkaPay(api_key=os.environ["BARKAPAY_API_KEY"], country="bf"))
