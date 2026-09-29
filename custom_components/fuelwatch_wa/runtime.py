"""Single presentation contract shared by entities, actions and frontend."""

from datetime import datetime, timedelta
from decimal import Decimal

from homeassistant.util import dt as dt_util

from .const import FEED_SOURCE, FEED_URL
from .engine import (
    active_day,
    compare_costs,
    decimal,
    fuel_litres,
    money,
    purchase_litres,
    quote_row,
    rank_quotes,
)


def public(value):
    """Serialise decimals at the external boundary, preserving None values."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [public(v) for v in value]
    return value


def state_number(hass, settings, key, units=()):
    """Read a configured sensor or literal; validate sensor units if specified."""
    entity = settings.get(f"{key}_entity")
    if not entity:
        return settings.get(key)
    state = hass.states.get(entity)
    if state is None or state.state in ("unknown", "unavailable"):
        raise ValueError(f"{key.replace('_', ' ').capitalize()} sensor is unavailable")
    if units and state.attributes.get("unit_of_measurement") not in units:
        raise ValueError(f"{key.replace('_', ' ').capitalize()} sensor has incompatible units")
    return float(decimal(state.state, maximum=100000))


def vehicle_view(hass, settings, manual_value=None):
    """Interpret telemetry without writing to a vehicle's existing sensors."""
    result = {
        "volume": None,
        "remaining": None,
        "capacity": settings.get("capacity"),
        "consumption": None,
        "error": None,
        "purchase_mode": settings.get("purchase_mode", "full"),
    }
    try:
        value = manual_value if manual_value is not None else settings.get("manual_level", 50)
        if entity := settings.get("fuel_entity"):
            state = hass.states.get(entity)
            if state is None:
                raise ValueError("Fuel-level sensor is unavailable")
            value = state.state
        volume = purchase_litres(settings, value)
        result["volume"] = float(volume)
        if settings.get("purchase_mode") == "current":
            result["remaining"] = float(
                fuel_litres(value, settings["fuel_format"], settings["capacity"])
            )
    except (ValueError, KeyError) as err:
        result["error"] = str(err)
    try:
        result["consumption"] = state_number(
            hass, settings, "consumption", ("L/100km", "L/100 km", "l/100km", "l/100 km")
        )
    except ValueError as err:
        result["consumption_error"] = str(err)
    return result


def _discounts(hass, entry):
    rules = []
    for sub in entry.subentries.values():
        if sub.subentry_type != "discount":
            continue
        rule = dict(sub.data)
        if entity := rule.get("eligibility_entity"):
            rule["enabled"] = bool(rule.get("enabled", True) and hass.states.is_state(entity, "on"))
        rules.append(rule)
    return rules


def _attach_costs(rows, vehicle, price_key):
    for row in rows:
        row["fill_cost"] = None
        row["full_tank_cost"] = None
        if vehicle and vehicle["volume"] is not None:
            row["fill_cost"] = float(
                money(decimal(vehicle["volume"]) * decimal(row[price_key]) / 100)
            )
        if vehicle and vehicle.get("capacity"):
            row["full_tank_cost"] = float(
                money(decimal(vehicle["capacity"]) * decimal(row[price_key]) / 100)
            )


def build_view(
    hass, entry, subentry_id, now: datetime | None = None, *, period="current", overrides=None
):
    """Build one named view. No network or disk I/O is performed here."""
    now = now or dt_util.utcnow()
    coordinator = entry.runtime_data
    if subentry_id not in entry.subentries:
        raise ValueError("Tracking entry no longer exists")
    sub = entry.subentries[subentry_id]
    settings = dict(sub.data)
    output = {
        "id": subentry_id,
        "title": sub.title,
        "type": sub.subentry_type,
        "period": period,
        "groups": {},
        "error": None,
        "vehicle": None,
        "schema_version": 1,
        "source_errors": dict(coordinator.errors),
        "source": FEED_SOURCE,
        "api_url": FEED_URL,
    }
    discounts = _discounts(hass, entry)
    vehicle_id = settings.get("vehicle_id")
    vehicle = None
    if vehicle_id:
        if (
            vehicle_id not in entry.subentries
            or entry.subentries[vehicle_id].subentry_type != "vehicle"
        ):
            output["error"] = "Referenced vehicle is missing"
            return output
        vehicle = vehicle_view(
            hass,
            entry.subentries[vehicle_id].data,
            getattr(coordinator, "manual_levels", {}).get(vehicle_id),
        )
        output["vehicle"] = vehicle | {"title": entry.subentries[vehicle_id].title}
    key = "effective_price" if settings.get("price_basis") == "effective" else "price"
    try:
        if sub.subentry_type == "vehicle":
            output["vehicle"] = vehicle_view(
                hass, settings, getattr(coordinator, "manual_levels", {}).get(subentry_id)
            )
        elif sub.subentry_type == "search":
            if zone_id := settings.get("zone"):
                zone = hass.states.get(zone_id)
                if zone is None:
                    raise ValueError("Search zone is unavailable")
                settings.update(
                    latitude=zone.attributes["latitude"], longitude=zone.attributes["longitude"]
                )
            output["centre"] = {
                "latitude": settings["latitude"],
                "longitude": settings["longitude"],
                "radius": settings["radius"],
            }
            output["groups"] = rank_quotes(coordinator.quotes, settings, now, discounts, period)
        elif sub.subentry_type == "station":
            target = active_day(now) + timedelta(days=period == "next")
            output["groups"] = {p: [] for p in settings["products"]}
            for quote in coordinator.quotes:
                if (
                    quote.station_id == settings["station_id"]
                    and quote.product in settings["products"]
                    and quote.day == target
                ):
                    output["groups"][quote.product] = [
                        quote_row(quote, discounts, now) | {"rank": 1}
                    ]
        elif sub.subentry_type == "comparison":
            target = active_day(now) + timedelta(days=period == "next")
            rows = {
                q.station_id: quote_row(q, discounts, now)
                for q in coordinator.quotes
                if q.product == settings["product"] and q.day == target
            }
            alternative = settings.get("station_b")
            if settings.get("search_id"):
                search = build_view(hass, entry, settings["search_id"], now, period=period)
                matching = search["groups"].get(settings["product"], [])
                if not matching:
                    raise ValueError("No comparable alternative in the selected search")
                alternative = matching[0]["station_id"]
            if settings["station_a"] not in rows or alternative not in rows:
                raise ValueError("A station has no valid quote for this period")
            if vehicle is None or vehicle["volume"] is None:
                raise ValueError("Vehicle purchase volume is unavailable")
            a, b = rows[settings["station_a"]], rows[alternative]
            travel = {}
            for field, units in (("extra_km", ("km",)), ("extra_minutes", ("min",))):
                try:
                    travel[field] = state_number(hass, settings, field, units)
                except ValueError as err:
                    travel[field] = None
                    output["travel_error"] = str(err)
            travel.update(overrides or {})
            comparison = compare_costs(
                vehicle["volume"],
                a[key],
                b[key],
                travel["extra_km"],
                vehicle["consumption"],
                travel["extra_minutes"],
                settings.get("value_of_time", 0),
            )
            output.update(station_a=a, station_b=b, comparison=public(comparison), travel=travel)
            threshold = decimal(settings.get("minimum_saving", 1))
            output["worth_it"] = (
                comparison["net_saving"] >= threshold
                if comparison["net_saving"] is not None
                else None
            )
        for rows in output["groups"].values():
            _attach_costs(rows, vehicle, key)
            for row in rows:
                bucket = f"{row['product']}/{row['date']}"
                row["last_fetched"] = coordinator.fetched.get(bucket)
                row["cached"] = bucket in coordinator.errors
                row["source"] = FEED_SOURCE
                row["publication_status"] = (
                    getattr(coordinator, "source_metadata", {})
                    .get(bucket, {})
                    .get("publication_status")
                )
        output["price_basis"] = settings.get("price_basis", "advertised")
    except (ValueError, KeyError) as err:
        output["error"] = str(err)
    return output
