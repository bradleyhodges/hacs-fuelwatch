"""Numeric automation entities with stable profile/product/rank identities."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.helpers.entity import EntityCategory

from .const import FEED_SOURCE, FEED_URL, PRODUCTS
from .entity import FuelWatchEntity


async def async_setup_entry(hass, entry, async_add_entities):
    for sid, sub in entry.subentries.items():
        entities = []
        if sub.subentry_type == "vehicle":
            for metric, label in (
                ("volume", "Litres to purchase"),
                ("remaining", "Fuel remaining"),
            ):
                entities.append(FuelWatchSensor(entry, sid, metric, label, "L"))
        elif sub.subentry_type in ("search", "station"):
            products = ["combined"] if sub.data.get("combine") else sub.data["products"]
            limit = sub.data.get("limit", 5) if sub.subentry_type == "search" else 1
            for product in products:
                title = PRODUCTS.get(product, "Accepted fuels")
                for rank in range(1, limit + 1):
                    for metric, label, unit in (
                        ("price", "price", "c/L"),
                        ("fill_cost", "refill cost", "AUD"),
                    ):
                        entities.append(
                            FuelWatchSensor(
                                entry, sid, metric, f"{title} #{rank} {label}", unit, product, rank
                            )
                        )
                entities.append(
                    FuelWatchSensor(
                        entry, sid, "next_price", f"{title} next-period price", "c/L", product
                    )
                )
                entities.append(
                    FuelWatchSensor(
                        entry, sid, "price_change", f"{title} next-period change", "c/L", product
                    )
                )
                entities.append(
                    FuelWatchSensor(
                        entry, sid, "full_tank_cost", f"{title} full-tank cost", "AUD", product
                    )
                )
        elif sub.subentry_type == "comparison":
            for metric, label, unit in (
                ("gross_saving", "Gross saving", "AUD"),
                ("travel_cost", "Additional fuel cost", "AUD"),
                ("net_saving", "Net saving", "AUD"),
                ("time_cost", "Additional time cost", "AUD"),
                ("break_even_litres", "Break-even volume", "L"),
            ):
                entities.append(FuelWatchSensor(entry, sid, metric, label, unit))
        async_add_entities(entities, config_subentry_id=sid)
    async_add_entities([SourceSensor(entry)])


class FuelWatchSensor(FuelWatchEntity, SensorEntity):
    def __init__(self, entry, sid, metric, name, unit, product=None, rank=1):
        identity = f"{product}_{rank}_{metric}" if product else metric
        super().__init__(entry, sid, identity, name)
        self.metric, self.product, self.rank = metric, product, rank
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = (
            "mdi:gas-station" if unit == "c/L" else "mdi:cash" if unit == "AUD" else "mdi:car"
        )
        if unit == "AUD":
            self._attr_device_class = SensorDeviceClass.MONETARY
        else:
            self._attr_state_class = SensorStateClass.MEASUREMENT
        if metric == "fill_cost" and not entry.subentries[sid].data.get("vehicle_id"):
            self._attr_entity_registry_enabled_default = False

    def row(self, period="current"):
        rows = self.view(period).get("groups", {}).get(self.product, [])
        return rows[self.rank - 1] if len(rows) >= self.rank else None

    @property
    def native_value(self):
        if self.product:
            basis = "effective_price" if self.view().get("price_basis") == "effective" else "price"
            row = self.row("next" if self.metric == "next_price" else "current")
            if self.metric == "price_change":
                upcoming = self.row("next")
                return round(upcoming[basis] - row[basis], 3) if row and upcoming else None
            return (
                row.get(basis if self.metric in ("price", "next_price") else self.metric)
                if row
                else None
            )
        view = self.view()
        if self.entry.subentries[self.sid].subentry_type == "vehicle":
            return (view.get("vehicle") or {}).get(self.metric)
        return view.get("comparison", {}).get(self.metric)

    @property
    def available(self):
        return super().available and self.native_value is not None

    @property
    def extra_state_attributes(self):
        if self.product:
            row = self.row("next" if self.metric == "next_price" else "current")
            return (row or {}) | {
                "tracking_id": self.sid,
                "price_basis": self.view().get("price_basis"),
                "error": self.view().get("error"),
            }
        return {
            "tracking_id": self.sid,
            "error": self.view().get("error") or (self.view().get("vehicle") or {}).get("error"),
        }


class SourceSensor(FuelWatchEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:cloud-check"

    def __init__(self, entry):
        super().__init__(entry, "service", "status", "Data status")

    @property
    def native_value(self):
        return (
            "degraded"
            if self.coordinator.errors
            else "ready"
            if self.coordinator.quotes
            else "no_prices"
        )

    @property
    def extra_state_attributes(self):
        return {
            "last_attempt": self.coordinator.data.get("updated") if self.coordinator.data else None,
            "errors": self.coordinator.errors,
            "catalogue_stations": len(self.coordinator.catalogue),
            "source": FEED_SOURCE,
            "api_url": FEED_URL,
            "snapshots": self.coordinator.source_metadata,
        }
