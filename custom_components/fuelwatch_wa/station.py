"""Validated, immutable station details shared by API parsing, storage and presentation."""

import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from .const import PERTH

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def text(value: object, maximum: int = 500) -> str:
    """Validate bounded text without accepting objects or silently coercing values."""
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError("Invalid station text")
    return value


def texts(value: object, maximum: int = 64) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise ValueError("Invalid station list")
    return tuple(text(item, 1000) for item in value)


def https_url(value: object) -> str:
    """Reject executable schemes and credential-bearing URLs before exposing attribution links."""
    result = text(value, 2048)
    url = urlsplit(result)
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ValueError("Invalid attribution URL")
    return result


@dataclass(frozen=True, slots=True)
class Enrichment:
    """Provider provenance travels with every field that may require attribution."""

    provider: str
    place_id: str
    fetched_at: str
    stale: bool
    fields: tuple[str, ...]
    google_maps_uri: str
    attributions: tuple[tuple[str, str], ...]

    @classmethod
    def from_api(cls, value: dict) -> Enrichment:
        if not isinstance(value, dict) or value.get("provider") != "Google Maps":
            raise ValueError("Invalid enrichment provider")
        if type(value.get("stale")) is not bool:
            raise ValueError("Invalid enrichment freshness")
        fetched = datetime.fromisoformat(text(value.get("fetchedAt"), 64))
        if fetched.tzinfo is None:
            raise ValueError("Missing enrichment timezone")
        attributions = value.get("attributions", [])
        if not isinstance(attributions, list) or len(attributions) > 20:
            raise ValueError("Invalid enrichment attribution")
        return cls(
            "Google Maps",
            text(value.get("placeId")),
            fetched.astimezone(PERTH).isoformat(),
            value["stale"],
            texts(value.get("fields"), 20),
            https_url(value.get("googleMapsUri")),
            tuple((text(item["displayName"]), https_url(item["uri"])) for item in attributions),
        )

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "place_id": self.place_id,
            "fetched_at": self.fetched_at,
            "stale": self.stale,
            "fields": list(self.fields),
            "google_maps_uri": self.google_maps_uri,
            "attributions": [{"display_name": name, "uri": uri} for name, uri in self.attributions],
        }


@dataclass(frozen=True, slots=True)
class StationDetails:
    """Optional metadata never participates in station identity or fuel-price calculations."""

    trading_name: str | None = None
    state: str = "WA"
    postcode: str | None = None
    phone: str | None = None
    is_24_hours: bool | None = None
    site_features: tuple[str, ...] = ()
    open_hours: tuple[tuple[str, str], ...] = ()
    restrictions: tuple[str, ...] | None = None
    source_notes: tuple[tuple[str, str | tuple[str, ...]], ...] = ()
    enrichment: Enrichment | None = None

    @classmethod
    def from_api(cls, item: dict) -> StationDetails:
        """Map camelCase JSON:API attributes to stable Home Assistant snake_case fields."""
        address = item.get("address", {})
        state = address.get("state", "WA")
        postcode, phone = address.get("postcode"), item.get("phone")
        if state != "WA" or (postcode is not None and not re.fullmatch(r"\d{4}", text(postcode))):
            raise ValueError("Invalid station address")
        if phone is not None and not re.fullmatch(r"\+[1-9]\d{6,14}", text(phone)):
            raise ValueError("Invalid E.164 phone")
        is_24_hours = item.get("is24Hours")
        if is_24_hours is not None and type(is_24_hours) is not bool:
            raise ValueError("Invalid opening indicator")
        hours = item.get("openHours", {})
        if not isinstance(hours, dict) or any(day not in WEEKDAYS for day in hours):
            raise ValueError("Invalid opening days")
        notes = item.get("sourceNotes", {})
        if not isinstance(notes, dict):
            raise ValueError("Invalid source notes")
        source_notes = []
        for original, public in (
            ("features", "features"),
            ("restrictions", "restrictions"),
            ("openHours", "open_hours"),
            ("phone", "phone"),
        ):
            if original in notes:
                value = (
                    texts(notes[original])
                    if original in ("features", "restrictions")
                    else text(notes[original], 10000)
                )
                source_notes.append((public, value))
        return cls(
            text(item["tradingName"]) if item.get("tradingName") is not None else None,
            state,
            postcode,
            phone,
            is_24_hours,
            texts(item.get("siteFeatures", [])),
            tuple((day, text(hours[day], 150)) for day in WEEKDAYS if day in hours),
            texts(item["restrictions"]) if item.get("restrictions") is not None else None,
            tuple(source_notes),
            Enrichment.from_api(item["enrichment"]) if item.get("enrichment") is not None else None,
        )

    def to_dict(self) -> dict:
        """Use the same JSON-safe shape for persistent storage and entity/websocket attributes."""
        return {
            "trading_name": self.trading_name,
            "state": self.state,
            "postcode": self.postcode,
            "phone": self.phone,
            "is_24_hours": self.is_24_hours,
            "site_features": list(self.site_features),
            "open_hours": dict(self.open_hours),
            "restrictions": list(self.restrictions) if self.restrictions is not None else None,
            "source_notes": {
                key: list(value) if isinstance(value, tuple) else value
                for key, value in self.source_notes
            },
            "enrichment": self.enrichment.to_dict() if self.enrichment else None,
        }

    @classmethod
    def from_dict(cls, value: dict) -> StationDetails:
        """Restore saved metadata through the same validation boundary; old records may omit it."""
        if not isinstance(value, dict):
            raise ValueError("Invalid saved station details")
        item = {
            remote: value.get(local)
            for local, remote in (
                ("trading_name", "tradingName"),
                ("phone", "phone"),
                ("is_24_hours", "is24Hours"),
                ("restrictions", "restrictions"),
            )
        }
        item.update(
            address={"state": value.get("state", "WA"), "postcode": value.get("postcode")},
            siteFeatures=value.get("site_features", []),
            openHours=value.get("open_hours", {}),
        )
        item["sourceNotes"] = {
            ("openHours" if key == "open_hours" else key): val
            for key, val in value.get("source_notes", {}).items()
        }
        if enrichment := value.get("enrichment"):
            item["enrichment"] = {
                "provider": enrichment["provider"],
                "placeId": enrichment["place_id"],
                "fetchedAt": enrichment["fetched_at"],
                "stale": enrichment["stale"],
                "fields": enrichment["fields"],
                "googleMapsUri": enrichment["google_maps_uri"],
                "attributions": [
                    {"displayName": entry["display_name"], "uri": entry["uri"]}
                    for entry in enrichment["attributions"]
                ],
            }
        return cls.from_api(item)
