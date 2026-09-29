"""Exercise worker JSON and saved identities through the real HA coordinator."""

from dataclasses import replace
from hashlib import sha256
from unittest.mock import AsyncMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fuelwatch_wa.api import FuelWatchClient, parse_feed
from custom_components.fuelwatch_wa.coordinator import FuelWatchCoordinator
from tests.test_api import DAY, FakeResponse, FakeSession, feed


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
