"""Disburse a transfer (payout).

Run: BARKAPAY_API_KEY=key_id:secret python examples/disburse.py
"""

from __future__ import annotations

import os
import sys
import time
from decimal import Decimal

import payhub
from payhub import (
    ConflictError,
    InsufficientBalanceError,
    NetworkError,
    PayHubError,
    TransferFailedError,
    TransferStatus,
)


def main(client: payhub.PayHub) -> payhub.Transfer | None:
    order_id = f"PAYOUT-{int(time.time())}"
    try:
        transfer = client.transfers.create(
            {
                "operator": "ORANGE",
                "phone_number": "74123456",
                "amount": Decimal("5000"),
                # unique=True: sending the same order twice is refused (409).
                "order": {"id": order_id, "unique": True},
            }
        )
    except InsufficientBalanceError as exc:
        print(f"Wallet too low: {exc}", file=sys.stderr)
        return None
    except TransferFailedError as exc:
        # The operator refused: nothing was paid out, the funds are released.
        print(f"Transfer refused: {exc} (reason_code={exc.reason_code})", file=sys.stderr)
        return exc.transfer
    except ConflictError as exc:
        # Same order id already used, or the 60 s duplicate guard fired.
        print(f"Duplicate transfer: {exc}", file=sys.stderr)
        return None
    except NetworkError as exc:
        # No answer: the payout MAY have been executed. Never re-send blindly —
        # look it up by your order id first.
        print(f"No answer ({exc}); checking {order_id} before anything else", file=sys.stderr)
        try:
            return client.transfers.get(order_id)
        except PayHubError:
            return None
    except PayHubError as exc:
        print(f"barkapay error: {exc}", file=sys.stderr)
        return None

    if transfer.status is TransferStatus.UNCERTAIN:
        # The operator did not answer clearly. Do NOT send again: the
        # transfer.* webhook will carry the verdict.
        print(f"Transfer {transfer.public_id} is being verified")
    else:
        print(f"Transfer {transfer.public_id} — status {transfer.raw_status}")
    return transfer


if __name__ == "__main__":
    main(payhub.PayHub(api_key=os.environ["BARKAPAY_API_KEY"], country="bf"))
