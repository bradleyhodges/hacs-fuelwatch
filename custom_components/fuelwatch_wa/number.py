"""Persisted manual fuel level for vehicles without a telemetry sensor."""

from homeassistant.components.number import NumberEntity, NumberMode

from .engine import decimal, fuel_litres
from .entity import FuelWatchEntity


async def async_setup_entry(hass, entry, async_add_entities):
    for sid, sub in entry.subentries.items():
        if (
            sub.subentry_type == "vehicle"
            and sub.data.get("purchase_mode") == "current"
            and not sub.data.get("fuel_entity")
        ):
            async_add_entities([ManualFuelLevel(entry, sid)], config_subentry_id=sid)


class ManualFuelLevel(FuelWatchEntity, NumberEntity):
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:gas-station"
    _attr_native_min_value = 0
    _attr_native_step = 0.1

    def __init__(self, entry, sid):
        super().__init__(entry, sid, "manual_level", "Manual fuel level")
        cfg = entry.subentries[sid].data
        mode = cfg.get("fuel_format", "percentage")
        self._attr_native_max_value = (
            100 if mode == "percentage" else 1 if mode == "proportion" else cfg["capacity"]
        )
        self._attr_native_unit_of_measurement = (
            "%" if mode == "percentage" else "L" if mode == "litres" else None
        )
        if mode == "proportion":
            self._attr_native_step = 0.01

    @property
    def native_value(self):
        return self.coordinator.manual_levels.get(
            self.sid, self.entry.subentries[self.sid].data.get("manual_level", 50)
        )

    async def async_set_native_value(self, value):
        cfg = self.entry.subentries[self.sid].data
        fuel_litres(value, cfg["fuel_format"], cfg["capacity"])
        self.coordinator.manual_levels[self.sid] = float(decimal(value))
        await self.coordinator.async_save()
        self.coordinator.views = {}
        self.coordinator.async_update_listeners()
