"""Collect a payment (mobile money).

Run: BARKAPAY_API_KEY=key_id:secret python examples/collect.py
"""

from __future__ import annotations

import os
import sys
import time

import payhub
from payhub import NetworkError, PayHubError, PaymentFailedError, PaymentStatus


def main(client: payhub.PayHub) -> payhub.Payment | None:
    order_id = f"ORDER-{int(time.time())}"
    try:
        # `otp` is only needed for operators that take the payer's code
        # up-front (ORANGE). Others answer AWAITING_OTP (confirm in a second
        # call) or PROCESSING_OPERATOR (the verdict arrives by webhook).
        # Always branch on the returned status.
        payment = client.payments.create(
            {
                "operator": "ORANGE",
                "phone_number": "74123456",
                "amount": 10000,
                "otp": "123456",
                # unique=True: a second create with the same id is refused (409),
                # so a retry after a network error can never charge twice.
                "order": {"id": order_id, "unique": True},
            }
        )
    except PaymentFailedError as exc:
        # The payer or the operator refused. The FAILED payment is attached.
        failed = exc.payment
        print(f"Payment refused: {exc} (reason_code={exc.reason_code})", file=sys.stderr)
        return failed
    except NetworkError as exc:
        # No answer: the payer MAY have been charged. Never re-send blindly —
        # check with a list filtered on your order id first.
        print(f"No answer ({exc}); checking {order_id} before anything else", file=sys.stderr)
        try:
            recorded = client.payments.list({"order_id": order_id})
        except PayHubError:
            return None  # still unknown: check again later, do not re-send
        return recorded[0] if len(recorded) else None
    except PayHubError as exc:
        print(f"barkapay error: {exc}", file=sys.stderr)
        return None

    if payment.status is PaymentStatus.SUCCESSFUL:
        print(f"Payment {payment.public_id} settled — fees {payment.fees} {payment.currency}")
    elif payment.status is PaymentStatus.AWAITING_OTP:
        print(f"Payment {payment.public_id} waits for the payer's OTP (payments.confirm_otp)")
    else:
        # PROCESSING_OPERATOR…: not final yet — wait for the payment.* webhook.
        print(f"Payment {payment.public_id} is {payment.raw_status}")
        if payment.redirect_url:
            print(f"Send the payer to {payment.redirect_url}")
    return payment


if __name__ == "__main__":
    main(payhub.PayHub(api_key=os.environ["BARKAPAY_API_KEY"], country="bf"))
