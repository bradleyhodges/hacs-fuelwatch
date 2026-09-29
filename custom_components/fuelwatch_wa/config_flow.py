"""UI configuration for independent searches, stations, vehicles and comparisons."""

from datetime import date
from uuid import uuid4

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import DOMAIN, NAME, PRODUCTS
from .engine import decimal, purchase_litres


def validate_settings(kind: str, data: dict, subentries: dict) -> dict:
    """Canonical validation beyond frontend selectors, including references."""
    data = {k: v for k, v in data.items() if v is not None and v != ""}
    name = str(data.get("name", "")).strip()
    if not name or len(name) > 80:
        raise ValueError("Give this entry a name of 1–80 characters")
    data["name"] = name
    if kind in ("search", "station"):
        if not data.get("products") or any(p not in PRODUCTS for p in data["products"]):
            raise ValueError("Choose one or more fuel products")
        data["products"] = sorted(set(data["products"]))
    if kind == "vehicle":
        if data.get("purchase_mode") not in ("full", "fixed", "current"):
            raise ValueError("Choose a purchase mode")
        if (
            data.get("purchase_mode") == "current"
            and not data.get("fuel_entity")
            and data.get("fuel_format") == "fraction"
        ):
            raise ValueError(
                "Fraction text requires a source entity; use percentage for a manual level"
            )
        if data.get("purchase_mode") == "current" and data.get("fuel_entity"):
            decimal(data["capacity"], minimum="0.001", maximum=100000)
            if data.get("fuel_format") not in ("percentage", "proportion", "fraction", "litres"):
                raise ValueError("Choose a fuel-level format")
            decimal(data.get("target", 100), maximum=100)
        else:
            purchase_litres(data, data.get("manual_level", 50))
        if "consumption" in data:
            decimal(data["consumption"], maximum=1000)
    if kind == "search":
        if not data.get("zone"):
            decimal(data.get("latitude"), minimum=-90, maximum=90)
            decimal(data.get("longitude"), minimum=-180, maximum=180)
        decimal(data.get("radius"), minimum="0.1", maximum=1000)
        limit = decimal(data.get("limit", 5), minimum=1, maximum=20)
        if int(limit) != limit:
            raise ValueError("Result count must be a whole number")
        data["limit"] = int(limit)
        if data.get("vendor_mode", "all") not in ("all", "include", "exclude"):
            raise ValueError("Choose a vendor filter mode")
        if data.get("vendor_mode") == "include" and not data.get("vendors"):
            raise ValueError("Choose at least one included vendor")
    if kind == "station" and not data.get("station_id"):
        raise ValueError("Choose a station")
    if kind == "comparison":
        if data.get("product") not in PRODUCTS or not data.get("station_a"):
            raise ValueError("Choose a product and baseline station")
        if bool(data.get("station_b")) == bool(data.get("search_id")):
            raise ValueError("Choose either an alternative station or a cheapest-station search")
        if not data.get("vehicle_id"):
            raise ValueError("Choose a vehicle")
        for key in ("extra_km", "extra_minutes", "minimum_saving", "value_of_time"):
            if key in data:
                decimal(data[key], maximum=10000)
        if data.get("search_id"):
            search = subentries.get(data["search_id"])
            if (
                not search
                or search.subentry_type != "search"
                or search.data.get("combine")
                or data["product"] not in search.data.get("products", [])
            ):
                raise ValueError("Choose a search with a separate ranking for this product")
    if kind == "discount":
        if not data.get("vendor") and not data.get("station_id"):
            raise ValueError("Choose a vendor or a station for the discount")
        decimal(data.get("amount"), maximum=200)
        if data.get("expires"):
            date.fromisoformat(data["expires"])
    if data.get("vehicle_id"):
        target = subentries.get(data["vehicle_id"])
        if not target or target.subentry_type != "vehicle":
            raise ValueError("Choose an existing vehicle profile")
    return data


def select(options, *, multiple=False, custom=False):
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            multiple=multiple,
            mode=selector.SelectSelectorMode.DROPDOWN,
            custom_value=custom,
            sort=True,
        )
    )


def number(minimum, maximum, unit=None, step="any"):
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            mode=selector.NumberSelectorMode.BOX,
            **({"unit_of_measurement": unit} if unit else {}),
        )
    )


FUEL_ENTITY = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=["sensor", "input_number", "input_text"])
)


class FuelWatchConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """A single service owns shared data; child entries carry user profiles."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id("western_australia")
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title=NAME, data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("show_sidebar", default=True): selector.BooleanSelector(),
                }
            ),
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry):
        return {
            kind: TrackingFlow
            for kind in ("search", "station", "vehicle", "comparison", "discount")
        }

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return FuelWatchOptionsFlow()


class FuelWatchOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "show_sidebar",
                        default=self.config_entry.options.get(
                            "show_sidebar", self.config_entry.data.get("show_sidebar", True)
                        ),
                    ): selector.BooleanSelector(),
                }
            ),
        )


class TrackingFlow(config_entries.ConfigSubentryFlow):
    """Shared validated forms with product-first station and vendor discovery."""

    def __init__(self):
        self._draft = {}
        self._editing = False

    async def async_step_reconfigure(self, user_input=None):
        sub = self._get_reconfigure_subentry()
        self._draft = dict(sub.data) | {"name": sub.title}
        self._editing = True
        return await self.async_step_user(user_input)

    async def async_step_user(self, user_input=None):
        kind = self._subentry_type
        if kind in ("search", "station"):
            if user_input is not None:
                self._draft.update(user_input)
                return await self.async_step_details()
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Required(
                            "name", default=self._draft.get("name", "")
                        ): selector.TextSelector(),
                        vol.Required(
                            "products", default=self._draft.get("products", ["1"])
                        ): select(
                            [{"value": k, "label": v} for k, v in PRODUCTS.items()], multiple=True
                        ),
                    }
                ),
            )
        return await self._form("user", user_input)

    async def async_step_details(self, user_input=None):
        return await self._form("details", user_input)

    async def _form(self, step, user_input):
        entry = self._get_entry()
        kind = self._subentry_type
        errors = {}
        placeholders = {"error": ""}
        if user_input is not None:
            candidate = (
                {k: self._draft[k] for k in ("name", "products") if k in self._draft}
                if step == "details"
                else {}
            ) | user_input
            try:
                data = validate_settings(kind, candidate, entry.subentries)
            except (ValueError, KeyError) as err:
                errors["base"] = "invalid_input"
                placeholders["error"] = str(err)
                self._draft = candidate
            else:
                if self._editing:
                    previous = self._get_reconfigure_subentry()
                    coord = getattr(entry, "runtime_data", None)
                    level_keys = (
                        "fuel_format",
                        "capacity",
                        "purchase_mode",
                        "fuel_entity",
                        "manual_level",
                    )
                    if (
                        kind == "vehicle"
                        and coord
                        and any(previous.data.get(key) != data.get(key) for key in level_keys)
                    ):
                        # Start from the newly configured manual value. Stored
                        # readings have units tied to the previous configuration.
                        coord.manual_levels.pop(previous.subentry_id, None)
                        await coord.async_save()
                    return self.async_update_and_abort(
                        entry, previous, data=data, title=data["name"]
                    )
                return self.async_create_entry(title=data["name"], data=data, unique_id=uuid4().hex)
        coord = getattr(entry, "runtime_data", None)
        if coord and kind in ("search", "station"):
            await coord.async_ensure_products(self._draft.get("products", ["1"]))
        catalogue = coord.catalogue if coord else {}
        stations = [
            {"value": key, "label": f"{s['name']} — {s['address']}, {s['suburb']}"}
            for key, s in catalogue.items()
        ]
        vendors = sorted({s["brand"] for s in catalogue.values()})
        vehicles = [
            {"value": key, "label": s.title}
            for key, s in entry.subentries.items()
            if s.subentry_type == "vehicle"
        ]
        searches = [
            {"value": key, "label": s.title}
            for key, s in entry.subentries.items()
            if s.subentry_type == "search" and not s.data.get("combine")
        ]
        schema = {}

        def add(key, control, default=None, required=False):
            value = self._draft.get(key, default)
            marker = vol.Required if required else vol.Optional
            schema[marker(key, **({"default": value} if value is not None else {}))] = control

        if step == "user":
            add("name", selector.TextSelector(), "", True)
        if kind == "vehicle":
            add(
                "purchase_mode",
                select(
                    [
                        {"value": "full", "label": "Empty-to-full estimate"},
                        {"value": "current", "label": "Fill from current level"},
                        {"value": "fixed", "label": "Fixed litres"},
                    ]
                ),
                "full",
                True,
            )
            add("capacity", number(0.1, 100000, "L"), 50, True)
            add("volume", number(0, 100000, "L"), 40)
            add("fuel_entity", FUEL_ENTITY)
            add(
                "fuel_format",
                select(
                    [
                        {"value": "percentage", "label": "Percentage (0–100)"},
                        {"value": "proportion", "label": "Proportion (0–1)"},
                        {"value": "fraction", "label": "Fraction text (1/4)"},
                        {"value": "litres", "label": "Litres remaining"},
                    ]
                ),
                "percentage",
                True,
            )
            add("manual_level", number(0, 100000), 50)
            add("target", number(0, 100, "%"), 100)
            add("consumption", number(0, 1000, "L/100km"), 7)
            add("consumption_entity", FUEL_ENTITY)
        if kind == "search":
            add("zone", selector.EntitySelector(selector.EntitySelectorConfig(domain="zone")))
            add("latitude", number(-90, 90), self.hass.config.latitude)
            add("longitude", number(-180, 180), self.hass.config.longitude)
            add("radius", number(0.1, 1000, "km"), 10, True)
            add("limit", number(1, 20, step=1), 5, True)
            add("combine", selector.BooleanSelector(), False)
            add(
                "vendor_mode",
                select(
                    [
                        {"value": "all", "label": "All vendors"},
                        {"value": "include", "label": "Only selected vendors"},
                        {"value": "exclude", "label": "Exclude selected vendors"},
                    ]
                ),
                "all",
                True,
            )
            add("vendors", select(vendors, multiple=True, custom=True), [])
            add("excluded_stations", select(stations, multiple=True), [])
            add("only_stations", select(stations, multiple=True), [])
        if kind == "station":
            add("station_id", select(stations), required=True)
            if not stations:
                errors.setdefault("base", "cannot_connect")
        if kind in ("search", "station", "comparison"):
            add(
                "vehicle_id",
                select(
                    ([{"value": "", "label": "No vehicle"}] if kind != "comparison" else [])
                    + vehicles
                ),
                required=kind == "comparison",
            )
            add(
                "price_basis",
                select(
                    [
                        {"value": "advertised", "label": "Advertised price"},
                        {"value": "effective", "label": "After eligible discounts"},
                    ]
                ),
                "advertised",
                True,
            )
        if kind == "comparison":
            add(
                "product",
                select([{"value": k, "label": v} for k, v in PRODUCTS.items()]),
                "1",
                True,
            )
            add("station_a", select(stations), required=True)
            add("station_b", select([{"value": "", "label": "Use search instead"}] + stations))
            add("search_id", select([{"value": "", "label": "Use station instead"}] + searches))
            add("extra_km", number(0, 10000, "km"))
            add("extra_km_entity", FUEL_ENTITY)
            add("extra_minutes", number(0, 10000, "min"))
            add("extra_minutes_entity", FUEL_ENTITY)
            add("value_of_time", number(0, 10000, "AUD/h"), 0)
            add("minimum_saving", number(0, 10000, "AUD"), 1)
        if kind == "discount":
            add("vendor", select(vendors, custom=True))
            add("station_id", select([{"value": "", "label": "Apply to vendor"}] + stations))
            add("amount", number(0, 200, "c/L"), 4, True)
            add(
                "products",
                select([{"value": k, "label": v} for k, v in PRODUCTS.items()], multiple=True),
                [],
            )
            add("enabled", selector.BooleanSelector(), True)
            add("expires", selector.DateSelector())
            add(
                "eligibility_entity",
                selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["input_boolean", "binary_sensor", "switch"]
                    )
                ),
            )
        return self.async_show_form(
            step_id=step,
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders=placeholders,
        )
