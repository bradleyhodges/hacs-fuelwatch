"""Asynchronous adapter for the FuelWatch worker's JSON:API price resources."""

import asyncio
import json
import random
import re
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from hashlib import sha256

import aiohttp

from .const import FEED_URL, MAX_RESPONSE, MAX_STATIONS, PERTH, PRODUCTS
from .engine import decimal
from .models import Quote


class FeedError(Exception):
    """The upstream response is unavailable or violates the feed contract."""


def _normalise(value: str) -> str:
    return " ".join(value.casefold().split())


def _station_id(address: str, suburb: str, latitude: float, longitude: float) -> str:
    identity = f"{_normalise(address)}|{_normalise(suburb)}|{latitude}|{longitude}"
    return sha256(identity.encode()).hexdigest()[:24]


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject ambiguous JSON instead of silently accepting the last duplicate key."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Invalid timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return parsed


def parse_feed(payload: bytes, product: str, expected_date: date) -> tuple[Quote, ...]:
    """Validate a complete product/period snapshot before replacing cached data."""
    if product not in PRODUCTS or len(payload) > MAX_RESPONSE:
        raise FeedError("Unsupported product or oversized response")
    try:
        # Parse JSON decimal numbers directly to Decimal; never round cents through a binary float.
        document = json.loads(payload, object_pairs_hook=_unique_object, parse_float=Decimal)
        if not isinstance(document, dict):
            raise ValueError("Expected a JSON object")
        if "errors" in document:
            raise ValueError("Upstream returned an error document")
        metadata = document.get("meta")
        if not isinstance(metadata, dict):
            raise ValueError("Expected snapshot metadata")
        if type(metadata.get("product")) is not int or metadata["product"] != int(product):
            raise ValueError("Unexpected product")
        if metadata.get("sourceDate") != expected_date.isoformat():
            raise ValueError("Unexpected snapshot date")
        valid_from = datetime.combine(expected_date, time(6), PERTH)
        if _timestamp(metadata.get("validFrom")) != valid_from or _timestamp(
            metadata.get("validUntil")
        ) != valid_from + timedelta(days=1):
            raise ValueError("Unexpected price validity window")
        _timestamp(metadata.get("fetchedAt"))
        items = document.get("data")
        if not isinstance(items, list) or len(items) > MAX_STATIONS:
            raise ValueError("Invalid station list")
        status = metadata.get("publicationStatus")
        if (items and status != "available") or (
            not items and status not in ("empty", "not_yet_published")
        ):
            raise ValueError("Inconsistent publication status")
        quotes = {}
        resource_ids: set[str] = set()
        for resource in items:
            if not isinstance(resource, dict) or resource.get("type") != "fuelPrices":
                raise ValueError("Invalid price resource")
            resource_id = resource.get("id")
            if (
                not isinstance(resource_id, str)
                or not resource_id.strip()
                or len(resource_id) > 200
                or resource_id in resource_ids
            ):
                raise ValueError("Invalid or duplicate resource identity")
            resource_ids.add(resource_id)
            item = resource.get("attributes")
            if not isinstance(item, dict):
                raise ValueError("Expected resource attributes")
            if type(item.get("product")) is not int or item["product"] != int(product):
                raise ValueError("Unexpected quote product")

            def field(name: str, required: bool = True, source: dict | None = None) -> str:
                value = (item if source is None else source).get(name, "")
                if not isinstance(value, str) or len(value) > 500:
                    raise ValueError(f"Invalid {name}")
                value = value.strip()
                if required and not value:
                    raise ValueError(f"Invalid {name}")
                return value

            price = item.get("price")
            address_fields = item.get("address")
            if not isinstance(price, dict) or not isinstance(address_fields, dict):
                raise ValueError("Expected structured price and address")
            as_at = _timestamp(price.get("asAt"))
            if as_at != valid_from:
                raise ValueError("Unexpected quote date")
            for value in (item.get("latitude"), item.get("longitude"), price.get("perLitre")):
                if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
                    raise ValueError("Expected a JSON number")
            latitude = float(decimal(item["latitude"], minimum=-90, maximum=90))
            longitude = float(decimal(item["longitude"], minimum=-180, maximum=180))
            address, suburb = (
                field("street", source=address_fields),
                field("suburb", source=address_fields),
            )
            if address_fields.get("state") != "WA":
                raise ValueError("Unexpected address state")
            # Neighbouring sites can share an address in the real feed. Include
            # coordinates, keeping identity independent of price and rebranding.
            station_id = _station_id(address, suburb, latitude, longitude)
            quote = Quote(
                station_id=station_id,
                product=product,
                day=expected_date,
                price=decimal(price["perLitre"], minimum="0.001", maximum=10000),
                name=field("name"),
                brand=field("brand", False) or "Independent",
                address=address,
                suburb=suburb,
                latitude=latitude,
                longitude=longitude,
            )
            if station_id in quotes and quotes[station_id] != quote:
                raise ValueError("Conflicting station identity")
            quotes[station_id] = quote
        return tuple(quotes.values())
    except Exception as err:
        raise FeedError(f"Invalid FuelWatch response: {type(err).__name__}") from err


class FuelWatchClient:
    """Bounded requests using Home Assistant's shared HTTP session.

    Concurrent identical requests share one task. Cancellation of a caller does
    not cancel another caller's request. async_close cancels remaining work.
    """

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self._semaphore = asyncio.Semaphore(2)
        self._inflight = {}
        self._legacy_station_ids: dict[str, str] = {}

    def restore_station_identity(self, quote: Quote) -> None:
        """Preserve saved address-only selectors for their recorded coordinates.

        An old ID may refer to only one site. Load snapshots first, then the
        catalogue, so its last known coordinates win without duplicate aliases.
        """
        if not isinstance(quote.address, str) or not isinstance(quote.suburb, str):
            return
        old_identity = f"{_normalise(quote.address)}|{_normalise(quote.suburb)}"
        old_id = sha256(old_identity.encode()).hexdigest()[:24]
        if quote.station_id != old_id:
            return
        try:
            latitude = float(decimal(quote.latitude, minimum=-90, maximum=90))
            longitude = float(decimal(quote.longitude, minimum=-180, maximum=180))
        except ValueError:
            return
        self._legacy_station_ids[old_id] = _station_id(
            quote.address, quote.suburb, latitude, longitude
        )

    async def fetch(self, product: str, day: date) -> tuple[Quote, ...]:
        key = (product, day)
        if key not in self._inflight:
            task = asyncio.create_task(self._fetch(product, day))
            self._inflight[key] = task
            task.add_done_callback(lambda done: self._inflight.pop(key, None))
        return await asyncio.shield(self._inflight[key])

    async def async_close(self):
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _fetch(self, product: str, day: date) -> tuple[Quote, ...]:
        if product not in PRODUCTS:
            raise FeedError("Unsupported product")
        async with self._semaphore:
            for attempt in range(3):
                delay = 2**attempt + random.random()
                try:
                    async with self.session.get(
                        FEED_URL,
                        params={
                            "filter[product]": product,
                            "filter[day]": day.strftime("%d/%m/%Y"),
                        },
                        timeout=aiohttp.ClientTimeout(total=25),
                        headers={
                            "Accept": "application/vnd.api+json",
                            "User-Agent": "FuelWatch-WA-Plus/0.1 HomeAssistant",
                        },
                        allow_redirects=False,
                    ) as response:
                        if response.status == 429 or 500 <= response.status < 600:
                            retry = response.headers.get("Retry-After", "")
                            if re.fullmatch(r"\d{1,6}", retry):
                                if int(retry) > 60:
                                    raise FeedError("FuelWatch requested a longer retry interval")
                                delay = max(delay, int(retry))
                            raise aiohttp.ClientConnectionError(f"Temporary HTTP {response.status}")
                        if response.status != 200:
                            raise FeedError(f"FuelWatch HTTP {response.status}")
                        data = bytearray()
                        async for chunk in response.content.iter_chunked(65536):
                            data.extend(chunk)
                            if len(data) > MAX_RESPONSE:
                                raise FeedError("FuelWatch response exceeds size limit")
                        quotes = parse_feed(bytes(data), product, day)
                        aliases = {new: old for old, new in self._legacy_station_ids.items()}
                        return tuple(
                            replace(quote, station_id=aliases[quote.station_id])
                            if quote.station_id in aliases
                            else quote
                            for quote in quotes
                        )
                except (aiohttp.ClientError, TimeoutError) as err:
                    if attempt == 2:
                        raise FeedError(
                            "FuelWatch could not be reached after three attempts"
                        ) from err
                await asyncio.sleep(delay)
        raise FeedError("FuelWatch request failed")
