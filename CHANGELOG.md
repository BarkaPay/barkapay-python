# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.1.0] - 2026-11-16

Backward compatible with 1.0.0: no public name was removed or renamed, and
every 1.0.0 call keeps its signature.

### Security

- **A request that creates or changes something is never replayed
  automatically after a 5xx or a network error.** 1.0.0 re-sent `POST`
  requests (payments, transfers, OTP confirmation) on 503 and on timeouts,
  which could execute the same payout or charge twice. Retries now apply only
  to requests answered 429 (not executed) and to read-only requests.
- **Redirects are never followed.** urllib used to follow them and forward the
  `Authorization` header to the redirect target, including another host.
- **HTTPS only.** `base_url` must be `https://` (`http://` is accepted for
  `localhost` only); `file:`, `ftp:`, `data:` and URLs with embedded
  credentials are refused, as are URLs handed to `UrllibTransport`.
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
  returned an unrelated payment); `.`/`..`, control characters and invalid
  country codes are refused.
- **Webhook verification is strict.** Malformed headers (duplicated `t`,
  non-hex or truncated `v1`, non-ASCII digits or characters) now return
  `False` instead of raising `TypeError` / `ValueError`; an empty signing
  secret and a negative `tolerance` raise `ConfigurationError` instead of
  silently accepting forged or replayed deliveries.
- A 2xx response whose body is not the JSON envelope, or lacks the expected
  object, raises `ApiError` (`code="invalid_response"`) instead of returning
  an empty `Payment` / `Transfer`.
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
- `Retry-After` is honoured (never retried earlier; a wait longer than 60 s is
  raised to the caller) and backoff is jittered.
- `UrllibTransport(context=…)` for a custom (verifying) CA bundle.
- `ConfigurationError` is now also a `ValueError`.
- Python 3.13 is tested and declared.

### Changed

- Default timeout raised from 30 to 40 seconds, so the SDK receives the API's
  own answer (given within 30 seconds) instead of timing out on an unknown
  outcome.
- `User-Agent` is now `payhub-python/<version> python/<version>`.
- `str()` and f-strings of the status enums give the wire value
  (`"SUCCESSFUL"`) on every Python version (3.11 changed the default).
- Documentation and messages use the brand name "barkapay". The distribution
  (`barkapay-payhub`) and import (`payhub`) names are unchanged.

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
