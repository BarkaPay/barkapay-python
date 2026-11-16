# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security vulnerabilities.

- Preferred: use GitHub's [private vulnerability reporting](https://github.com/BarkaPay/payhub-python/security/advisories/new).
- Or email **security@payhub.africa** with details and reproduction steps.

We aim to acknowledge reports within 72 hours.

## Supported versions

The latest released minor version receives security fixes. Upgrade to it
before reporting an issue: 1.1.0 fixes several weaknesses of 1.0.0 (see the
"Security" section of the [changelog](CHANGELOG.md)).

## What the SDK guarantees

- **Transport**: HTTPS only, certificates and host names always verified
  (TLS 1.2 minimum), redirects never followed. There is no switch to disable
  verification; a custom CA bundle goes through
  `payhub.UrllibTransport(context=...)`, which refuses a non-verifying context.
- **Credentials**: the API key is sent in the `Authorization` header to the
  configured `base_url` only. It never appears in `repr()`, exception messages
  or logs produced by the SDK.
- **Money safety**: a request that creates or changes something is never
  replayed automatically after a 5xx or a network error.
- **Webhooks**: constant-time signature comparison, replay window (5 minutes by
  default), strict header parsing, and a refusal to verify with an empty
  secret.
- **Supply chain**: no runtime dependency. Releases are built by GitHub
  Actions and published to PyPI through trusted publishing (OIDC): the release
  workflow stores no PyPI token.

## What remains your responsibility

- Keep `key_id:secret` API keys and `whsec_…` webhook secrets out of source
  control, logs and client-side code. Load them from the environment or a
  secrets manager; rotate them from the dashboard if they leak.
- Restrict each API application to the scopes, operators and IP addresses it
  needs.
- Verify every webhook against the **raw** request body
  (`payhub.webhook.verify` / `parse`) before trusting it, and deduplicate on
  `event_id`.
- If you inject your own `transport`, it must verify TLS and must not follow
  redirects.
- Send `order.unique: true` on every payment and transfer, and after a
  `NetworkError` or a 5xx on a create call, check with a list filtered on your
  `order.id` before sending it again (see "Retries, timeouts and idempotency"
  in the README).
