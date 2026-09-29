"""Stable profile identities and shared, memoised presentation data."""

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, NAME
from .runtime import build_view


def get_view(hass, entry, sid, period="current"):
    coordinator = entry.runtime_data
    if not hasattr(coordinator, "views"):
        coordinator.views = {}
    key = (sid, period, dt_util.utcnow().replace(second=0, microsecond=0))
    if key not in coordinator.views:
        coordinator.views[key] = build_view(hass, entry, sid, period=period)
    return coordinator.views[key]


class FuelWatchEntity(CoordinatorEntity):
    """Each child owns a device; references never share device ownership."""

    _attr_has_entity_name = True

    def __init__(self, entry, sid, metric, name):
        super().__init__(entry.runtime_data)
        self.entry, self.sid = entry, sid
        identity = f"{entry.entry_id}_{sid}"
        sub = entry.subentries.get(sid)
        if sub and sub.subentry_type == "station":
            identity += f"_{sub.data['station_id']}"
        self._attr_unique_id = f"{identity}_{metric}"
        self._attr_name = name
        title = entry.subentries[sid].title if sid in entry.subentries else NAME
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{sid}")},
            name=title,
            manufacturer="FuelWatch WA",
            model="Tracking profile" if sid in entry.subentries else "FuelWatch service",
        )

    def view(self, period="current"):
        return get_view(self.hass, self.entry, self.sid, period)

    @property
    def available(self):
        return self.coordinator.data is not None
