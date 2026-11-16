"""Read the merchant wallet balance.

Run: BARKAPAY_API_KEY=key_id:secret python examples/balance.py
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal

import payhub
from payhub import PayHubError


def main(client: payhub.PayHub) -> Decimal | None:
    try:
        balance = client.balance.get()
    except PayHubError as exc:
        print(f"barkapay error: {exc}", file=sys.stderr)
        return None

    print(f"Available: {balance.available} {balance.currency}")
    print(f"On hold:   {balance.holds} {balance.currency}")
    return balance.available_decimal


if __name__ == "__main__":
    main(payhub.PayHub(api_key=os.environ["BARKAPAY_API_KEY"], country="bf"))
