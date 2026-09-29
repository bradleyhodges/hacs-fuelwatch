"""Runtime behaviour using Home Assistant's own test fixture."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fuelwatch_wa.api import FeedError
from custom_components.fuelwatch_wa.coordinator import FuelWatchCoordinator
from custom_components.fuelwatch_wa.runtime import build_view, vehicle_view
from tests.test_engine import quote, snapshot


def sub(kind, data, name="Example"):
    return SimpleNamespace(subentry_type=kind, data=data, title=name)


def entry_with(entries):
    return SimpleNamespace(entry_id="test", title="FuelWatch", subentries=entries)


def test_vehicle_unknown_state_does_not_become_empty(hass):
    cfg = {
        "purchase_mode": "current",
        "capacity": 50,
        "fuel_format": "percentage",
        "fuel_entity": "sensor.fuel",
    }
    assert vehicle_view(hass, cfg)["volume"] is None
    hass.states.async_set("sensor.fuel", "25", {"unit_of_measurement": "%"})
    assert vehicle_view(hass, cfg)["volume"] == 37.5
    hass.states.async_set("sensor.fuel", "unavailable")
    assert vehicle_view(hass, cfg)["volume"] is None


async def test_comparison_and_missing_reference(hass):
    now = datetime(2026, 9, 29, 12, tzinfo=ZoneInfo("Australia/Perth"))
    vehicle = sub("vehicle", {"purchase_mode": "fixed", "volume": 37.5, "consumption": 7})
    comparison = sub(
        "comparison",
        {"vehicle_id": "car", "product": "1", "station_a": "a", "station_b": "b", "extra_km": 10},
    )
    entry = entry_with({"car": vehicle, "compare": comparison})
    coordinator = SimpleNamespace(
        quotes=(quote("a", "185.9"), quote("b", "177.9")), errors={}, fetched={}, catalogue={}
    )
    entry.runtime_data = coordinator
    view = build_view(hass, entry, "compare", now)
    assert view["comparison"]["net_saving"] == 1.7
    del entry.subentries["car"]
    assert build_view(hass, entry, "compare", now)["error"] == "Referenced vehicle is missing"


async def test_missing_distance_sensor_preserves_gross_savings(hass):
    now = datetime(2026, 9, 29, 12, tzinfo=ZoneInfo("Australia/Perth"))
    entry = entry_with(
        {
            "car": sub("vehicle", {"purchase_mode": "fixed", "volume": 37.5, "consumption": 7}),
            "compare": sub(
                "comparison",
                {
                    "vehicle_id": "car",
                    "product": "1",
                    "station_a": "a",
                    "station_b": "b",
                    "extra_km_entity": "sensor.distance",
                },
            ),
        }
    )
    entry.runtime_data = SimpleNamespace(
        quotes=(quote("a", "185.9"), quote("b", "177.9")), errors={}, fetched={}, catalogue={}
    )
    result = build_view(hass, entry, "compare", now)
    assert result["comparison"]["gross_saving"] == 3
    assert result["comparison"]["net_saving"] is None


async def test_partial_failure_retains_valid_prices(hass, freezer):
    freezer.move_to("2026-09-29T04:00:00Z")
    entry = MockConfigEntry(domain="fuelwatch_wa", data={}, title="FuelWatch")
    entry.add_to_hass(hass)
    coord = FuelWatchCoordinator(hass, entry)
    coord.client.fetch_snapshot = AsyncMock(return_value=snapshot(quote()))
    await coord.async_refresh()
    assert len(coord.quotes) > 0
    coord.client.fetch_snapshot = AsyncMock(side_effect=FeedError("Offline"))
    freezer.tick(31)
    await coord.async_refresh()
    assert coord.quotes[0].price == quote().price
    assert coord.errors
    await coord.async_shutdown()


async def test_persisted_snapshots_survive_reload(hass, freezer):
    freezer.move_to("2026-09-29T04:00:00Z")
    entry = MockConfigEntry(domain="fuelwatch_wa", data={}, title="FuelWatch")
    entry.add_to_hass(hass)
    coord = FuelWatchCoordinator(hass, entry)
    coord.client.fetch_snapshot = AsyncMock(return_value=snapshot(quote()))
    await coord.async_refresh()
    await coord.async_save()
    other = FuelWatchCoordinator(hass, entry)
    await other.async_load()
    assert other.quotes[0] == quote()
    await coord.async_shutdown()
    await other.async_shutdown()


async def test_one_corrupt_cache_bucket_does_not_discard_other_data(hass, freezer):
    freezer.move_to("2026-09-29T04:00:00Z")
    entry = MockConfigEntry(domain="fuelwatch_wa", data={})
    entry.add_to_hass(hass)
    coord = FuelWatchCoordinator(hass, entry)
    coord.store.async_load = AsyncMock(
        return_value={
            "snapshots": {"bad": [{}], "1/2026-09-29": [quote().to_dict()]},
            "manual_levels": {"car": 50},
            "catalogue": {"a": quote().to_dict() | {"products": ["1"]}},
        }
    )
    await coord.async_load()
    assert coord.quotes == (quote(),)
    assert coord.manual_levels == {"car": 50}
    assert "a" in coord.catalogue
    await coord.async_shutdown()
