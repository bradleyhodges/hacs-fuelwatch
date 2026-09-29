"""Behavioural tests for money, input interpretation and comparable rankings."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pytest

from custom_components.fuelwatch_wa.engine import (
    active_day,
    compare_costs,
    fuel_litres,
    purchase_litres,
    rank_quotes,
)
from custom_components.fuelwatch_wa.models import FeedSnapshot, Quote

DAY = date(2026, 9, 29)
NOW = datetime(2026, 9, 29, 12, tzinfo=ZoneInfo("Australia/Perth"))


def snapshot(*rows):
    """A typed API snapshot for coordinator tests that isolate downstream behavior."""
    return FeedSnapshot(tuple(rows), "1", DAY, "fuelwatch.wa.gov.au", NOW, "available")


def quote(sid="one", price="185.9", **kwargs):
    defaults = dict(
        station_id=sid,
        product="1",
        day=DAY,
        price=D(price),
        name=sid,
        brand="Example",
        address="1 Test Road",
        suburb="PERTH",
        latitude=-31.95,
        longitude=115.86,
    )
    return Quote(**(defaults | kwargs))


@pytest.mark.parametrize(
    "value,mode,expected",
    [
        ("25", "percentage", "12.5"),
        ("0.25", "proportion", "12.5"),
        ("1/4", "fraction", "12.5"),
        ("¼", "fraction", "12.5"),
        ("12.5", "litres", "12.5"),
        ("0.25", "percentage", ".125"),
        ("0", "percentage", "0"),
        ("100", "percentage", "50"),
    ],
)
def test_explicit_fuel_units(value, mode, expected):
    assert fuel_litres(value, mode, 50) == D(expected)


@pytest.mark.parametrize(
    "value,mode",
    [
        ("unknown", "litres"),
        ("unavailable", "percentage"),
        ("NaN", "litres"),
        ("inf", "proportion"),
        ("1/0", "fraction"),
        ("101", "percentage"),
        ("-1", "litres"),
        ("51", "litres"),
        ("__import__('os')", "fraction"),
        ("2/1", "fraction"),
        (True, "percentage"),
    ],
)
def test_bad_fuel_level_is_never_empty(value, mode):
    with pytest.raises(ValueError):
        fuel_litres(value, mode, 50)


def test_purchase_modes_and_targets():
    assert purchase_litres({"purchase_mode": "full", "capacity": 50}) == D(50)
    assert purchase_litres({"purchase_mode": "fixed", "volume": 30}) == D(30)
    settings = {
        "purchase_mode": "current",
        "capacity": 50,
        "fuel_format": "fraction",
        "target": 100,
    }
    assert purchase_litres(settings, "1/4") == D("37.5")
    assert purchase_litres(settings | {"target": 20}, "1/4") == 0
    with pytest.raises(ValueError):
        purchase_litres(settings, "unavailable")


def test_approved_savings_example():
    result = compare_costs(37.5, 185.9, 177.9, 10, 7)
    assert result["gross_saving"] == D("3.00")
    assert result["travel_cost"] == D("1.30")
    assert result["net_saving"] == D("1.70")
    assert result["break_even_litres"] == D("16.27")
    assert compare_costs(37.5, 185.9, 177.9, 10, 7, 15, 20)["net_saving"] == D("-3.30")


def test_missing_travel_is_not_free_travel():
    result = compare_costs(40, 180, 170, None, 7)
    assert result["gross_saving"] == D(4)
    assert result["net_saving"] is None
    assert compare_costs(40, 180, 170, 0, 7)["net_saving"] == D(4)
    assert compare_costs(40, 170, 180, 10, 7)["break_even_litres"] is None


def test_perth_six_am_not_midnight():
    assert active_day(datetime(2026, 9, 29, 5, 59, tzinfo=ZoneInfo("Australia/Perth"))) == date(
        2026, 9, 28
    )
    assert active_day(datetime(2026, 9, 28, 22, tzinfo=ZoneInfo("UTC"))) == DAY


def test_rank_filters_and_product_separation():
    data = [
        quote("a", "180"),
        quote("b", "170", brand="Excluded"),
        quote("c", "100", latitude=-33),
        quote("d", "160", product="6"),
        quote("expired", "90", day=date(2026, 9, 28)),
    ]
    config = {
        "latitude": -31.95,
        "longitude": 115.86,
        "radius": 10,
        "products": ["1", "6"],
        "vendor_mode": "exclude",
        "vendors": ["Excluded"],
        "limit": 5,
    }
    result = rank_quotes(data, config, NOW)
    assert [r["station_id"] for r in result["1"]] == ["a"]
    assert [r["station_id"] for r in result["6"]] == ["d"]
    assert rank_quotes(data, config | {"vendor_mode": "include", "vendors": []}, NOW) == {
        "1": [],
        "6": [],
    }


def test_ties_combined_products_and_discount():
    data = [quote("b", "180"), quote("a", "180"), quote("a", "175", product="6")]
    config = {
        "latitude": -31.95,
        "longitude": 115.86,
        "radius": 10,
        "products": ["1", "6"],
        "limit": 5,
    }
    assert rank_quotes(data, config, NOW)["1"][0]["station_id"] == "a"
    combined = rank_quotes(data, config | {"combine": True}, NOW)["combined"]
    assert [(r["station_id"], r["product"]) for r in combined] == [("a", "6"), ("b", "1")]
    discounts = [{"station_id": "b", "amount": 10, "enabled": True, "expires": "2026-09-30"}]
    assert (
        rank_quotes(data, config | {"price_basis": "effective"}, NOW, discounts)["1"][0][
            "station_id"
        ]
        == "b"
    )
    expired = [discounts[0] | {"expires": "2026-09-28"}]
    assert (
        rank_quotes(data, config | {"price_basis": "effective"}, NOW, expired)["1"][0]["station_id"]
        == "a"
    )


def test_future_quotes_are_separate():
    data = [quote(), replace(quote(), day=date(2026, 9, 30), price=D(170))]
    config = {"latitude": -31.95, "longitude": 115.86, "radius": 10, "products": ["1"], "limit": 1}
    assert rank_quotes(data, config, NOW)["1"][0]["price"] == 185.9
    assert rank_quotes(data, config, NOW, period="next")["1"][0]["price"] == 170


def test_favourite_group_only_includes_selected_stations():
    config = {
        "latitude": -31.95,
        "longitude": 115.86,
        "radius": 10,
        "products": ["1"],
        "only_stations": ["a"],
        "limit": 5,
    }
    assert [
        r["station_id"] for r in rank_quotes([quote("a"), quote("b", "170")], config, NOW)["1"]
    ] == ["a"]


def test_discount_cannot_make_negative_fuel_price():
    cfg = {"latitude": -31.95, "longitude": 115.86, "radius": 10, "products": ["1"], "limit": 1}
    rows = rank_quotes([quote("a", "5")], cfg, NOW, [{"station_id": "a", "amount": 10}])["1"]
    assert rows[0]["effective_price"] == 0
