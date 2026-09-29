"""Validated, response-returning automation actions."""

import voluptuous as vol
from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError

from .access import can_read
from .const import DOMAIN
from .runtime import build_view


def get_entry(hass):
    for entry in hass.config_entries.async_entries(DOMAIN):
        if getattr(entry, "runtime_data", None) is not None:
            return entry
    raise HomeAssistantError("FuelWatch is not loaded")


@callback
def async_register_services(hass):
    if hass.services.has_service(DOMAIN, "query"):
        return

    async def query(call):
        entry = get_entry(hass)
        await check_access(call, entry)
        try:
            return build_view(
                hass, entry, call.data["tracking_id"], period=call.data.get("period", "current")
            )
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err

    async def compare(call):
        entry = get_entry(hass)
        await check_access(call, entry)
        sid = call.data["tracking_id"]
        if sid not in entry.subentries or entry.subentries[sid].subentry_type != "comparison":
            raise HomeAssistantError("Choose a comparison tracking entry")
        return build_view(
            hass,
            entry,
            sid,
            period=call.data.get("period", "current"),
            overrides={k: call.data[k] for k in ("extra_km", "extra_minutes") if k in call.data},
        )

    async def refresh(call):
        await get_entry(hass).runtime_data.async_request_refresh()

    async def check_access(call, entry):
        if call.context.user_id:
            user = await hass.auth.async_get_user(call.context.user_id)
            if not user or not can_read(hass, user, entry, call.data["tracking_id"]):
                raise HomeAssistantError("Tracking entry unavailable or access denied")

    base = {
        vol.Required("tracking_id"): vol.All(str, vol.Length(min=1, max=64)),
        vol.Optional("period", default="current"): vol.In(["current", "next"]),
    }
    hass.services.async_register(
        DOMAIN, "query", query, schema=vol.Schema(base), supports_response=SupportsResponse.ONLY
    )
    hass.services.async_register(
        DOMAIN,
        "compare",
        compare,
        schema=vol.Schema(
            base
            | {
                vol.Optional("extra_km"): vol.All(vol.Coerce(float), vol.Range(min=0, max=10000)),
                vol.Optional("extra_minutes"): vol.All(
                    vol.Coerce(float), vol.Range(min=0, max=10000)
                ),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(DOMAIN, "refresh", refresh, schema=vol.Schema({}))
