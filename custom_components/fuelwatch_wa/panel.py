"""Serve one local frontend bundle for the sidebar and optional Lovelace card."""

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig

from .const import DOMAIN, PANEL_URL, VERSION


async def async_setup_panel(hass, entry):
    state = hass.data.setdefault(DOMAIN, {})
    if not state.get("static_registered"):
        await hass.http.async_register_static_paths(
            [StaticPathConfig("/fuelwatch_wa_static", str(Path(__file__).parent / "www"), True)]
        )
        state["static_registered"] = True
    if entry.options.get("show_sidebar", entry.data.get("show_sidebar", True)):
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=PANEL_URL,
            webcomponent_name="fuelwatch-wa-panel",
            sidebar_title="FuelWatch",
            sidebar_icon="mdi:gas-station",
            module_url=f"/fuelwatch_wa_static/fuelwatch-wa-card.js?v={VERSION}",
            embed_iframe=False,
            require_admin=False,
            config={},
        )


def async_remove_panel(hass):
    frontend.async_remove_panel(hass, PANEL_URL)
