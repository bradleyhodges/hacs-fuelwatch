"""Validate sidebar registration/removal through Home Assistant's real API."""

from homeassistant.components.frontend import DATA_PANELS
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fuelwatch_wa.panel import async_remove_panel, async_setup_panel


async def test_sidebar_registration_and_removal(hass):
    assert await async_setup_component(hass, "frontend", {})
    entry = MockConfigEntry(domain="fuelwatch_wa", data={"show_sidebar": True})
    entry.add_to_hass(hass)
    await async_setup_panel(hass, entry)
    assert hass.data[DATA_PANELS]["fuelwatch-wa"].sidebar_title == "FuelWatch"
    async_remove_panel(hass)
    assert "fuelwatch-wa" not in hass.data[DATA_PANELS]
