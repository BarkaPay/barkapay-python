"""A fake transport that answers like the API, route by route — used to run
the README snippets and the examples without any network."""

from __future__ import annotations

import json
import re
import threading
from typing import Any

from barkapay.http import Request, Response
from tests import samples

_PREFIX = r"^https://hub\.barkapay\.com/(bf|bj|sn)/v1"


def _envelope(data: Any = None, meta: Any = None, status: int = 200) -> Response:
    body: dict[str, Any] = {
        "status": "success",
        "code": "resource_created" if status == 201 else "success",
        "message": "OK.",
        "timestamp": "2026-11-16T08:00:00+00:00",
        "request_id": "00000000-0000-4000-8000-000000000000",
    }
    if data is not None:
        body["data"] = data
    if meta is not None:
        body["meta"] = meta
    return Response(status, json.dumps(body))


class ApiDouble:
    """Routes a request to a canned, realistic response and records it."""

    def __init__(self) -> None:
        self.requests: list[Request] = []
        self._lock = threading.Lock()

    def calls(self) -> list[str]:
        """``["POST /payments", …]`` — what was sent, in order."""
        return [f"{r.method} {re.sub(_PREFIX, '', r.url.split('?')[0])}" for r in self.requests]

    def __call__(self, request: Request) -> Response:
        with self._lock:
            self.requests.append(request)
        method = request.method
        path = re.sub(_PREFIX, "", request.url.split("?")[0])
        query = request.url.split("?")[1] if "?" in request.url else ""
        link = samples.payment_link()
        link_summary = {
            "public_id": link["public_id"],
            "slug": link["slug"],
            "status": link["status"],
            "stats": link["stats"],
        }
        one_page = {"current_page": 1, "last_page": 1, "per_page": 50, "total": 1}

        routes: list[tuple[str, str, Response]] = [
            ("GET", r"/ping", _envelope({"version": "2.0.0", "documentation": "https://x"})),
            (
                "GET",
                r"/me",
                _envelope(
                    {
                        "merchant": {"public_id": "M-1", "name": "Shop", "country": "BF"},
                        "api_app": {"label": "shop", "scopes": ["payments:create"]},
                    }
                ),
            ),
            (
                "GET",
                r"/balance",
                _envelope(
                    {
                        "country": "BF",
                        "currency": "XOF",
                        "available": "150000.00",
                        "total": "150250.50",
                        "holds": "250.50",
                    }
                ),
            ),
            ("GET", r"/operators/info", _envelope({"operators": {}, "supported_countries": []})),
            ("GET", r"/operators/availability", _envelope({"operational": True, "services": {}})),
            ("POST", r"/payments/links", _envelope({"payment_link": link}, status=201)),
            (
                "GET",
                r"/payments/links",
                _envelope(
                    {"payment_links": [link]}, {"current_page": 1, "per_page": 25, "total": 1}
                ),
            ),
            (
                "GET",
                r"/payments/links/[^/]+/payments",
                _envelope(
                    {
                        "payment_link": link_summary,
                        "payments": [
                            samples.payment(channel="hosted", link_public_id=link["public_id"])
                        ],
                    },
                    one_page,
                ),
            ),
            ("POST", r"/payments/links/[^/]+/(pause|resume)", _envelope({"payment_link": link})),
            ("GET", r"/payments/links/[^/]+", _envelope({"payment_link": link})),
            ("PATCH", r"/payments/links/[^/]+", _envelope({"payment_link": link})),
            ("DELETE", r"/payments/links/[^/]+", _envelope({"payment_link": link})),
            ("POST", r"/payments", _envelope({"payment": samples.payment()})),
            ("POST", r"/payments/[^/]+/confirm-otp", _envelope({"payment": samples.payment()})),
            ("POST", r"/payments/[^/]+/resend-otp", _envelope()),
            ("GET", r"/payments", _envelope({"payments": [samples.payment()]}, one_page)),
            ("GET", r"/payments/[^/]+", _envelope({"payments": [samples.payment()]})),
            ("POST", r"/transfers", _envelope({"transfer": samples.transfer()}, status=201)),
            ("GET", r"/transfers", _envelope({"transfers": [samples.transfer()]}, one_page)),
            ("GET", r"/transfers/[^/]+", _envelope({"transfers": [samples.transfer()]})),
        ]
        for route_method, pattern, response in routes:
            if method == route_method and re.fullmatch(pattern, path):
                return response
        raise AssertionError(f"ApiDouble: no route for {method} {path} ?{query}")
