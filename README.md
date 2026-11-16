# barkapay Python SDK

Official Python client for the [barkapay API](https://payhub.africa/developers) —
mobile money collections, payment links and disbursements behind one contract
(Burkina Faso, Bénin, Sénégal).

- **Zero runtime dependencies** — pure standard library (`urllib`, `hmac`, `json`).
- Typed objects for payments, payment links, transfers and balance (frozen
  dataclasses, `py.typed`, mypy-strict clean).
- Webhook signature verification built in.
- Safe by default: TLS always verified, HTTPS only, redirects never followed,
  and **a request that moves money is never replayed blindly**.

The distribution is `barkapay-payhub`; you import it as `payhub`.

## Requirements

Python **3.9+**.

## Install

```bash
pip install barkapay-payhub
```

## Quick start

```python
import payhub

client = payhub.PayHub(
    api_key="key_id:secret",  # exactly what you copy from the dashboard
    country="bf",  # default country (bf, bj, sn) — overridable per call
)

payment = client.payments.create(
    {
        "operator": "ORANGE",
        "phone_number": "74123456",
        "amount": 10000,
        "otp": "123456",  # ORANGE takes the payer's code up-front; other operators do not
        "order": {"id": "ORDER-2026-001"},
    }
)

print(payment.public_id, payment.status)
```

> **Authentication:** pass the raw `key_id:secret` (or `key_id=` and `secret=`
> separately). The SDK adds the `Bearer` prefix itself. Keep the key on your
> server: load it from an environment variable or a secrets manager, never
> commit it, never ship it to a browser or a mobile app.

Every call targets one country: `https://hub.barkapay.com/{country}/v1/…`.
Set the default on the client and override it per call when needed:

```python
client.balance.get()  # default country
client.balance.get(country="sn")  # another country your key is enabled for
```

## Payments

```python
client.payments.create(params)  # -> payhub.Payment
client.payments.get("public_id or order id")  # -> payhub.Payment
client.payments.list({"status": "SUCCESSFUL", "per_page": 50})  # -> payhub.PaymentList
client.payments.confirm_otp(public_id, "123456")  # for AWAITING_OTP payments
client.payments.resend_otp(public_id)
```

What `create` returns depends on the operator — **always branch on `status`**:

| Returned `status` | Meaning | What to do |
|---|---|---|
| `SUCCESSFUL` | Settled synchronously | Deliver the order |
| `AWAITING_OTP` | The payer received a code (two-step operators) | `confirm_otp(public_id, code)` |
| `PROCESSING_OPERATOR` | The payer must approve on their phone | Wait for the webhook; open `redirect_url` when set |

`client.operators.info()` lists the operators of a country with their amount
bounds and whether they work with an OTP (`otp_required`): sent up-front in
`otp` for ORANGE (Burkina Faso, Sénégal), confirmed in a second step with
`confirm_otp` for MOOV (Burkina Faso).

The object returned by `create` is a short snapshot (no `channel`,
`created_at` or `order_data` yet); `get()` returns the full payment.

A payment that the payer or the operator **refuses** is not returned: it is
raised as `PaymentFailedError`, with the `FAILED` payment attached.

```python
from payhub import PaymentFailedError, PaymentStatus, ReasonCode

try:
    payment = client.payments.create(params)
except PaymentFailedError as error:
    failed = error.payment  # payhub.Payment, status FAILED
    print(failed.public_id, error.reason_code, failed.provider_reason)
    if error.reason_code == ReasonCode.PAYER_INSUFFICIENT_FUNDS:
        ...  # tell the customer
else:
    if payment.status is PaymentStatus.AWAITING_OTP:
        payment = client.payments.confirm_otp(payment.public_id, otp)
```

`get` accepts the payment's `public_id` or your own `order.id`. Several
payments can share an `order.id` (unless you send `order.unique`): `get` then
returns the first one — use `list({"order_id": ...})` to see them all, and
for an `order.id` that contains a `/`.

Useful fields: `status` (a `PaymentStatus`, or `None` for a status newer than
this SDK — the original string is always in `raw_status`), `reason_code`
(stable failure code, see `ReasonCode`), `provider_reason` (the operator's own
sentence, for support), `provider_transaction_id`, `provider_reference`,
`channel` (`api` or `hosted`), `link_public_id`, `redirect_url`, and `raw`
(the full payload, so a field added by the API tomorrow is never lost).

`SUCCESSFUL`, `FAILED`, `EXPIRED` and `CANCELED` are final
(`payment.status.is_final()`). **`status` is the verdict** — never decide that
money moved from a reason code or a message.

> Pass OTP codes as **strings**: `"012345"` is not `12345`.

## Payment links

A payment link is a durable URL on the barkapay-hosted checkout: your customer
picks an operator and pays there — no checkout to build.

```python
link = client.payment_links.create(
    {
        "title": "Invoice INV-2026-001",
        "amount": {"type": "fixed", "value": 15000, "currency": "XOF"},
        "usage": "single",  # or "multi"
        "order": {"id": "INV-2026-001"},
        "redirect": {"success_url": "https://shop.example/thanks"},
    }
)
print(link.url)  # send this to your customer

client.payment_links.get(link.public_id)  # by PL-… id or by slug
client.payment_links.list({"status": ["active", "paid"]})
client.payment_links.update(link.public_id, {"title": "Invoice INV-2026-001 (updated)"})
client.payment_links.pause(link.public_id)  # the checkout refuses new payments
client.payment_links.resume(link.public_id)
client.payment_links.cancel(link.public_id)  # for good

page = client.payment_links.list_payments(link.public_id, {"status": "SUCCESSFUL"})
for payment in page:
    print(payment.public_id, payment.amount)
```

`amount.type` is `fixed` (`value`), `range` (`min` / `max`) or `open` (the payer
chooses). Only `title`, `description`, `expires_at` and `redirect` can be
edited afterwards. Payments made through a link are regular payments
(`channel == "hosted"`, `link_public_id` set) and trigger the same `payment.*`
webhooks on the application that created the link.

`list_payments` returns the payments of **the country you call**; a link open
to several countries has payments in each of them (`page.payment_link.stats`
is the global total).

Your API application needs the `payment_links:create`, `payment_links:read`
and `payment_links:manage` scopes (see `client.me()["api_app"]["scopes"]`).

## Transfers

```python
from decimal import Decimal

transfer = client.transfers.create(
    {
        "operator": "ORANGE",
        "phone_number": "74123456",
        "amount": Decimal("5000"),
        "order": {"id": "PAYOUT-2026-001", "unique": True},
    }
)
client.transfers.get("public_id or order id")
client.transfers.list(
    {"from_date": "2026-11-01", "to_date": "2026-11-30"}
)  # -> payhub.TransferList
```

- A definitive refusal raises `TransferFailedError` (the `FAILED` transfer is
  in `error.transfer`; the reserved funds are released).
- `InsufficientBalanceError` means your wallet cannot cover the amount —
  nothing was sent.
- A returned status of **`UNCERTAIN`** means the operator did not answer
  clearly: the payout **may have been executed**. Do not send it again — the
  `transfer.*` webhook carries the verdict.
- `ConflictError` (409): the same `order.id` was already used with
  `order.unique`, or an identical transfer was sent less than 60 seconds ago.

## Balance & operators

```python
balance = client.balance.get()  # payhub.Balance
print(balance.available, balance.currency, balance.available_decimal)

client.operators.info()  # operators of the country: OTP, amount bounds…
client.operators.availability()  # real-time payment / transfer availability
client.me()  # your merchant + API application (scopes, permissions)
client.ping()  # liveness (also checks your key)
```

## Amounts

`amount` comes back as the API sends it (a JSON number for mobile money),
`fees` and balances as decimal strings (`"6.25"`). For arithmetic use the
exact `Decimal` accessors — `payment.amount_decimal`, `payment.fees_decimal`,
`balance.available_decimal`… — rather than floats. In requests, send amounts
as `int` or `Decimal` (at most 2 decimals).

## Pagination

Lists return one page (`items`, plus `meta` with `current_page`, `per_page`,
`total`). Walk the pages with `meta.next_page`:

```python
page_number = 1
while page_number is not None:
    page = client.payments.list({"status": "SUCCESSFUL", "per_page": 100, "page": page_number})
    for payment in page:
        ...
    page_number = page.meta.next_page if page.meta else None
```

## Webhooks

Final outcomes are pushed to your HTTPS endpoint, signed with the
`Barkapay-Signature` header (`t=<unix>,v1=<hmac-sha256>`). Verify every
delivery against the **raw request body**:

```python
from payhub import SignatureVerificationError, webhook

try:
    event = webhook.parse(
        raw_body,  # the RAW bytes of the request body
        signature_header,  # the Barkapay-Signature header (webhook.SIGNATURE_HEADER)
        endpoint_secret,  # whsec_…
    )
except SignatureVerificationError:
    ...  # answer 400 and stop

if event["type"] == "payment" and event["status"] == "SUCCESSFUL":
    ...  # payment.succeeded: deliver the order
```

`webhook.verify(...)` returns a `bool` if you prefer not to catch. Both reject
a delivery older (or newer) than 5 minutes — tune it with `tolerance=`.

**The raw-body trap.** The signature covers the exact bytes that were sent. If
your framework parses the JSON and you re-serialise it, the bytes differ and
the check fails. Read the raw body *first*:

```python
# Flask — request.get_data(), never request.json
@app.post("/webhooks/barkapay")
def barkapay_webhook():
    signature = request.headers.get(webhook.SIGNATURE_HEADER)
    try:
        event = webhook.parse(request.get_data(), signature, endpoint_secret)
    except SignatureVerificationError:
        return "invalid signature", 400
    return "ok", 200
```

```python
# Django — request.body, never request.POST nor a re-dumped json.loads(...)
# (with Django REST framework, read request.body before request.data)
@csrf_exempt
def barkapay_webhook(request):
    signature = request.headers.get(webhook.SIGNATURE_HEADER)
    try:
        event = webhook.parse(request.body, signature, endpoint_secret)
    except SignatureVerificationError:
        return HttpResponse("invalid signature", status=400)
    return HttpResponse("ok")
```

```python
# FastAPI / Starlette — await request.body(), never a Pydantic model parameter
@app.post("/webhooks/barkapay")
async def barkapay_webhook(request: Request):
    signature = request.headers.get(webhook.SIGNATURE_HEADER)
    try:
        event = webhook.parse(await request.body(), signature, endpoint_secret)
    except SignatureVerificationError:
        return Response("invalid signature", status_code=400)
    return Response("ok")
```

Then:

- **Deduplicate on `event["event_id"]`** — deliveries are at-least-once and a
  retry carries the same `event_id`.
- The body has no event name; derive it from `type` and `status`:
  `payment` + `SUCCESSFUL` → `payment.succeeded`, `FAILED`/`CANCELED` →
  `payment.failed`, `EXPIRED` → `payment.expired`; `transfer` + `SUCCESSFUL` →
  `transfer.succeeded`, `FAILED`/`CANCELED` → `transfer.failed`.
- Answer any `2xx` quickly and do the work afterwards.
- Load the signing secret from your configuration and fail at start-up if it
  is missing — the SDK refuses to verify with an empty secret.

A complete receiver is in [`examples/webhook.py`](examples/webhook.py).

## Errors

Every API error raises a typed exception extending `payhub.ApiError`, with
`.code`, `.http_status`, `.request_id` (quote it to support), `.errors`
(field → messages), `.data`, `.retry_after` and `.headers`:

| Exception | When |
|---|---|
| `AuthenticationError` | 401 — missing or invalid key |
| `AuthorizationError` | 403 / 410 / 451 — scope, operator permission, IP allow-list, account suspended or closed |
| `ValidationError` | 422 — invalid request (`.errors`); also 400 for an unreadable body |
| `NotFoundError` | 404 — unknown resource, endpoint or country |
| `ConflictError` | 409 — duplicate (`order.unique`, 60 s transfer guard) |
| `InsufficientBalanceError` | 400 — wallet too low for the transfer |
| `PaymentFailedError` | 424 — payment refused; the payment is in `.payment` |
| `TransferFailedError` | 424 — transfer refused; the transfer is in `.transfer` |
| `RateLimitError` | 429 — slow down; wait `.retry_after` seconds |
| `ServiceUnavailableError` | 503 — operator or service temporarily unavailable |
| `ServerError` | other 5xx |
| `ApiError` | anything else (e.g. 405) |
| `NetworkError` | no response at all (DNS, TLS, timeout) — not an `ApiError` |

Two more exceptions never come from the API: `ConfigurationError` (invalid
arguments — malformed key, non-HTTPS URL, empty id… — raised before any
request is sent) and `SignatureVerificationError` (webhooks).

Catch everything from the SDK with `payhub.PayHubError`.

```python
from payhub import ApiError, NetworkError, RateLimitError, ValidationError

try:
    client.transfers.create(params)
except ValidationError as error:
    print(error.errors)  # {"phone_number": ["…"]}
except RateLimitError as error:
    print(error.retry_after)  # seconds
except ApiError as error:
    print(error.http_status, error.code, error.request_id)
except NetworkError:
    ...  # outcome unknown — see below
```

## Retries, timeouts and idempotency

The SDK retries **only what is safe to repeat** (`max_retries`, default 2,
exponential backoff with jitter, `Retry-After` honoured):

- any request answered **429** — the rate limiter refused it before anything ran;
- **read-only** requests (`GET`) that hit 502 / 503 / 504 or a network error.

A request that creates or changes something (`payments.create`,
`transfers.create`, `confirm_otp`, payment-link writes) is **never replayed**
after a 5xx or a network error: the first attempt may have gone through, and
the API has no idempotency-key header. When such a call ends with a
`NetworkError` or a 5xx, the outcome is unknown:

1. look the operation up with your own reference —
   `client.transfers.get(order_id)` / `client.payments.get(order_id)`;
2. only create again if it does not exist.

Send `"order": {"id": …, "unique": True}` to make this airtight: the API then
refuses a second create with the same `order.id` (409 `ConflictError`),
whatever the outcome of the first — use a new id for a deliberate new attempt.

The default timeout is 40 seconds per connection / read (the API answers
within 30 seconds).

## Rate limits

Requests are throttled per merchant and per route family (payments and
payment links, transfer creation, transfer reads). Over the limit, the API
answers **429** with a `Retry-After` header: the SDK waits that long (when it
is at most 60 seconds) and retries, up to `max_retries` times, then raises
`RateLimitError` with `.retry_after`. The limits are not published — read
them at run time: authenticated responses carry `x-ratelimit-limit` and
`x-ratelimit-remaining`. On errors they are in `error.headers`; a transport
wrapper sees them on every response:

```python
base_transport = payhub.UrllibTransport()


def watching_transport(request: payhub.Request) -> payhub.Response:
    response = base_transport(request)
    remaining = response.headers.get("x-ratelimit-remaining")
    if remaining is not None and int(remaining) < 5:
        print("close to the rate limit:", remaining, "requests left this minute")
    return response


paced_client = payhub.PayHub(api_key="key_id:secret", country="bf", transport=watching_transport)
```

## Configuration

```python
client = payhub.PayHub(
    api_key="key_id:secret",
    country="bf",
    base_url="https://hub.barkapay.com",  # https only
    max_retries=2,  # 0 disables automatic retries
    timeout=40.0,  # seconds
)
```

`transport=` accepts any callable `(payhub.Request) -> payhub.Response`: use
it to plug another HTTP stack, or to fake the network in your tests.

```python
import json


def fake_transport(request: payhub.Request) -> payhub.Response:
    body = {
        "status": "success",
        "data": {"country": "BF", "currency": "XOF", "available": "1500.00"},
    }
    return payhub.Response(200, json.dumps(body))


test_client = payhub.PayHub(api_key="key_id:secret", country="bf", transport=fake_transport)
assert test_client.balance.get().available == "1500.00"
```

A client holds no per-request state: create one and share it between threads.

## Security

- TLS certificates and host names are always verified; there is no option to
  turn that off. A custom CA bundle goes through
  `payhub.UrllibTransport(context=...)`, which refuses a non-verifying context.
- Only `https://` base URLs are accepted (`http://` for `localhost` only).
- Redirects are never followed, so the API key cannot be forwarded to another
  host.
- The API key never appears in `repr()`, exceptions or logs produced by the SDK.

If a call fails with `NetworkError: … CERTIFICATE_VERIFY_FAILED`, your Python
cannot find a CA bundle (common in minimal containers). Point it at one — do
not look for a way to skip verification:

```python
import ssl

context = ssl.create_default_context(cafile="/etc/ssl/certs/ca-certificates.crt")
client = payhub.PayHub(
    api_key="key_id:secret",
    country="bf",
    transport=payhub.UrllibTransport(timeout=40.0, context=context),
)
```

(or set the `SSL_CERT_FILE` environment variable to that file).

See [SECURITY.md](SECURITY.md) to report a vulnerability.

## Versioning

This project follows [Semantic Versioning](https://semver.org/); changes are
listed in [CHANGELOG.md](CHANGELOG.md).

## License

MIT.
