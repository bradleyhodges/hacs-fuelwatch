"""End-to-end backend setup, entity values, telemetry updates and unloading."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigSubentry
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fuelwatch_wa.coordinator import FuelWatchCoordinator
from custom_components.fuelwatch_wa.entity import FuelWatchEntity
from tests.test_engine import quote, snapshot
from tests.test_runtime import sub


async def test_station_change_starts_distinct_history_but_search_rank_is_stable(hass):
    entry = MockConfigEntry(domain="fuelwatch_wa", data={})
    entry.add_to_hass(hass)
    coord = FuelWatchCoordinator(hass, entry)
    view_entry = SimpleNamespace(
        entry_id=entry.entry_id,
        runtime_data=coord,
        subentries={"station": sub("station", {"station_id": "a"})},
    )
    a = FuelWatchEntity(view_entry, "station", "1_price", "Price")
    view_entry.subentries["station"].data["station_id"] = "b"
    b = FuelWatchEntity(view_entry, "station", "1_price", "Price")
    assert a.unique_id != b.unique_id
    view_entry.subentries["search"] = sub("search", {"radius": 5})
    first = FuelWatchEntity(view_entry, "search", "1_rank_1_price", "Price")
    view_entry.subentries["search"].data["radius"] = 10
    second = FuelWatchEntity(view_entry, "search", "1_rank_1_price", "Price")
    assert first.unique_id == second.unique_id
    await coord.async_shutdown()


async def test_setup_sensor_update_and_unload(hass, freezer):
    freezer.move_to("2026-09-29T04:00:00Z")
    vehicle = ConfigSubentry(
        subentry_type="vehicle",
        title="Car",
        unique_id="car",
        data={
            "purchase_mode": "current",
            "capacity": 50,
            "fuel_format": "percentage",
            "fuel_entity": "sensor.fuel",
        },
    )
    search = ConfigSubentry(
        subentry_type="search",
        title="Home",
        unique_id="home",
        data={
            "latitude": -31.95,
            "longitude": 115.86,
            "radius": 10,
            "products": ["1"],
            "limit": 1,
            "vehicle_id": vehicle.subentry_id,
        },
    )
    entry = MockConfigEntry(
        domain="fuelwatch_wa",
        data={"show_sidebar": False},
        title="FuelWatch",
        subentries_data=[vehicle.as_dict(), search.as_dict()],
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.fuel", "25", {"unit_of_measurement": "%"})
    with (
        patch(
            "custom_components.fuelwatch_wa.api.FuelWatchClient.fetch_snapshot",
            AsyncMock(return_value=snapshot(quote())),
        ),
        patch("custom_components.fuelwatch_wa.panel.async_setup_panel", AsyncMock()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        uid = f"{entry.entry_id}_{vehicle.subentry_id}_volume"
        entity_id = registry.async_get_entity_id("sensor", "fuelwatch_wa", uid)
        assert hass.states.get(entity_id).state == "37.5"
        hass.states.async_set("sensor.fuel", "50", {"unit_of_measurement": "%"})
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).state == "25.0"
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).state == "unavailable"
