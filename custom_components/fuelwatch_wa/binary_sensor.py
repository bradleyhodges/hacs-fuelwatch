"""Threshold-based comparisons remain unknown if required inputs are missing."""

from homeassistant.components.binary_sensor import BinarySensorEntity

from .entity import FuelWatchEntity


async def async_setup_entry(hass, entry, async_add_entities):
    for sid, sub in entry.subentries.items():
        if sub.subentry_type == "comparison":
            async_add_entities([WorthItSensor(entry, sid)], config_subentry_id=sid)


class WorthItSensor(FuelWatchEntity, BinarySensorEntity):
    _attr_icon = "mdi:cash-check"

    def __init__(self, entry, sid):
        super().__init__(entry, sid, "worth_it", "Worth the detour")

    @property
    def is_on(self):
        return self.view().get("worth_it")

    @property
    def available(self):
        return super().available and self.is_on is not None
