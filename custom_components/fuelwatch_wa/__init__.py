"""FuelWatch WA integration lifecycle."""

from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_utc_time_change

from .coordinator import FuelWatchCoordinator
from .panel import async_remove_panel, async_setup_panel
from .services import async_register_services
from .websocket import async_register_websocket

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.NUMBER, Platform.BUTTON]


async def async_setup(hass, config):
    async_register_services(hass)
    async_register_websocket(hass)
    return True


async def async_setup_entry(hass, entry):
    """Retain useful offline configuration even when the feed is unreachable."""
    coordinator = FuelWatchCoordinator(hass, entry)
    entry.runtime_data = coordinator
    await coordinator.async_load()
    await coordinator.async_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_setup_panel(hass, entry)

    @callback
    def local_update(_event=None):
        coordinator.views = {}
        coordinator.async_update_listeners()

    sources = set()
    for sub in entry.subentries.values():
        for key, value in sub.data.items():
            if (key.endswith("_entity") or key == "zone") and value:
                sources.add(value)
    if sources:
        entry.async_on_unload(async_track_state_change_event(hass, sources, local_update))
    # Local timestamps, next-period changes and discount expiry never wait for
    # network success. Minute ticks do no network I/O.
    entry.async_on_unload(async_track_utc_time_change(hass, local_update, second=0))

    async def scheduled_refresh(_now):
        local_update()
        await coordinator.async_request_refresh()

    # Perth UTC+8: 06:00 rollover; 14:31 publication, then bounded late checks.
    entry.async_on_unload(
        async_track_utc_time_change(hass, scheduled_refresh, hour=22, minute=0, second=1)
    )
    for minute in (31, 36, 46):
        entry.async_on_unload(
            async_track_utc_time_change(hass, scheduled_refresh, hour=6, minute=minute, second=0)
        )
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    async_remove_panel(hass)
    await entry.runtime_data.async_shutdown()
    return True
