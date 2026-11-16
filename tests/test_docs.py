"""The README and the examples are part of the product: run them.

Every ``python`` block of the README is executed against :class:`ApiDouble`
(no network). Blocks that only make sense inside a web framework are compiled
(syntax-checked) instead.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import re
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import payhub
from payhub import webhook
from tests import samples
from tests.routing import ApiDouble
from tests.support import json_response

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
SECRET = "whsec_readme_secret"


def _python_blocks() -> list[str]:
    return re.findall(r"```python\n(.*?)```", README, flags=re.DOTALL)


def _signed(body: bytes) -> str:
    ts = int(time.time())
    digest = hmac.new(SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def _namespace(api: ApiDouble) -> dict[str, Any]:
    raw_body = json.dumps(
        {
            "type": "payment",
            "status": "SUCCESSFUL",
            "public_id": samples.PAYMENT_ID,
            "event_id": "dlv:1",
        }
    ).encode()
    client = payhub.PayHub(api_key="key_id:secret", country="bf", transport=api)
    params = {"operator": "ORANGE", "phone_number": "74123456", "amount": 10000}
    return {
        "payhub": payhub,
        "client": client,
        "params": params,
        "public_id": samples.PAYMENT_ID,
        "otp": "123456",
        "link": payhub.PaymentLink.from_dict(samples.payment_link()),
        "raw_body": raw_body,
        "signature_header": _signed(raw_body),
        "endpoint_secret": SECRET,
    }


_CA_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",  # Debian, Ubuntu, Alpine
    "/etc/pki/tls/certs/ca-bundle.crt",  # RHEL, Fedora, AlmaLinux
    "/etc/ssl/cert.pem",  # macOS, BSD
)

# Web-framework glue (Flask / Django / FastAPI views): cannot run here.
_FRAMEWORK_ONLY = re.compile(r"def barkapay_webhook\(")


def test_readme_has_python_blocks() -> None:
    assert len(_python_blocks()) >= 12


@pytest.mark.parametrize("index", range(len(_python_blocks())))
def test_readme_block_runs(index: int, monkeypatch: pytest.MonkeyPatch) -> None:
    block = _python_blocks()[index]
    code = compile(block, f"README.md[python block {index}]", "exec")

    if _FRAMEWORK_ONLY.search(block):
        return  # framework glue: syntax-checked only

    if "cafile=" in block:
        # The CA-bundle snippet names a real file: use one that exists here.
        bundle = next((path for path in _CA_BUNDLES if Path(path).is_file()), None)
        if bundle is None:
            return  # no system bundle on this machine: syntax-checked only
        block = re.sub(r'cafile="[^"]+"', f'cafile="{bundle}"', block)
        code = compile(block, f"README.md[python block {index}]", "exec")

    api = ApiDouble()
    # The quick-start and configuration blocks build their own client: route
    # it to the double instead of the network.
    monkeypatch.setattr(payhub.client, "UrllibTransport", lambda timeout: api)
    exec(code, _namespace(api))


def test_readme_quick_start_sends_the_documented_request(monkeypatch: pytest.MonkeyPatch) -> None:
    api = ApiDouble()
    monkeypatch.setattr(payhub.client, "UrllibTransport", lambda timeout: api)
    quick_start = next(b for b in _python_blocks() if "client = payhub.PayHub(" in b)

    namespace: dict[str, Any] = {}
    exec(compile(quick_start, "README.md[quick start]", "exec"), namespace)

    request = api.requests[0]
    assert request.method == "POST"
    assert request.url == "https://hub.barkapay.com/bf/v1/payments"
    assert request.headers["Authorization"] == "Bearer key_id:secret"
    assert json.loads(request.body or "{}") == {
        "operator": "ORANGE",
        "phone_number": "74123456",
        "amount": 10000,
        "otp": "123456",
        "order": {"id": "ORDER-2026-001"},
    }
    assert namespace["payment"].status is payhub.PaymentStatus.SUCCESSFUL


def test_readme_payment_links_block_covers_the_whole_resource() -> None:
    api = ApiDouble()
    block = next(b for b in _python_blocks() if "client.payment_links.create(" in b)
    exec(compile(block, "README.md[payment links]", "exec"), _namespace(api))
    link_id = samples.LINK_ID
    assert api.calls() == [
        "POST /payments/links",
        f"GET /payments/links/{link_id}",
        "GET /payments/links",
        f"PATCH /payments/links/{link_id}",
        f"POST /payments/links/{link_id}/pause",
        f"POST /payments/links/{link_id}/resume",
        f"DELETE /payments/links/{link_id}",
        f"GET /payments/links/{link_id}/payments",
    ]


def test_readme_names_the_real_signature_header_and_no_stale_one() -> None:
    assert "Barkapay-Signature" in README
    assert "PayHub-Signature" not in README
    assert webhook.SIGNATURE_HEADER in README


def test_readme_documents_every_public_error() -> None:
    for name in payhub.__all__:
        if name.endswith("Error"):
            assert name in README, f"{name} is not documented in the README"


def test_readme_documents_every_resource_method() -> None:
    client = payhub.PayHub(api_key="key_id:secret", country="bf", transport=ApiDouble())
    for resource in ("payments", "payment_links", "transfers", "balance", "operators"):
        for method in dir(getattr(client, resource)):
            if not method.startswith("_"):
                assert f"client.{resource}.{method}(" in README, f"{resource}.{method}"
    assert "client.ping()" in README and "client.me()" in README


# --- examples ---------------------------------------------------------------------


def _load_example(name: str) -> ModuleType:
    path = ROOT / "examples" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"example_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # importing must not touch the network
    return module


def _client(api: Any) -> payhub.PayHub:
    return payhub.PayHub(api_key="key_id:secret", country="bf", transport=api, max_retries=0)


def test_example_collect(capsys: pytest.CaptureFixture[str]) -> None:
    api = ApiDouble()
    payment = _load_example("collect").main(_client(api))
    assert payment.status is payhub.PaymentStatus.SUCCESSFUL
    assert api.calls() == ["POST /payments"]
    assert json.loads(api.requests[0].body or "{}")["order"]["unique"] is True
    assert "settled" in capsys.readouterr().out


def test_example_collect_reports_a_refusal(capsys: pytest.CaptureFixture[str]) -> None:
    refused = json_response(
        424,
        {
            "status": "failed",
            "code": "payment_failed",
            "message": "Payer has insufficient funds.",
            "data": {"payment": samples.failed_payment()},
        },
    )
    payment = _load_example("collect").main(_client(lambda request: refused))
    assert payment.status is payhub.PaymentStatus.FAILED
    assert "payer_insufficient_funds" in capsys.readouterr().err


def test_example_disburse(capsys: pytest.CaptureFixture[str]) -> None:
    api = ApiDouble()
    transfer = _load_example("disburse").main(_client(api))
    assert transfer.public_id == samples.TRANSFER_ID
    assert api.calls() == ["POST /transfers"]
    body = json.loads(api.requests[0].body or "{}")
    assert body["amount"] == 5000 and body["order"]["unique"] is True
    assert samples.TRANSFER_ID in capsys.readouterr().out


def test_example_disburse_looks_the_transfer_up_after_a_network_error() -> None:
    calls: list[str] = []

    def flaky(request: payhub.Request) -> payhub.Response:
        calls.append(request.method)
        if request.method == "POST":
            raise payhub.NetworkError("timed out")
        return ApiDouble()(request)

    transfer = _load_example("disburse").main(_client(flaky))
    assert calls == ["POST", "GET"]  # never a second POST
    assert transfer.public_id == samples.TRANSFER_ID


def test_example_balance(capsys: pytest.CaptureFixture[str]) -> None:
    api = ApiDouble()
    available = _load_example("balance").main(_client(api))
    assert str(available) == "150000.00"
    assert "Available: 150000.00 XOF" in capsys.readouterr().out


def test_example_payment_link(capsys: pytest.CaptureFixture[str]) -> None:
    api = ApiDouble()
    link = _load_example("payment_link").main(_client(api))
    assert link.public_id == samples.LINK_ID
    assert api.calls() == [
        "POST /payments/links",
        f"GET /payments/links/{samples.LINK_ID}/payments",
    ]
    assert link.url in capsys.readouterr().out


def test_example_webhook_handler(capsys: pytest.CaptureFixture[str]) -> None:
    example = _load_example("webhook")
    body = json.dumps(
        {
            "type": "payment",
            "status": "SUCCESSFUL",
            "public_id": samples.PAYMENT_ID,
            "order_id": "ORDER-1",
            "event_id": "dlv:example:1",
        }
    ).encode()

    assert example.handle_webhook(body, _signed(body), SECRET) == ("ok", 200)
    assert "payment.succeeded" in capsys.readouterr().out
    # A retry of the same delivery is acknowledged but not processed twice.
    assert example.handle_webhook(body, _signed(body), SECRET) == ("ok", 200)
    assert capsys.readouterr().out == ""
    # Forged, unsigned and tampered deliveries are refused.
    assert example.handle_webhook(body, _signed(body).replace("v1=", "v1=0"), SECRET)[1] == 400
    assert example.handle_webhook(body, None, SECRET) == ("invalid signature", 400)
    assert example.handle_webhook(body + b" ", _signed(body), SECRET)[1] == 400


def test_examples_never_ship_a_default_secret_or_key() -> None:
    for path in (ROOT / "examples").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "os.environ.get(" not in source, f"{path.name}: use os.environ[...] (fail fast)"
        assert "sk_live" not in source and "whsec_xxx" not in source
        assert "PayHub-Signature" not in source
