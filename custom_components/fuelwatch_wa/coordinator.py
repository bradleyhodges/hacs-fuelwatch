"""Shared, persistent price snapshots with independent per-product failures."""

import asyncio
import logging
import math
from datetime import date, timedelta

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import FeedError, FuelWatchClient
from .const import DOMAIN, MAX_STATIONS, PERTH, PRODUCTS
from .engine import active_day
from .models import Quote

_LOGGER = logging.getLogger(__name__)


class FuelWatchCoordinator(DataUpdateCoordinator[dict]):
    """One feed owner regardless of the number of configured entities."""

    def __init__(self, hass, entry):
        super().__init__(
            hass, _LOGGER, name=DOMAIN, config_entry=entry, update_interval=timedelta(hours=1)
        )
        self.entry = entry
        self.client = FuelWatchClient(async_get_clientsession(hass))
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self.snapshots = {}
        self.fetched = {}
        self.errors = {}
        self.catalogue = {}
        self.manual_levels = {}
        self._fetch_lock = asyncio.Lock()
        self._last_attempt = None
        self._extra_products = set()
        self.views = {}

    @property
    def quotes(self) -> tuple[Quote, ...]:
        return tuple(q for snapshot in self.snapshots.values() for q in snapshot)

    def required_products(self):
        products = {"1"} | self._extra_products
        for sub in self.entry.subentries.values():
            products.update(sub.data.get("products", []))
            if product := sub.data.get("product"):
                products.add(product)
        return products & PRODUCTS.keys()

    async def async_load(self):
        """Reject corrupt cache records independently; never unpickle data."""
        try:
            saved = await self.store.async_load() or {}
        except ValueError, TypeError, KeyError:
            _LOGGER.warning("Ignoring invalid FuelWatch cache; fresh data will be requested")
            return
        if not isinstance(saved, dict):
            return
        snapshots = saved.get("snapshots", {})
        for key, rows in snapshots.items() if isinstance(snapshots, dict) else ():
            try:
                if len(rows) > MAX_STATIONS:
                    continue
                product, day = key.split("/", 1)
                if product not in PRODUCTS:
                    continue
                expected = date.fromisoformat(day)
                parsed = tuple(Quote.from_dict(row) for row in rows)
                if any(
                    q.day != expected
                    or q.product != product
                    or not q.price.is_finite()
                    or q.price <= 0
                    for q in parsed
                ):
                    continue
                self.snapshots[key] = parsed
                for quote in parsed:
                    self.client.restore_station_identity(quote)
            except ValueError, TypeError, KeyError, ArithmeticError, AttributeError:
                _LOGGER.warning("Ignoring an invalid FuelWatch snapshot")
        catalogue = saved.get("catalogue", {})
        for key, row in catalogue.items() if isinstance(catalogue, dict) else ():
            try:
                quote = Quote.from_dict({k: v for k, v in row.items() if k != "products"})
                if not (-90 <= quote.latitude <= 90 and -180 <= quote.longitude <= 180):
                    continue
                if not all(
                    isinstance(getattr(quote, field), str)
                    for field in ("name", "brand", "address", "suburb", "station_id")
                ):
                    continue
                self.catalogue[key] = quote.to_dict() | {
                    "products": [p for p in row.get("products", []) if p in PRODUCTS]
                }
                self.client.restore_station_identity(quote)
            except ValueError, TypeError, KeyError, ArithmeticError, AttributeError:
                continue
        for field in ("fetched", "manual_levels"):
            values = saved.get(field, {})
            if isinstance(values, dict):
                setattr(
                    self,
                    field,
                    {
                        key: value
                        for key, value in values.items()
                        if isinstance(key, str)
                        and (
                            isinstance(value, str)
                            if field == "fetched"
                            else isinstance(value, (int, float)) and math.isfinite(value)
                        )
                    },
                )
        self._prune()

    def _prune(self):
        oldest = active_day(dt_util.utcnow()) - timedelta(days=1)
        for key in list(self.snapshots):
            if date.fromisoformat(key.split("/", 1)[1]) < oldest:
                self.snapshots.pop(key, None)
                self.fetched.pop(key, None)
                self.errors.pop(key, None)

    async def async_save(self):
        await self.store.async_save(
            {
                "snapshots": {
                    key: [q.to_dict() for q in rows] for key, rows in self.snapshots.items()
                },
                "fetched": self.fetched,
                "catalogue": self.catalogue,
                "manual_levels": self.manual_levels,
            }
        )

    async def async_ensure_products(self, products):
        """Populate selectors once a user has chosen the products they need."""
        missing = set(products) - self.required_products()
        self._extra_products.update(products)
        if missing or not self.catalogue:
            if missing:
                self._last_attempt = None
            await self.async_refresh()

    async def _async_update_data(self):
        async with self._fetch_lock:
            now = dt_util.utcnow()
            if self._last_attempt and (now - self._last_attempt).total_seconds() < 30:
                return self.data or {}
            self._last_attempt = now
            current = active_day(now)
            days = [current]
            local = now.astimezone(PERTH)
            # Before 06:00 the next period is already published; between 06:00
            # and 14:30 tomorrow is not expected to be available.
            if local.hour < 6 or (local.hour, local.minute) >= (14, 30):
                days.append(current + timedelta(days=1))

            async def fetch_one(product, day):
                key = f"{product}/{day}"
                try:
                    rows = await self.client.fetch(product, day)
                except FeedError as err:
                    self.errors[key] = str(err)
                    return
                # Client validates actual quote dates; do not mix product periods.
                if any(q.product != product or q.day != day for q in rows):
                    self.errors[key] = "Quote does not match requested product/date"
                    return
                self.snapshots[key] = rows
                self.fetched[key] = now.isoformat()
                self.errors.pop(key, None)
                for quote in rows:
                    old = self.catalogue.get(quote.station_id, {})
                    self.catalogue[quote.station_id] = quote.to_dict() | {
                        "products": sorted(set(old.get("products", [])) | {product})
                    }

            await asyncio.gather(*(fetch_one(p, d) for p in self.required_products() for d in days))
            self._prune()
            await self.async_save()
            self.views = {}
            return {"updated": now.isoformat(), "errors": dict(self.errors)}

    async def async_shutdown(self):
        await self.client.async_close()
        await super().async_shutdown()
