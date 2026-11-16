"""One client shared by many threads: no state leaks from a call to another."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

from payhub import PayHub
from payhub.http import Request, Response


class EchoTransport:
    """Answers each request with a payment whose id is derived from that very
    request — any cross-talk between threads shows up as a mismatch."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.request_ids: list[str] = []
        self.barrier = threading.Barrier(8, timeout=10)

    def __call__(self, request: Request) -> Response:
        with self.lock:
            self.request_ids.append(request.headers["X-Request-Id"])
        if request.url.endswith("/warmup"):
            # Hold 8 calls inside the transport at the same time.
            self.barrier.wait()
        country = request.url.split("/")[3]
        order_id = request.url.rsplit("/", 1)[1]
        body = {
            "status": "success",
            "data": {
                "payments": [
                    {
                        "public_id": f"{country}:{order_id}",
                        "order_id": order_id,
                        "status": "SUCCESSFUL",
                        "amount": 100,
                    }
                ]
            },
        }
        return Response(200, json.dumps(body), {"x-request-id": request.headers["X-Request-Id"]})


def test_concurrent_calls_do_not_interfere() -> None:
    transport = EchoTransport()
    client = PayHub(api_key="pk:sk", country="bf", transport=transport)
    countries = ["bf", "bj", "sn"]

    def work(n: int) -> tuple[str, str, str]:
        country = countries[n % 3]
        order_id = f"ORDER-{n}"
        payment = client.payments.get(order_id, country=country)
        return payment.public_id, country, order_id

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(work, range(400)))

    for public_id, country, order_id in results:
        assert public_id == f"{country}:{order_id}"
    # One correlation id per call, never reused.
    assert len(transport.request_ids) == 400
    assert len(set(transport.request_ids)) == 400


def test_calls_really_overlap_inside_the_transport() -> None:
    transport = EchoTransport()
    client = PayHub(api_key="pk:sk", country="bf", transport=transport)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(client.payments.get, "warmup") for _ in range(8)]
        ids = [future.result(timeout=15).public_id for future in futures]

    assert ids == ["bf:warmup"] * 8


def test_client_configuration_is_not_mutated_by_calls() -> None:
    transport = EchoTransport()
    client = PayHub(api_key="pk:sk", country="bf", transport=transport)
    before = (client._api.base_url, client._api.default_country, client._api._max_retries)
    client.payments.get("A", country="sn")
    client.payments.get("B")
    assert (client._api.base_url, client._api.default_country, client._api._max_retries) == before
    assert client.payments.get("C").public_id == "bf:C"  # the default country is untouched
