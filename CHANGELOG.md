# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.1.0] - 2026-11-16

No public name was removed or renamed and every 1.0.0 call keeps its
signature. Several behaviours are deliberately stricter or safer — read
"Behaviour changes to check when upgrading" below.

### Security

- **A request that creates or changes something is never replayed
  automatically after a timeout, a network error or a 5xx.** 1.0.0 re-sent
  `POST` requests (payments, transfers, OTP confirmation) on 503 and on
  timeouts, which could execute the same payout or charge twice — the API has
  no idempotency key. A write is now re-sent in one case only: the API's rate
  limiter refused it (429 `too_many_requests`, no recorded operation in the
  answer) and asks to wait 10 seconds at most. Read-only requests are retried
  on 429, 502, 503, 504 and network errors.
- **Redirects are never followed.** urllib used to follow them and forward the
  `Authorization` header to the redirect target, including another host.
- **HTTPS only.** With the built-in transport `base_url` must be `https://`
  (`http://` is accepted for `localhost` only). `file:`, `ftp:`, `data:`,
  URLs with embedded credentials, a query string or non-ASCII characters are
  always refused. (A custom `transport=` owns its connection and may still be
  pointed at a plain-http mock — never at a barkapay host.)
- **TLS verification cannot be weakened by accident.** The transport builds its
  own verifying context (TLS 1.2 minimum), unaffected by a process-wide
  `ssl._create_default_https_context` override; `UrllibTransport(context=…)`
  refuses a context that does not verify certificates and host names.
- **The API key no longer leaks through `repr()`.** `repr(Request)` masks the
  `Authorization` / `X-Api-Key` headers, the client has a credential-free
  `repr`, and a malformed key (spaces, line breaks, non-ASCII, missing key id)
  is refused at construction without being echoed.
- **Identifiers and country codes are validated before reaching the URL.** An
  empty id no longer turns `payments.get("")` into the list endpoint (which
  returned an unrelated payment); `.`/`..`, `/`, `?`, `#`, control characters
  and country codes that are not two letters are refused before any request.
- **Webhook verification is strict.** Malformed headers (duplicated `t`,
  non-hex or truncated `v1`, non-ASCII digits or characters) now return
  `False` instead of raising `TypeError` / `ValueError`; an empty signing
  secret and a `tolerance` of 0 or less raise `ConfigurationError` instead of
  silently accepting forged or replayed deliveries.
- A 2xx response whose body is not the JSON envelope, or lacks the expected
  object or list (`ping()`, `me()` and `operators.*` included), raises
  `ApiError` (`code="invalid_response"`) instead of returning an empty
  `Payment` / `Transfer`, an empty page or `{}`. It means "accepted but
  unreadable", not "refused".
- The API key is no longer present in the local variables of a traceback
  raised while building a request.
- Truncated and oversized (> 10 MiB) responses raise `NetworkError`.

### Added

- **Payment links**: `client.payment_links` with `create`, `list`, `get`,
  `update`, `cancel`, `pause`, `resume` and `list_payments`, and the
  `PaymentLink`, `PaymentLinkAmount`, `PaymentLinkStats`, `PaymentLinkList`,
  `PaymentLinkPaymentList`, `PaymentLinkSummary` objects and
  `PaymentLinkStatus` enum.
- **Failure details**: `Payment` / `Transfer` expose `reason_code`,
  `provider_reason`, `provider_reference` and `raw_status`; `Payment` also
  exposes `link_public_id` and `redirect_url`. New `ReasonCode` enum.
- **Statuses**: `PaymentStatus.EXPIRED` (final) and `TransferStatus.UNCERTAIN`.
- **Errors**: `PaymentFailedError` and `TransferFailedError` (HTTP 424) and
  `InsufficientBalanceError` (HTTP 400). Every `ApiError` now carries `data`,
  `retry_after` and `headers`, plus the `payment`, `transfer` and
  `reason_code` accessors for the failed operation returned with the error.
  HTTP 410 and 451 map to `AuthorizationError` even without a known code.
- **Webhooks**: the signature header is now `Barkapay-Signature`
  (`webhook.SIGNATURE_HEADER`). Several `v1` signatures in one header are
  supported; the payload may be `bytes`, `bytearray`, `memoryview` or `str`,
  the secret `str` or `bytes`.
- **Exact amounts**: `amount_decimal`, `fees_decimal`, `available_decimal`,
  `total_decimal`, `holds_decimal` accessors; `Decimal` amounts, `date` and
  timezone-aware `datetime` values are accepted in request params.
- **Pagination helpers**: `Pagination.page_count`, `next_page`, `has_more`.
- `Retry-After` is honoured (never retried earlier; a wait longer than 10 s is
  raised to the caller) and backoff is jittered.
- `NetworkError.request_id`: the `X-Request-Id` that was sent, to quote to
  support.
- `UrllibTransport(context=…)` for a custom (verifying) CA bundle.
- `ConfigurationError` is now also a `ValueError`.
- Python 3.13 and 3.14 are tested and declared.

### Changed

- Default timeout raised from 30 to 40 seconds, so the SDK receives the API's
  own answer (given within 30 seconds) instead of timing out on an unknown
  outcome. With the built-in transport it bounds the connection and each read
  (the standard library has no whole-request deadline).
- `User-Agent` is now `payhub-python/<version> python/<version>`.
- `str()` and f-strings of the status enums give the wire value
  (`"SUCCESSFUL"`) on every Python version (3.11 changed the default).
- Documentation and messages use the brand name "barkapay". The distribution
  (`barkapay-payhub`) and import (`payhub`) names are unchanged.

### Behaviour changes to check when upgrading

- Automatic retries: writes are no longer re-sent after 503 / timeouts (see
  Security). If you relied on that, handle `ServiceUnavailableError` and
  `NetworkError` yourself — after checking that the operation was not
  recorded.
- A 429 whose `Retry-After` is 10 s or less is waited for instead of being
  retried within a second; a longer one raises `RateLimitError` immediately
  (reads and writes alike — same rule in the PHP and Node SDKs).
- `ConfigurationError` is now raised at construction or before sending for:
  a `base_url` that is not https (built-in transport), an `api_key` without
  the `key_id:` part, an invalid country code, an empty or malformed id (an id
  containing `/` used to be sent percent-encoded and answered 404),
  `timeout` that is not a positive finite number, `max_retries` that is not
  an `int` (values above 10 are capped), params that cannot be encoded as
  JSON (was `TypeError`).
- Redirects are returned as `ApiError` (HTTP 3xx) instead of being followed.
- The built-in transport ignores an opener installed with
  `urllib.request.install_opener()`; environment proxies still apply.
- `webhook.verify` / `parse`: an empty secret, a `tolerance` of 0 or less
  (0 used to switch the replay check off) or non-finite, and a parsed
  (non-raw) payload raise `ConfigurationError`.
- `str(PaymentStatus.SUCCESSFUL)` is `"SUCCESSFUL"` (was
  `"PaymentStatus.SUCCESSFUL"`).
- `EXPIRED` payments now have `status=PaymentStatus.EXPIRED` (was `None`).

### Fixed

- `fees` returned as a bare JSON number was hydrated as `None`.
- `EXPIRED` payments and `UNCERTAIN` transfers were hydrated with
  `status=None`; `PaymentStatus.is_final()` now includes `EXPIRED`.
- A payment or transfer refused by the operator lost its `public_id` and
  failure reason (the error carried no `data`).
- `Decimal` amounts raised a raw `TypeError`; unserialisable params now raise
  `ConfigurationError`.
- The OTP passed to `confirm_otp` is always sent as a string.
- The examples used a phone number the API rejects and a fallback webhook
  secret; they now fail fast when the configuration is missing.

## [1.0.0] - 2026-06-28

### Added

- Initial Python SDK: `PayHub` client with `payments`, `transfers`, `balance`,
  `operators` resources plus `ping()` / `me()`.
- Typed data objects (`Payment`, `Transfer`, `Balance`, `Pagination`) and status
  enums (`PaymentStatus`, `TransferStatus`).
- Webhook signature verification (`payhub.webhook.verify` / `parse`).
- Typed exception hierarchy mapped from the API error envelope.
- Zero-dependency `urllib` transport with automatic retries on 429/503/network,
  and an injectable `Transport` for tests.

[Unreleased]: https://github.com/BarkaPay/payhub-python/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/BarkaPay/payhub-python/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/BarkaPay/payhub-python/releases/tag/v1.0.0
