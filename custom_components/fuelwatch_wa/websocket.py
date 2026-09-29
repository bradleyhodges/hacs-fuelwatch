"""Authenticated bounded dashboard data; telemetry permissions are respected."""

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .access import can_read
from .const import PRODUCTS
from .runtime import build_view
from .services import get_entry


@callback
def async_register_websocket(hass):
    websocket_api.async_register_command(hass, websocket_view)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "fuelwatch_wa/view",
        vol.Optional("tracking_id"): vol.All(str, vol.Length(max=64)),
        vol.Optional("period", default="current"): vol.In(["current", "next"]),
        vol.Optional("extra_km"): vol.All(vol.Coerce(float), vol.Range(min=0, max=10000)),
        vol.Optional("extra_minutes"): vol.All(vol.Coerce(float), vol.Range(min=0, max=10000)),
    }
)
@websocket_api.async_response
async def websocket_view(hass, connection, msg):
    try:
        entry = get_entry(hass)
        profiles = [
            {"id": sid, "title": sub.title, "type": sub.subentry_type}
            for sid, sub in entry.subentries.items()
            if sub.subentry_type != "discount" and can_read(hass, connection.user, entry, sid)
        ]
        sid = msg.get("tracking_id")
        if sid and not any(p["id"] == sid for p in profiles):
            connection.send_error(
                msg["id"], "not_found", "Tracking entry unavailable or access denied"
            )
            return
        result = {
            "schema_version": 1,
            "profiles": profiles,
            "products": PRODUCTS,
            "source_errors": dict(entry.runtime_data.errors),
            "view": None,
        }
        if sid:
            result["view"] = build_view(
                hass,
                entry,
                sid,
                period=msg["period"],
                overrides={k: msg[k] for k in ("extra_km", "extra_minutes") if k in msg},
            )
        connection.send_result(msg["id"], result)
    except (HomeAssistantError, ValueError) as err:
        connection.send_error(msg["id"], "not_ready", str(err))
