"""Verify upstream validation without depending on today's changing prices."""

import json
from datetime import date
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from custom_components.fuelwatch_wa.api import FeedError, parse_feed

DAY = date(2026, 9, 29)


@pytest.mark.parametrize("expanded", [False, True])
def test_station_reference_codes_and_expanded_objects_keep_human_labels(expanded):
    document = json.loads(feed())
    item = document["data"][0]["attributes"]
    item["brand"] = (
        {"code": 5, "name": "BP", "logo": "/static/image/brand/bp.svg"} if expanded else 5
    )
    item["siteFeatures"] = (
        [{"code": 1, "name": "Credit Cards"}, {"code": 4, "name": "ATM"}] if expanded else [1, 4]
    )
    item["restrictions"] = [{"code": 3, "name": "Membership Required"}] if expanded else [3]
    quote = parse_feed(json.dumps(document).encode(), "1", DAY)[0]
    assert quote.brand == "BP"
    assert quote.details.site_features == ("Credit Cards", "ATM")
    assert quote.details.restrictions == ("Membership Required",)


@pytest.mark.parametrize(
    "brand", [True, 999, {"code": 5, "name": "Shell"}, {"code": True, "name": "BP"}]
)
def test_invalid_reference_codes_or_conflicting_names_reject_the_snapshot(brand):
    with pytest.raises(FeedError):
        parse_feed(feed(brand=brand), "1", DAY)


def test_unmapped_brand_retains_its_source_name_in_compact_and_expanded_forms():
    for brand in [0, {"code": 0, "name": "Quest Fuel", "logo": "/static/image/brand/generic.svg"}]:
        document = json.loads(feed(brand=brand))
        document["data"][0]["attributes"]["sourceNotes"] = {"brand": "Quest Fuel"}
        quote = parse_feed(json.dumps(document).encode(), "1", DAY)[0]
        assert quote.brand == "Quest Fuel"
        assert dict(quote.details.source_notes)["brand"] == "Quest Fuel"


def test_shared_worker_fixture_uses_compact_codes_and_decodes_them():
    path = Path(__file__).parents[1] / "api-worker/tests/fixtures/fuelwatch-v1.json"
    payload = path.read_bytes()
    assert json.loads(payload)["data"][0]["attributes"]["brand"] == 5
    assert parse_feed(payload, "1", DAY)[0].brand == "BP"


def feed(price=185.9, day="2026-09-29", brand="Test"):
    fixture = Path(__file__).parents[1] / "api-worker/tests/fixtures/fuelwatch-v1.json"
    data = json.loads(fixture.read_text())
    data["data"][0]["attributes"].update(
        {"price": {"products": {"1": price}, "asAt": f"{day}T06:00:00.000+08:00"}, "brand": brand}
    )
    return json.dumps(data).encode()


def change(**values):
    data = json.loads(feed())
    for key, value in values.items():
        if key in ("data", "errors", "jsonapi"):
            data[key] = value
        else:
            data["meta"][key] = value
    return json.dumps(data).encode()


def first_price(value):
    return {"products": {"1": value}, "asAt": "2026-09-29T06:00:00.000+08:00"}


def test_parse_quote_and_brand_independent_identity():
    result = parse_feed(feed(), "1", DAY)
    assert len(result) == 1
    assert result[0].price == Decimal("185.9")
    assert result[0].station_id == parse_feed(feed(brand="Rebranded"), "1", DAY)[0].station_id


@pytest.mark.parametrize(
    "data",
    [
        b"<html>Forbidden</html>",
        b"not JSON",
        feed(price="NaN"),
        feed(price="-1"),
        feed(day="2026-09-28"),
        feed().replace(b"-31.95", b"999"),
        b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss><channel><item>&x;</item></channel></rss>',
        change(errors=[{"status": "502", "code": "invalid_feed"}]),
        change(data=[{"type": "wrongType", "id": "1", "attributes": {}}]),
        change(product=4),
        change(product=True),
        change(sourceDate="2026-09-28"),
        change(validFrom="2026-09-29T00:00:00Z"),
        change(validUntil="2026-09-29T22:01:00Z"),
        change(fetchedAt="not a timestamp"),
        change(fetchedAt="2026-09-29T10:00:00"),
        change(publicationStatus="empty"),
        change(data="invalid"),
        feed(price=True),
        feed(price={"value": "185.9"}),
        feed().replace(b'"1": 185.9', b'"1": 185.9, "1": 100'),
        feed().replace(b"T06:00:00.000+08:00", b"T00:00:00.000Z"),
        feed().replace(b'"latitude": -31.95', b'"latitude": "-31.95"'),
        feed().replace(b'"state": "WA"', b'"state": "NSW"'),
    ],
)
def test_untrusted_response_is_rejected_atomically(data):
    with pytest.raises(FeedError):
        parse_feed(data, "1", DAY)


def test_empty_valid_feed_is_distinct_from_failure():
    data = json.loads(feed())
    data["data"] = []
    data["meta"]["publicationStatus"] = "empty"
    assert parse_feed(json.dumps(data).encode(), "1", DAY) == ()


@pytest.mark.parametrize(
    "changes",
    [
        {"id": ""},
        {"id": 123},
        {"id": "x" * 201},
        {"type": "stations"},
        {"attributes": []},
        {"attributes": None},
    ],
)
def test_jsonapi_resource_structure_is_validated(changes):
    data = json.loads(feed())
    data["data"][0].update(changes)
    with pytest.raises(FeedError):
        parse_feed(json.dumps(data).encode(), "1", DAY)


@pytest.mark.parametrize("product", [2, True, "1", None])
def test_resource_fuel_must_match_the_requested_snapshot(product):
    data = json.loads(feed())
    data["data"][0]["attributes"]["product"] = product
    with pytest.raises(FeedError):
        parse_feed(json.dumps(data).encode(), "1", DAY)


def test_grouped_products_are_selected_without_inventing_unavailable_fuel():
    document = json.loads(feed())
    document["meta"].pop("product")
    document["meta"]["products"] = [1, 2, 6]
    document["data"][0]["attributes"]["price"]["products"] = {"1": 185.9, "2": 200.2}
    payload = json.dumps(document).encode()
    assert parse_feed(payload, "1", DAY)[0].price == Decimal("185.9")
    assert parse_feed(payload, "2", DAY)[0].price == Decimal("200.2")
    assert parse_feed(payload, "6", DAY) == ()
    with pytest.raises(FeedError):
        parse_feed(payload, "4", DAY)


def test_previous_deployed_price_schema_is_supported_during_upgrade():
    document = json.loads(feed())
    resource = document["data"][0]
    resource["type"] = "fuelPrices"
    item = resource["attributes"]
    item["product"] = 1
    item["price"]["perLitre"] = item["price"].pop("products")["1"]
    assert parse_feed(json.dumps(document).encode(), "1", DAY) == parse_feed(feed(), "1", DAY)


def test_duplicate_station_validation_is_atomic():
    data = json.loads(feed())
    data["data"] *= 2
    with pytest.raises(FeedError):
        parse_feed(json.dumps(data).encode(), "1", DAY)
    data["data"][1] = data["data"][1] | {
        "id": "another",
        "attributes": data["data"][1]["attributes"] | {"price": first_price(199.9)},
    }
    with pytest.raises(FeedError):
        parse_feed(json.dumps(data).encode(), "1", DAY)


def test_neighbouring_stations_can_share_an_address():
    data = json.loads(feed())
    first = data["data"][0]
    data["data"].append(
        first
        | {
            "id": "neighbour",
            "attributes": first["attributes"] | {"longitude": 115.87, "price": first_price(199.9)},
        }
    )
    quotes = parse_feed(json.dumps(data).encode(), "1", DAY)
    assert len(quotes) == 2
    assert quotes[0].station_id != quotes[1].station_id


def test_coordinate_formatting_does_not_change_identity():
    assert (
        parse_feed(feed(), "1", DAY)[0].station_id
        == parse_feed(feed().replace(b"115.86", b"115.86000000"), "1", DAY)[0].station_id
    )


def test_snapshot_size_and_station_limits():
    from custom_components.fuelwatch_wa.const import MAX_RESPONSE, MAX_STATIONS

    data = json.loads(feed())
    data["data"] *= MAX_STATIONS + 1
    for payload in (json.dumps(data).encode(), b"x" * (MAX_RESPONSE + 1)):
        with pytest.raises(FeedError):
            parse_feed(payload, "1", DAY)


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status, self.body = status, body or feed()
        self.headers = {"Content-Type": "application/vnd.api+json"} | (headers or {})
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def iter_chunked(self, size):
        yield self.body


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = 0
        self.calls = []

    def get(self, *args, **kwargs):
        self.requests += 1
        self.calls.append((args, kwargs))
        return next(self.responses)


async def test_client_uses_versioned_worker_json():
    from custom_components.fuelwatch_wa.api import FuelWatchClient

    session = FakeSession([FakeResponse()])
    assert len(await FuelWatchClient(session).fetch("1", DAY)) == 1
    args, kwargs = session.calls[0]
    assert args == ("https://fuelwatch.oss.bhodges.me/v1",)
    assert kwargs["params"] == {"filter[product]": "1", "filter[day]": "29/09/2026"}
    assert kwargs["headers"]["Accept"] == "application/vnd.api+json"
    assert kwargs["allow_redirects"] is False


async def test_saved_station_selections_survive_coordinate_identity_upgrade():
    from dataclasses import replace

    from custom_components.fuelwatch_wa.api import FuelWatchClient

    old_id = sha256(b"1 test road|perth").hexdigest()[:24]
    saved = replace(parse_feed(feed(), "1", DAY)[0], station_id=old_id)
    data = json.loads(feed(brand="New brand"))
    data["data"].append(
        data["data"][0]
        | {"id": "neighbour", "attributes": data["data"][0]["attributes"] | {"longitude": 115.87}}
    )
    client = FuelWatchClient(FakeSession([FakeResponse(body=json.dumps(data).encode())]))
    client.restore_station_identity(saved)
    quotes = await client.fetch("1", DAY)
    assert quotes[0].station_id == old_id
    assert quotes[1].station_id != old_id
    assert len({quote.station_id for quote in quotes}) == 2


async def test_latest_saved_coordinate_replaces_ambiguous_old_identity_mapping():
    from dataclasses import replace

    from custom_components.fuelwatch_wa.api import FuelWatchClient

    old_id = sha256(b"1 test road|perth").hexdigest()[:24]
    saved = replace(parse_feed(feed(), "1", DAY)[0], station_id=old_id)
    data = json.loads(feed())
    data["data"].append(
        data["data"][0]
        | {"id": "neighbour", "attributes": data["data"][0]["attributes"] | {"longitude": 115.87}}
    )
    client = FuelWatchClient(FakeSession([FakeResponse(body=json.dumps(data).encode())]))
    client.restore_station_identity(saved)
    client.restore_station_identity(replace(saved, longitude=115.87))
    quotes = await client.fetch("1", DAY)
    assert quotes[0].station_id != old_id
    assert quotes[1].station_id == old_id


async def test_http_retry_recovers_only_from_transient_failures(monkeypatch):
    from unittest.mock import AsyncMock

    from custom_components.fuelwatch_wa.api import FuelWatchClient

    monkeypatch.setattr("custom_components.fuelwatch_wa.api.asyncio.sleep", AsyncMock())
    session = FakeSession([FakeResponse(503), FakeResponse()])
    client = FuelWatchClient(session)
    assert (await client.fetch("1", DAY))[0].price == Decimal("185.9")
    assert session.requests == 2
    denied = FakeSession([FakeResponse(403)])
    with pytest.raises(FeedError, match="403"):
        await FuelWatchClient(denied).fetch("1", DAY)
    assert denied.requests == 1


async def test_oversized_http_response_is_rejected():
    from custom_components.fuelwatch_wa.api import FuelWatchClient
    from custom_components.fuelwatch_wa.const import MAX_RESPONSE

    client = FuelWatchClient(FakeSession([FakeResponse(body=b"x" * (MAX_RESPONSE + 1))]))
    with pytest.raises(FeedError, match="size"):
        await client.fetch("1", DAY)


def enriched_feed():
    document = json.loads(feed())
    item = document["data"][0]["attributes"]
    item.update(
        tradingName="Example Trading Name",
        phone="+61899811151",
        is24Hours=False,
        siteFeatures=["ATM", "Toilets"],
        openHours={"Monday": "06:00-20:30"},
        restrictions=["Membership Required"],
        sourceNotes={"features": ["Unclassified feature"], "openHours": "Public holidays vary"},
        enrichment={
            "provider": "Google Maps",
            "placeId": "place_123",
            "stale": False,
            "fetchedAt": "2026-09-28T12:00:00.000+08:00",
            "fields": ["address.postcode", "phone"],
            "googleMapsUri": "https://maps.google.com/?cid=123",
            "attributions": [{"displayName": "Example provider", "uri": "https://example.com/"}],
        },
    )
    item["address"]["postcode"] = "6000"
    return json.dumps(document).encode()


def test_station_details_and_attribution_survive_api_storage_and_entity_rows():
    from datetime import datetime

    from custom_components.fuelwatch_wa.engine import quote_row
    from custom_components.fuelwatch_wa.models import Quote

    quote = parse_feed(enriched_feed(), "1", DAY)[0]
    restored = Quote.from_dict(json.loads(json.dumps(quote.to_dict())))
    assert restored == quote
    row = quote_row(restored, [], datetime.fromisoformat("2026-09-29T12:00:00+08:00"))
    assert row["postcode"] == "6000"
    assert row["trading_name"] == "Example Trading Name"
    assert row["phone"] == "+61899811151"
    assert row["site_features"] == ["ATM", "Toilets"]
    assert row["open_hours"] == {"Monday": "06:00-20:30"}
    assert row["restrictions"] == ["Membership Required"]
    assert row["source_notes"]["open_hours"] == "Public holidays vary"
    assert row["enrichment"]["provider"] == "Google Maps"
    assert row["enrichment"]["attributions"][0]["uri"] == "https://example.com/"


async def test_conditional_requests_reuse_validated_snapshot_and_original_provenance():
    from custom_components.fuelwatch_wa.api import FuelWatchClient

    session = FakeSession(
        [FakeResponse(body=enriched_feed(), headers={"ETag": '"one"'}), FakeResponse(304)]
    )
    client = FuelWatchClient(session)
    first = await client.fetch_snapshot("1", DAY)
    second = await client.fetch_snapshot("1", DAY)
    assert second == first
    assert second.fetched_at.isoformat() == "2026-09-29T16:00:00+08:00"
    assert second.source == "fuelwatch.wa.gov.au"
    assert session.calls[1][1]["headers"]["If-None-Match"] == '"one"'
    assert second.quotes[0].price == Decimal("185.9")
    await client.async_close()


async def test_unsolicited_304_and_wrong_media_type_are_rejected():
    from custom_components.fuelwatch_wa.api import FuelWatchClient

    for response in [FakeResponse(304), FakeResponse(headers={"Content-Type": "text/html"})]:
        with pytest.raises(FeedError):
            await FuelWatchClient(FakeSession([response])).fetch("1", DAY)


def test_unknown_or_missing_source_is_rejected():
    with pytest.raises(FeedError):
        parse_feed(change(source="unknown.example"), "1", DAY)


@pytest.mark.parametrize(
    "prices",
    [
        {},
        {"1": None},
        {"1": True},
        {"1": "195.0"},
        {"1": -1},
        {"999": 200},
        {"1": 185.9, "2": 200},
        [],
    ],
)
def test_invalid_grouped_prices_reject_entire_snapshot(prices):
    document = json.loads(feed())
    document["data"][0]["attributes"]["price"]["products"] = prices
    with pytest.raises(FeedError):
        parse_feed(json.dumps(document).encode(), "1", DAY)


def test_old_saved_quotes_without_station_details_remain_loadable():
    from custom_components.fuelwatch_wa.models import Quote

    original = parse_feed(feed(), "1", DAY)[0]
    saved = original.to_dict()
    saved.pop("details")
    restored = Quote.from_dict(saved)
    assert restored.station_id == original.station_id
    assert restored.price == original.price
    assert restored.details.site_features == ()


async def test_cancelled_caller_does_not_cancel_another_consumers_shared_fetch():
    import asyncio

    from custom_components.fuelwatch_wa.api import FuelWatchClient

    started, release = asyncio.Event(), asyncio.Event()

    class SlowResponse(FakeResponse):
        async def __aenter__(self):
            started.set()
            await release.wait()
            return self

    session = FakeSession([SlowResponse()])
    client = FuelWatchClient(session)
    first = asyncio.create_task(client.fetch_snapshot("1", DAY))
    await started.wait()
    second = asyncio.create_task(client.fetch_snapshot("1", DAY))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert (await second).quotes[0].price == Decimal("185.9")
    assert session.requests == 1
    await client.async_close()
    assert not client._inflight
