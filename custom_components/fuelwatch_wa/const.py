"""Shared constants; source dates always use Western Australian time."""

from zoneinfo import ZoneInfo

DOMAIN = "fuelwatch_wa"
NAME = "FuelWatch WA"
VERSION = "0.1.0"
PERTH = ZoneInfo("Australia/Perth")
FEED_URL = "https://fuelwatch.oss.bhodges.me/v1"
FEED_SOURCE = "fuelwatch.wa.gov.au"
PRODUCTS = {
    "1": "Unleaded 91",
    "2": "Premium unleaded 95",
    "4": "Diesel",
    "5": "LPG",
    "6": "Premium unleaded 98",
    "10": "E85",
    "11": "Premium diesel",
}
MAX_RESPONSE = 4 * 1024 * 1024
MAX_STATIONS = 5000
PANEL_URL = "fuelwatch-wa"
