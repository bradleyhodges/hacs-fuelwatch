"""A shared debounced refresh action."""

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import EntityCategory

from .entity import FuelWatchEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([RefreshButton(entry)])


class RefreshButton(FuelWatchEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:refresh"

    def __init__(self, entry):
        super().__init__(entry, "service", "refresh", "Refresh prices")

    async def async_press(self):
        await self.coordinator.async_request_refresh()
