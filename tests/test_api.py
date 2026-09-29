"""Verify upstream validation without depending on today's changing prices."""

import json
from datetime import date
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from custom_components.fuelwatch_wa.api import FeedError, parse_feed

DAY = date(2026, 9, 29)


def feed(price=185.9, day="2026-09-29", brand="Test"):
    fixture = Path(__file__).parents[1] / "api-worker/tests/fixtures/fuelwatch-v1.json"
    data = json.loads(fixture.read_text())
    data["feed"]["items"][0].update({"price": {"perLitre": price, "asAt": f"{day}T00:00:00.000+08:00"}, "brand": brand})
    return json.dumps(data).encode()


def change(**values):
    return json.dumps(json.loads(feed()) | values).encode()


def first_price(value):
    return {"perLitre": value, "asAt": "2026-09-29T00:00:00.000+08:00"}


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
        change(schemaVersion=2),
        change(schemaVersion=True),
        change(product=4),
        change(product=True),
        change(sourceDate="2026-09-28"),
        change(validFrom="2026-09-29T00:00:00Z"),
        change(validUntil="2026-09-29T22:01:00Z"),
        change(fetchedAt="not a timestamp"),
        change(fetchedAt="2026-09-29T10:00:00"),
        change(publicationStatus="empty"),
        change(feed={"items": "invalid"}),
        feed(price=True),
        feed(price={"value": "185.9"}),
        feed().replace(b'"perLitre": 185.9', b'"perLitre": 185.9, "perLitre": 100'),
        feed().replace(b"T00:00:00.000+08:00", b"T00:00:00.000Z"),
        feed().replace(b'"latitude": -31.95', b'"latitude": "-31.95"'),
        feed().replace(b'"state": "WA"', b'"state": "NSW"'),
    ],
)
def test_untrusted_response_is_rejected_atomically(data):
    with pytest.raises(FeedError):
        parse_feed(data, "1", DAY)


def test_empty_valid_feed_is_distinct_from_failure():
    data = json.loads(feed())
    data["feed"]["items"] = []
    data["publicationStatus"] = "empty"
    assert parse_feed(json.dumps(data).encode(), "1", DAY) == ()


def test_duplicate_station_validation_is_atomic():
    data = json.loads(feed())
    data["feed"]["items"] *= 2
    assert len(parse_feed(json.dumps(data).encode(), "1", DAY)) == 1
    data["feed"]["items"][1] = data["feed"]["items"][1] | {"price": first_price(199.9)}
    with pytest.raises(FeedError):
        parse_feed(json.dumps(data).encode(), "1", DAY)


def test_neighbouring_stations_can_share_an_address():
    data = json.loads(feed())
    first = data["feed"]["items"][0]
    data["feed"]["items"].append(first | {"longitude": 115.87, "price": first_price(199.9)})
    quotes = parse_feed(json.dumps(data).encode(), "1", DAY)
    assert len(quotes) == 2
    assert quotes[0].station_id != quotes[1].station_id


def test_coordinate_formatting_does_not_change_identity():
    assert parse_feed(feed(), "1", DAY)[0].station_id == parse_feed(
        feed().replace(b"115.86", b"115.86000000"), "1", DAY
    )[0].station_id


def test_snapshot_size_and_station_limits():
    from custom_components.fuelwatch_wa.const import MAX_RESPONSE, MAX_STATIONS

    data = json.loads(feed())
    data["feed"]["items"] *= MAX_STATIONS + 1
    for payload in (json.dumps(data).encode(), b"x" * (MAX_RESPONSE + 1)):
        with pytest.raises(FeedError):
            parse_feed(payload, "1", DAY)


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status, self.body, self.headers = status, body or feed(), headers or {}
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
    assert kwargs["params"] == {"Product": "1", "Day": "29/09/2026"}
    assert kwargs["headers"]["Accept"] == "application/json"
    assert kwargs["allow_redirects"] is False


async def test_saved_station_selections_survive_coordinate_identity_upgrade():
    from dataclasses import replace

    from custom_components.fuelwatch_wa.api import FuelWatchClient

    old_id = sha256(b"1 test road|perth").hexdigest()[:24]
    saved = replace(parse_feed(feed(), "1", DAY)[0], station_id=old_id)
    data = json.loads(feed(brand="New brand"))
    data["feed"]["items"].append(data["feed"]["items"][0] | {"longitude": 115.87})
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
    data["feed"]["items"].append(data["feed"]["items"][0] | {"longitude": 115.87})
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
