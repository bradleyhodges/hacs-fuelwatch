"""References may share a vehicle without becoming a permission cycle."""

from types import SimpleNamespace

from homeassistant.config_entries import ConfigSubentry
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fuelwatch_wa.websocket import can_read
from tests.test_runtime import entry_with, sub


def test_shared_vehicle_reference_is_not_a_cycle():
    user = SimpleNamespace(
        is_admin=True, permissions=SimpleNamespace(check_entity=lambda entity, policy: True)
    )
    entry = entry_with(
        {
            "car": sub("vehicle", {"fuel_entity": "sensor.fuel"}),
            "search": sub("search", {"vehicle_id": "car"}),
            "compare": sub("comparison", {"vehicle_id": "car", "search_id": "search"}),
        }
    )
    assert can_read(None, user, entry, "compare")


def test_denied_telemetry_is_not_exposed():
    user = SimpleNamespace(
        is_admin=True, permissions=SimpleNamespace(check_entity=lambda entity, policy: False)
    )
    entry = entry_with({"car": sub("vehicle", {"fuel_entity": "sensor.private_fuel"})})
    assert not can_read(None, user, entry, "car")


async def test_own_profile_permissions_protect_coordinates(hass):
    profile = ConfigSubentry(
        subentry_type="search",
        title="Private location",
        unique_id="home",
        data={"latitude": -31.95, "longitude": 115.86},
    )
    entry = MockConfigEntry(domain="fuelwatch_wa", data={}, subentries_data=[profile.as_dict()])
    entry.add_to_hass(hass)
    registered = er.async_get(hass).async_get_or_create(
        "sensor",
        "fuelwatch_wa",
        "private-price",
        config_entry=entry,
        config_subentry_id=profile.subentry_id,
    )
    allowed = set()
    user = SimpleNamespace(
        is_admin=False,
        permissions=SimpleNamespace(check_entity=lambda entity, policy: entity in allowed),
    )
    assert not can_read(hass, user, entry, profile.subentry_id)
    allowed.add(registered.entity_id)
    assert can_read(hass, user, entry, profile.subentry_id)
