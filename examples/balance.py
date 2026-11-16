"""Read the merchant wallet balance.

Run: BARKAPAY_API_KEY=key_id:secret python examples/balance.py
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal

import barkapay
from barkapay import BarkaPayError


def main(client: barkapay.BarkaPay) -> Decimal | None:
    try:
        balance = client.balance.get()
    except BarkaPayError as exc:
        print(f"barkapay error: {exc}", file=sys.stderr)
        return None

    print(f"Available: {balance.available} {balance.currency}")
    print(f"On hold:   {balance.holds} {balance.currency}")
    return balance.available_decimal


if __name__ == "__main__":
    main(barkapay.BarkaPay(api_key=os.environ["BARKAPAY_API_KEY"], country="bf"))
