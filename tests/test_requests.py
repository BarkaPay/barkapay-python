"""How logical calls are turned into HTTP requests, and how the client
protects the credential, the URL and the money."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from decimal import Decimal

import pytest

from payhub import ApiError, ConfigurationError, PayHub, PayHubError, Request, __version__
from payhub.http import Response
from tests import samples
from tests.support import FakeTransport, json_response, make_client, ok, sent_json

KEY = "pk_live_abc123:sk_live_SUPERSECRET"


# --- identity -----------------------------------------------------------------


def test_user_agent_carries_sdk_and_python_versions() -> None:
    client, transport, _ = make_client([ok({})])
    client.ping()
    user_agent = transport.requests[0].headers["User-Agent"]
    assert user_agent.startswith(f"payhub-python/{__version__} python/3.")


def test_version_is_1_1_0() -> None:
    assert __version__ == "1.1.0"
    assert PayHub.VERSION == "1.1.0"


def test_default_base_url_is_the_public_hub() -> None:
    client, transport, _ = make_client([ok({})])
    client.ping()
    assert transport.requests[0].url == "https://hub.barkapay.com/bf/v1/ping"


def test_request_id_is_stable_across_retries() -> None:
    client, transport, _ = make_client(
        [json_response(503, {"status": "error", "code": "service_unavailable"}), ok({})],
        max_retries=1,
    )
    client.ping()
    ids = {r.headers["X-Request-Id"] for r in transport.requests}
    assert len(transport.requests) == 2
    assert len(ids) == 1


# --- the credential never leaks -----------------------------------------------


def test_repr_of_client_and_request_never_show_the_key() -> None:
    transport = FakeTransport([ok({})])
    client = PayHub(api_key=KEY, country="bf", transport=transport)
    client.ping()

    request = transport.requests[0]
    assert request.headers["Authorization"] == f"Bearer {KEY}"  # sent on the wire…
    for text in (repr(client), str(client), repr(client._api), repr(request), str(request)):
        assert "SUPERSECRET" not in text  # …never printed
        assert "sk_live" not in text
    assert "[redacted]" in repr(request)
    assert "bf" in repr(client)


def test_request_repr_redacts_every_credential_header() -> None:
    request = Request(
        "GET",
        "https://hub.barkapay.com/bf/v1/ping",
        {"authorization": "Bearer a:b", "X-Api-Key": "kid", "Accept": "application/json"},
    )
    text = repr(request)
    assert "a:b" not in text
    assert "kid" not in text
    assert "application/json" in text


def test_errors_never_echo_the_key() -> None:
    client, _, _ = make_client(
        [json_response(401, {"status": "failed", "code": "incorrect_credentials", "message": "x"})]
    )
    with pytest.raises(ApiError) as exc_info:
        client.ping()
    assert "sk_live" not in str(exc_info.value)
    assert "sk_live" not in repr(exc_info.value)


@pytest.mark.parametrize(
    "bad_key",
    [
        "sk_live_SUPERSECRET",  # no key id
        ":sk_live_SUPERSECRET",
        "pk_live_abc:",
        "pk_live_abc:sk_live_SUPER\r\nX-Injected: 1",  # header injection
        "pk_live_abc:sk live SUPERSECRET",
        "pk_live_abc:sk_live_SUPERSÉCRET",
    ],
)
def test_malformed_api_key_is_refused_without_echoing_it(bad_key: str) -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        PayHub(api_key=bad_key, country="bf")
    assert "SUPER" not in str(exc_info.value)


def test_non_string_credentials_are_refused() -> None:
    with pytest.raises(ConfigurationError):
        PayHub(api_key=b"pk:sk", country="bf")  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        PayHub(key_id="pk", secret=None, country="bf")


def test_configuration_error_is_also_a_value_error() -> None:
    with pytest.raises(ValueError):
        PayHub(country="bf")
    assert issubclass(ConfigurationError, PayHubError)


# --- base URL: https only ------------------------------------------------------


@pytest.mark.parametrize(
    "base_url",
    [
        "http://hub.barkapay.com",  # clear text
        "file:///etc",
        "ftp://hub.barkapay.com",
        "data:text/plain,hello",
        "//hub.barkapay.com",
        "hub.barkapay.com",
        "https://user:pass@hub.barkapay.com",
        "https://hub.barkapay.com?x=1",
        "https://hub.barkapay.com/#frag",
        "https://hub.barkapay.com/ path",
        "https://hub.barkapay.com:notaport",
        "",
    ],
)
def test_unsafe_base_url_is_refused(base_url: str) -> None:
    with pytest.raises(ConfigurationError):
        PayHub(api_key=KEY, country="bf", base_url=base_url)


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://hub.barkapay.com/", "https://hub.barkapay.com/bf/v1/ping"),
        ("https://proxy.example.com/barkapay", "https://proxy.example.com/barkapay/bf/v1/ping"),
        ("http://localhost:8080", "http://localhost:8080/bf/v1/ping"),
        ("http://127.0.0.1:8080/", "http://127.0.0.1:8080/bf/v1/ping"),
        ("http://[::1]:8080", "http://[::1]:8080/bf/v1/ping"),
    ],
)
def test_accepted_base_urls(base_url: str, expected: str) -> None:
    transport = FakeTransport([ok({})])
    PayHub(api_key=KEY, country="bf", base_url=base_url, transport=transport).ping()
    assert transport.requests[0].url == expected


# --- country and identifiers cannot rewrite the path ---------------------------


@pytest.mark.parametrize("country", ["BF", " bf ", "Bf"])
def test_country_is_normalised(country: str) -> None:
    client, transport, _ = make_client([ok({})], country=country)
    client.ping()
    assert transport.requests[0].url == "https://hub.barkapay.com/bf/v1/ping"


@pytest.mark.parametrize(
    "country", ["../admin", "bf/v1/x", "b", "bf?x=1", "b f", "bfbf", "bfa", "civ", "ʙꜰ", "12"]
)
def test_invalid_country_is_refused(country: str) -> None:
    with pytest.raises(ConfigurationError):
        PayHub(api_key=KEY, country=country)
    client, transport, _ = make_client([ok({})])
    with pytest.raises(ConfigurationError):
        client.ping(country)
    assert transport.requests == []


def test_missing_country_is_refused_before_any_request() -> None:
    transport = FakeTransport([ok({})])
    client = PayHub(api_key=KEY, transport=transport)
    with pytest.raises(ConfigurationError):
        client.balance.get()
    assert transport.requests == []


@pytest.mark.parametrize("bad_id", ["", "   ", ".", "..", "abc\n", "a\x00b", "x" * 256])
def test_unusable_identifier_is_refused_before_any_request(bad_id: str) -> None:
    client, transport, _ = make_client([ok({})] * 12)
    calls: list[Callable[[], object]] = [
        lambda: client.payments.get(bad_id),
        lambda: client.payments.confirm_otp(bad_id, "123456"),
        lambda: client.payments.resend_otp(bad_id),
        lambda: client.transfers.get(bad_id),
        lambda: client.payment_links.get(bad_id),
        lambda: client.payment_links.update(bad_id, {"title": "x"}),
        lambda: client.payment_links.cancel(bad_id),
        lambda: client.payment_links.pause(bad_id),
        lambda: client.payment_links.resume(bad_id),
        lambda: client.payment_links.list_payments(bad_id),
    ]
    for call in calls:
        with pytest.raises(ConfigurationError):
            call()
    assert transport.requests == []


def test_non_string_identifier_is_refused() -> None:
    client, transport, _ = make_client([ok({})])
    with pytest.raises(ConfigurationError):
        client.payments.get(None)  # type: ignore[arg-type]
    assert transport.requests == []


def test_identifier_is_encoded_as_a_single_path_segment() -> None:
    client, transport, _ = make_client([ok({"payments": [samples.payment()]})])
    client.payments.get("ORDER 2026 x=y&z%é")
    assert (
        transport.requests[0].url
        == "https://hub.barkapay.com/bf/v1/payments/ORDER%202026%20x%3Dy%26z%25%C3%A9"
    )


@pytest.mark.parametrize(
    "identifier",
    ["../../bj/v1/balance", "INV/2026/0001", "x?per_page=200", "x#frag", "?", "#", "/"],
)
def test_identifiers_that_could_leave_their_segment_are_refused(identifier: str) -> None:
    client, transport, _ = make_client([])
    calls: list[Callable[[], object]] = [
        lambda: client.payments.get(identifier),
        lambda: client.payments.confirm_otp(identifier, "123456"),
        lambda: client.payments.resend_otp(identifier),
        lambda: client.transfers.get(identifier),
        lambda: client.payment_links.get(identifier),
        lambda: client.payment_links.update(identifier, {"title": "x"}),
        lambda: client.payment_links.cancel(identifier),
        lambda: client.payment_links.pause(identifier),
        lambda: client.payment_links.resume(identifier),
        lambda: client.payment_links.list_payments(identifier),
    ]
    for call in calls:
        with pytest.raises(ConfigurationError):
            call()
    assert transport.requests == []


# --- bodies and query strings --------------------------------------------------


def test_body_is_compact_utf8_json_with_content_type() -> None:
    client, transport, _ = make_client([ok({"payment": samples.payment()})])
    client.payments.create({"operator": "ORANGE", "order": {"id": "Café-1"}})
    request = transport.requests[0]
    assert request.headers["Content-Type"] == "application/json"
    assert request.body == '{"operator":"ORANGE","order":{"id":"Café-1"}}'


def test_get_requests_carry_no_body_and_no_content_type() -> None:
    client, transport, _ = make_client([ok({})])
    client.ping()
    assert transport.requests[0].body is None
    assert "Content-Type" not in transport.requests[0].headers


def test_decimal_amounts_are_sent_as_exact_json_numbers() -> None:
    client, transport, _ = make_client([ok({"transfer": samples.transfer()})] * 2)
    client.transfers.create({"amount": Decimal("5000"), "order": {"id": "A"}})
    client.transfers.create({"amount": Decimal("100.10"), "order": {"id": "B"}})
    assert '"amount":5000,' in (transport.requests[0].body or "")
    assert '"amount":100.1,' in (transport.requests[1].body or "")


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("NaN"),
        Decimal("Infinity"),
        float("nan"),
        float("inf"),
        Decimal("0.1234567890123456789"),
    ],
)
def test_amounts_that_cannot_be_sent_exactly_are_refused(amount: object) -> None:
    client, transport, _ = make_client([ok({"transfer": samples.transfer()})])
    with pytest.raises(ConfigurationError):
        client.transfers.create({"amount": amount})
    assert transport.requests == []


def test_unserialisable_params_raise_a_configuration_error() -> None:
    client, transport, _ = make_client([ok({"payment": samples.payment()})])
    with pytest.raises(ConfigurationError):
        client.payments.create({"order": {"data": {"x": object()}}})
    assert transport.requests == []


def test_aware_datetimes_are_serialised_and_naive_ones_refused() -> None:
    client, transport, _ = make_client([ok({"payment_link": samples.payment_link()})] * 2)
    expires = dt.datetime(2026, 12, 31, 23, 59, 59, tzinfo=dt.timezone.utc)
    client.payment_links.create({"title": "x", "amount": {"type": "open"}, "expires_at": expires})
    assert sent_json(transport)["expires_at"] == "2026-12-31T23:59:59+00:00"

    with pytest.raises(ConfigurationError):
        client.payment_links.create({"title": "x", "expires_at": dt.datetime(2026, 12, 31)})
    assert len(transport.requests) == 1


def test_query_values_are_rendered_for_the_api() -> None:
    client, transport, _ = make_client([ok({"payments": []})])
    client.payments.list(
        {
            "status": "SUCCESSFUL",
            "from_date": dt.date(2026, 11, 1),
            "to_date": dt.date(2026, 11, 16),
            "amount": Decimal("1000"),
            "amount_operator": ">=",
            "per_page": 50,
            "page": 2,
            "operator": None,  # dropped
        }
    )
    assert transport.requests[0].url == (
        "https://hub.barkapay.com/bf/v1/payments?status=SUCCESSFUL&from_date=2026-11-01"
        "&to_date=2026-11-16&amount=1000&amount_operator=%3E%3D&per_page=50&page=2"
    )


def test_empty_query_adds_no_question_mark() -> None:
    client, transport, _ = make_client([ok({"payments": []})] * 2)
    client.payments.list()
    client.payments.list({"status": None})
    assert [r.url for r in transport.requests] == ["https://hub.barkapay.com/bf/v1/payments"] * 2


# --- responses that are not what the API returns -------------------------------


@pytest.mark.parametrize("body", ["<html>Just a moment…</html>", "", "[]", "null", '"ok"', "{"])
def test_a_2xx_that_is_not_the_json_envelope_is_an_error_not_an_empty_object(body: str) -> None:
    client, _, _ = make_client([Response(200, body, {"x-request-id": "rid-from-header"})])
    with pytest.raises(ApiError) as exc_info:
        client.payments.create({"operator": "ORANGE"})
    assert exc_info.value.code == "invalid_response"
    assert exc_info.value.http_status == 200
    assert exc_info.value.request_id == "rid-from-header"


def test_a_2xx_without_the_expected_resource_is_an_error() -> None:
    client, _, _ = make_client([ok({"something_else": {}})])
    with pytest.raises(ApiError) as exc_info:
        client.transfers.create({"operator": "ORANGE"})
    assert exc_info.value.code == "invalid_response"


def test_a_redirect_is_surfaced_as_an_error_and_never_followed() -> None:
    client, transport, _ = make_client(
        [Response(302, "", {"Location": "https://evil.example/steal"})], max_retries=2
    )
    with pytest.raises(ApiError) as exc_info:
        client.ping()
    assert exc_info.value.http_status == 302
    assert len(transport.requests) == 1


def test_non_json_error_body_still_maps_to_a_typed_error() -> None:
    client, _, _ = make_client([Response(502, "<html>Bad gateway</html>")])
    with pytest.raises(ApiError) as exc_info:
        client.payments.create({"operator": "ORANGE"})
    assert exc_info.value.http_status == 502
    assert "502" in str(exc_info.value)


def test_error_status_in_a_2xx_envelope_is_still_an_error() -> None:
    client, _, _ = make_client(
        [json_response(200, {"status": "failed", "code": "payment_failed", "message": "no"})]
    )
    with pytest.raises(ApiError):
        client.ping()


def test_ping_and_me_return_the_data_block() -> None:
    client, transport, _ = make_client(
        [
            ok({"version": "2.0.0", "documentation": "https://example.com"}),
            ok({"merchant": {"public_id": "M-1"}, "api_app": {"scopes": ["payments:read"]}}),
        ]
    )
    assert client.ping()["version"] == "2.0.0"
    assert client.me()["api_app"]["scopes"] == ["payments:read"]
    assert [r.url.rsplit("/", 1)[1] for r in transport.requests] == ["ping", "me"]


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.payments.list(),
        lambda c: c.payments.get("ORDER-1"),
        lambda c: c.transfers.list(),
        lambda c: c.transfers.get("ORDER-1"),
        lambda c: c.payment_links.list(),
        lambda c: c.payment_links.list_payments(samples.LINK_ID),
        lambda c: c.balance.get(),
    ],
)
@pytest.mark.parametrize("envelope", [{"status": "success"}, {"status": "success", "data": []}])
def test_a_malformed_success_is_never_passed_off_as_an_empty_result(
    call: Callable[[PayHub], object], envelope: dict[str, object]
) -> None:
    client, _, _ = make_client([json_response(200, envelope)])
    with pytest.raises(ApiError) as exc_info:
        call(client)
    assert exc_info.value.code == "invalid_response"


def test_an_empty_list_from_the_api_is_a_valid_empty_page() -> None:
    client, _, _ = make_client(
        [ok({"transfers": []}, {"current_page": 1, "last_page": 1, "per_page": 50, "total": 0})]
    )
    page = client.transfers.list()
    assert len(page) == 0 and list(page) == []
    assert page.meta is not None and page.meta.total == 0
