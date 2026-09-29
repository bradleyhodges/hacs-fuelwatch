"""Exercise actual Home Assistant configuration/subentry flows."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigSubentry
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fuelwatch_wa.config_flow import validate_settings


@pytest.mark.parametrize(
    "kind,data",
    [
        ("search", {"name": "x", "products": [], "radius": 10}),
        (
            "vehicle",
            {
                "name": "x",
                "purchase_mode": "current",
                "capacity": 50,
                "fuel_format": "fraction",
                "manual_level": 50,
            },
        ),
        ("vehicle", {"name": "x", "purchase_mode": "full", "capacity": 0}),
        ("comparison", {"name": "x", "product": "1", "vehicle_id": "missing"}),
    ],
)
def test_config_rejects_ambiguous_or_missing_values(kind, data):
    with pytest.raises(ValueError):
        validate_settings(kind, data, {})


async def test_single_parent_config(hass):
    with patch("custom_components.fuelwatch_wa.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            "fuelwatch_wa", context={"source": "user"}
        )
        assert result["type"] == FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"show_sidebar": True}
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        second = await hass.config_entries.flow.async_init(
            "fuelwatch_wa", context={"source": "user"}
        )
        assert second["type"] == FlowResultType.ABORT


async def test_add_vehicle_subentry(hass):
    entry = MockConfigEntry(domain="fuelwatch_wa", data={}, title="FuelWatch", unique_id="wa")
    entry.add_to_hass(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "vehicle"), context={"source": "user"}
    )
    assert result["type"] == FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Cerato",
            "purchase_mode": "full",
            "capacity": 50,
            "fuel_format": "percentage",
            "manual_level": 50,
            "target": 100,
            "consumption": 7,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert list(entry.subentries.values())[0].title == "Cerato"


async def test_bad_vehicle_shows_form_error(hass):
    entry = MockConfigEntry(domain="fuelwatch_wa", data={}, title="FuelWatch")
    entry.add_to_hass(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "vehicle"), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Bad",
            "purchase_mode": "current",
            "capacity": 50,
            "fuel_format": "fraction",
            "manual_level": 50,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_input"


async def test_manual_units_reconfigure_discards_incompatible_persisted_level(hass):
    profile = ConfigSubentry(
        subentry_type="vehicle",
        title="Car",
        unique_id="car",
        data={
            "name": "Car",
            "purchase_mode": "current",
            "capacity": 50,
            "fuel_format": "proportion",
            "manual_level": 0.5,
        },
    )
    entry = MockConfigEntry(domain="fuelwatch_wa", data={}, subentries_data=[profile.as_dict()])
    entry.add_to_hass(hass)
    coord = SimpleNamespace(
        manual_levels={profile.subentry_id: 0.5}, async_save=AsyncMock(), catalogue={}
    )
    entry.runtime_data = coord
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "vehicle"),
        context={"source": "reconfigure", "subentry_id": profile.subentry_id},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Car",
            "purchase_mode": "current",
            "capacity": 50,
            "fuel_format": "percentage",
            "manual_level": 50,
        },
    )
    assert result["type"] == FlowResultType.ABORT
    assert profile.subentry_id not in coord.manual_levels
    coord.async_save.assert_awaited_once()
