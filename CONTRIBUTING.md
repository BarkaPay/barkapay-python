# Contributing

Thanks for helping improve the barkapay Python SDK.

## Development setup

We use [uv](https://docs.astral.sh/uv/). Install the dev tools into a managed
environment — `uv.lock` pins the exact versions CI uses:

```bash
uv sync --locked --extra dev
```

To move to newer tool versions: `uv lock --upgrade`, run the checks, commit
the lock file.

## Checks (must pass before opening a PR)

```bash
uv run ruff check .            # lint
uv run ruff format --check .   # formatting
uv run mypy                    # static typing (strict, src + tests)
uv run pytest                  # tests
```

CI runs them on Python 3.9 to 3.14, then builds the
distribution and checks it (`twine check`, `py.typed` present in the wheel).

## Scope & conventions

- The SDK mirrors the public API (`https://hub.barkapay.com/{country}/v1/…`) —
  keep the public surface in sync with it, and identical in spirit to the PHP
  and Node SDKs (same resource and method names). Prefer small, typed additions.
- **No runtime dependencies**: stick to the standard library so the SDK stays
  trivial to embed anywhere.
- **Never replay a write.** Automatic retries are limited to 429 answers and
  read-only requests; do not widen that without an idempotency mechanism on
  the API side.
- Open an issue first for larger changes so we can align on the design.
- Add or update tests with every behaviour change; never call the live API from
  tests (use the injectable `Transport`; socket tests bind to 127.0.0.1 only).
- The README code blocks are executed by `tests/test_docs.py`: keep them
  runnable.

## Releasing (maintainers)

1. Bump `src/barkapay/_version.py` and move the `[Unreleased]` notes of
   `CHANGELOG.md` under the new version.
2. Merge to `main` and wait for CI to be green.
3. Tag and push — the tag must equal the version:

   ```bash
   git tag v1.2.3 && git push origin v1.2.3
   ```

The `Release` workflow tests, builds and publishes to PyPI through trusted
publishing. A published version cannot be replaced: double-check before tagging.
