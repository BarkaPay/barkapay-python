"""Test support: a fake transport that records requests and replays responses."""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping, Sequence
from typing import Any

from payhub import PayHub
from payhub.http import Request, Response


class FakeTransport:
    """Records the request and replays queued responses — no network."""

    def __init__(self, responses: Sequence[Response | BaseException]) -> None:
        self._queue: list[Response | BaseException] = list(responses)
        self._lock = threading.Lock()
        self.requests: list[Request] = []

    @property
    def last_request(self) -> Request | None:
        return self.requests[-1] if self.requests else None

    def __call__(self, request: Request) -> Response:
        with self._lock:
            self.requests.append(request)
            if not self._queue:
                raise RuntimeError("FakeTransport: no more queued responses.")
            nxt = self._queue.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt


def json_response(
    status: int,
    body: Mapping[str, Any],
    headers: Mapping[str, str] | None = None,
) -> Response:
    return Response(status, json.dumps(body), dict(headers or {}))


def ok(data: Mapping[str, Any] | None = None, meta: Mapping[str, Any] | None = None) -> Response:
    body: dict[str, Any] = {"status": "success", "code": "success", "message": "OK."}
    if data is not None:
        body["data"] = dict(data)
    if meta is not None:
        body["meta"] = dict(meta)
    return json_response(200, body)


def make_client(
    responses: Sequence[Response | BaseException],
    *,
    max_retries: int = 0,
    country: str = "bf",
) -> tuple[PayHub, FakeTransport, list[float]]:
    """A client wired to a :class:`FakeTransport`, whose sleeps are recorded
    instead of slept and whose jitter is deterministic (upper bound)."""
    transport = FakeTransport(responses)
    client = PayHub(
        api_key="pk_live_test:sk_live_test",
        country=country,
        max_retries=max_retries,
        transport=transport,
    )
    sleeps: list[float] = []
    client._api._sleep = sleeps.append
    client._api._uniform = lambda low, high: high
    return client, transport, sleeps


def sent_json(transport: FakeTransport, index: int = -1) -> Any:
    body = transport.requests[index].body
    assert body is not None
    return json.loads(body)
