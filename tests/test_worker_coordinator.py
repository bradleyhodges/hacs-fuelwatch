"""Exercise worker JSON and saved identities through the real HA coordinator."""

import json
from dataclasses import replace
from hashlib import sha256
from unittest.mock import AsyncMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fuelwatch_wa.api import FuelWatchClient, parse_feed
from custom_components.fuelwatch_wa.coordinator import FuelWatchCoordinator
from tests.test_api import DAY, FakeResponse, FakeSession, enriched_feed, feed


async def test_saved_station_id_survives_json_refresh_without_duplicate_catalogue(hass, freezer):
    freezer.move_to("2026-09-29T04:00:00Z")
    entry = MockConfigEntry(domain="fuelwatch_wa", data={}, title="FuelWatch")
    entry.add_to_hass(hass)
    old_id = sha256(b"1 test road|perth").hexdigest()[:24]
    quote = replace(parse_feed(feed(), "1", DAY)[0], station_id=old_id)
    coordinator = FuelWatchCoordinator(hass, entry)
    coordinator.client = FuelWatchClient(FakeSession([FakeResponse()]))
    coordinator.store.async_load = AsyncMock(
        return_value={
            "snapshots": {"1/2026-09-29": [quote.to_dict()]},
            "catalogue": {old_id: quote.to_dict() | {"products": ["1"]}},
        }
    )
    await coordinator.async_load()
    await coordinator.async_refresh()
    assert coordinator.quotes == (quote,)
    assert set(coordinator.catalogue) == {old_id}
    assert coordinator.errors == {}
    await coordinator.async_shutdown()


async def test_worker_provenance_and_enrichment_survive_coordinator_reload(hass, freezer):
    freezer.move_to("2026-09-29T09:00:00Z")
    entry = MockConfigEntry(domain="fuelwatch_wa", data={}, title="FuelWatch")
    entry.add_to_hass(hass)
    coordinator = FuelWatchCoordinator(hass, entry)
    coordinator.client = FuelWatchClient(
        FakeSession(
            [FakeResponse(body=enriched_feed()), FakeResponse(503, headers={"Retry-After": "120"})]
        )
    )
    await coordinator.async_refresh()
    key = "1/2026-09-29"
    assert coordinator.fetched[key] == "2026-09-29T16:00:00+08:00"
    assert coordinator.source_metadata[key]["source"] == "fuelwatch.wa.gov.au"
    assert coordinator.quotes[0].details.phone == "+61899811151"
    other = FuelWatchCoordinator(hass, entry)
    await other.async_load()
    assert other.fetched[key] == coordinator.fetched[key]
    assert other.quotes == coordinator.quotes
    assert other.source_metadata == coordinator.source_metadata
    await coordinator.async_shutdown()
    await other.async_shutdown()


async def test_empty_replacement_keeps_prices_details_and_original_age(hass, freezer):
    freezer.move_to("2026-09-29T04:00:00Z")
    entry = MockConfigEntry(domain="fuelwatch_wa", data={}, title="FuelWatch")
    entry.add_to_hass(hass)
    empty = json.loads(feed())
    empty["data"] = []
    empty["meta"]["publicationStatus"] = "empty"
    coordinator = FuelWatchCoordinator(hass, entry)
    coordinator.client = FuelWatchClient(
        FakeSession(
            [FakeResponse(body=enriched_feed()), FakeResponse(body=json.dumps(empty).encode())]
        )
    )
    await coordinator.async_refresh()
    before, fetched = coordinator.quotes, dict(coordinator.fetched)
    freezer.move_to("2026-09-29T05:00:00Z")
    await coordinator.async_refresh()
    assert coordinator.quotes == before
    assert coordinator.fetched == fetched
    assert "retaining" in coordinator.errors["1/2026-09-29"]
    await coordinator.async_shutdown()
