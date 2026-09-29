"""Pure, unit-explicit fuel calculations and deterministic station searches."""

from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from fractions import Fraction
from math import asin, cos, radians, sin, sqrt

from .const import PERTH, PRODUCTS
from .models import Quote

CENT = Decimal("0.01")


def decimal(value, minimum=0, maximum=None) -> Decimal:
    """Parse a finite bounded number; booleans are never numeric inputs."""
    if isinstance(value, bool) or value is None or len(str(value)) > 64:
        raise ValueError("A finite number is required")
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as err:
        raise ValueError("A finite number is required") from err
    if (
        not result.is_finite()
        or result < Decimal(str(minimum))
        or (maximum is not None and result > Decimal(str(maximum)))
    ):
        raise ValueError("Number is outside its permitted range")
    return result


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def active_day(now: datetime) -> date:
    """Return the price period currently in effect, not the calendar date."""
    if now.tzinfo is None:
        raise ValueError("An aware timestamp is required")
    return (now.astimezone(PERTH) - timedelta(hours=6)).date()


def fuel_litres(value, mode: str, capacity) -> Decimal:
    """Convert an explicitly selected fuel-level representation to litres."""
    cap = decimal(capacity, minimum=Decimal("0.001"), maximum=100000)
    if mode == "fraction":
        raw = str(value).strip()
        raw = {"¼": "1/4", "½": "1/2", "¾": "3/4", "⅓": "1/3", "⅔": "2/3"}.get(raw, raw)
        if len(raw) > 32 or raw.count("/") != 1:
            raise ValueError("Use a fraction such as 1/4")
        try:
            fraction = Fraction(raw)
            level = Decimal(fraction.numerator) / Decimal(fraction.denominator)
        except (ValueError, ZeroDivisionError) as err:
            raise ValueError("Invalid fraction") from err
        result = decimal(level, maximum=1) * cap
    elif mode == "percentage":
        result = decimal(value, maximum=100) * cap / 100
    elif mode == "proportion":
        result = decimal(value, maximum=1) * cap
    elif mode == "litres":
        result = decimal(value, maximum=cap)
    else:
        raise ValueError("Unknown fuel-level format")
    return result


def purchase_litres(vehicle: dict, value=None) -> Decimal:
    """Return the planned quantity; missing telemetry never means empty."""
    mode = vehicle.get("purchase_mode", "full")
    if mode == "fixed":
        return decimal(vehicle["volume"], maximum=100000)
    cap = decimal(vehicle["capacity"], minimum=Decimal("0.001"), maximum=100000)
    if mode == "full":
        return cap
    if mode != "current":
        raise ValueError("Unknown purchase mode")
    target = cap * decimal(vehicle.get("target", 100), maximum=100) / 100
    return max(target - fuel_litres(value, vehicle["fuel_format"], cap), Decimal(0))


def compare_costs(
    volume, price_a, price_b, extra_km=None, consumption=None, extra_minutes=None, value_of_time=0
) -> dict:
    """Compare the same planned volume; prices in c/L, outputs in AUD.

    Travel inputs are incremental complete-journey distances/times. This is an
    economic estimate, not a prediction of the receipt after driving to a pump.
    """
    q = decimal(volume, maximum=100000)
    a, b = decimal(price_a, maximum=10000) / 100, decimal(price_b, maximum=10000) / 100
    gross = q * (a - b)
    result = {
        "volume": q,
        "cost_a": money(q * a),
        "cost_b": money(q * b),
        "gross_saving": money(gross),
        "travel_cost": None,
        "time_cost": None,
        "net_saving": None,
        "break_even_litres": None,
    }
    if extra_km is None:
        return result
    distance = decimal(extra_km, maximum=10000)
    if distance > 0 and consumption is None:
        return result
    travel = distance * decimal(consumption or 0, maximum=1000) / 100 * a
    hourly = decimal(value_of_time, maximum=10000)
    if hourly and extra_minutes is None:
        return result
    time_cost = decimal(extra_minutes or 0, maximum=100000) / 60 * hourly
    result.update(
        travel_cost=money(travel),
        time_cost=money(time_cost),
        net_saving=money(gross - travel - time_cost),
        break_even_litres=money((travel + time_cost) / (a - b)) if a > b else None,
    )
    return result


def distance_km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance; never used as road driving distance."""
    lat1, lat2, dlat, dlon = (
        radians(lat1),
        radians(lat2),
        radians(lat2 - lat1),
        radians(lon2 - lon1),
    )
    haversine = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 6371.0088 * 2 * asin(sqrt(min(1, max(0, haversine))))


def effective_price(quote: Quote, discounts: list[dict], on_day: date) -> Decimal:
    """Choose one eligible rule; station rules take precedence over brand rules."""
    station_rules, vendor_rules = [], []
    for rule in discounts:
        if not rule.get("enabled", True):
            continue
        if rule.get("expires") and date.fromisoformat(rule["expires"]) < on_day:
            continue
        if rule.get("products") and quote.product not in rule["products"]:
            continue
        if rule.get("station_id"):
            if rule["station_id"] == quote.station_id:
                station_rules.append(min(quote.price, decimal(rule["amount"], maximum=200)))
        elif rule.get("vendor", "").casefold() == quote.brand.casefold():
            vendor_rules.append(min(quote.price, decimal(rule["amount"], maximum=200)))
    return quote.price - max(station_rules or vendor_rules or [Decimal(0)])


def quote_row(quote: Quote, discounts: list[dict], now: datetime) -> dict:
    """Common, bounded public station contract for sensors, actions and UI."""
    effective = effective_price(quote, discounts, max(quote.day, now.astimezone(PERTH).date()))
    return {
        "station_id": quote.station_id,
        "product": quote.product,
        "product_name": PRODUCTS[quote.product],
        "name": quote.name,
        "vendor": quote.brand,
        "address": quote.address,
        "suburb": quote.suburb,
        "latitude": quote.latitude,
        "longitude": quote.longitude,
        "price": float(quote.price),
        "effective_price": float(effective),
        "discount": float(quote.price - effective),
        "date": quote.day.isoformat(),
        "valid_from": quote.valid_from.isoformat(),
        "valid_until": quote.valid_until.isoformat(),
        **quote.details.to_dict(),
    }


def rank_quotes(quotes, settings: dict, now: datetime, discounts=None, period="current") -> dict:
    """Filter by radius and brand, then rank each fuel independently by default."""
    discounts = discounts or []
    products = settings["products"]
    if not products or any(p not in PRODUCTS for p in products):
        raise ValueError("Select at least one supported fuel product")
    lat = float(decimal(settings["latitude"], minimum=-90, maximum=90))
    lon = float(decimal(settings["longitude"], minimum=-180, maximum=180))
    radius = float(decimal(settings.get("radius", 10), minimum=Decimal("0.1"), maximum=1000))
    limit = int(decimal(settings.get("limit", 5), minimum=1, maximum=20))
    if period not in ("current", "next"):
        raise ValueError("Unknown period")
    target = active_day(now) + timedelta(days=period == "next")
    vendors = {v.casefold() for v in settings.get("vendors", [])}
    mode = settings.get("vendor_mode", "all")
    groups = {"combined": []} if settings.get("combine") else {p: [] for p in products}
    for quote in quotes:
        if quote.product not in products or quote.day != target:
            continue
        if quote.station_id in settings.get("excluded_stations", []):
            continue
        if settings.get("only_stations") and quote.station_id not in settings["only_stations"]:
            continue
        if mode == "include" and quote.brand.casefold() not in vendors:
            continue
        if mode == "exclude" and quote.brand.casefold() in vendors:
            continue
        distance = distance_km(lat, lon, quote.latitude, quote.longitude)
        if distance > radius:
            continue
        row = quote_row(quote, discounts, now) | {
            "distance_km": distance,
            "distance_type": "straight_line",
        }
        groups["combined" if settings.get("combine") else quote.product].append(row)
    price_key = "effective_price" if settings.get("price_basis") == "effective" else "price"
    for key, rows in groups.items():
        rows.sort(key=lambda r: (r[price_key], r["distance_km"], r["station_id"], r["product"]))
        unique = {}
        for row in rows:
            unique.setdefault(row["station_id"], row)
        groups[key] = [
            row | {"rank": rank} for rank, row in enumerate(list(unique.values())[:limit], 1)
        ]
    return groups
