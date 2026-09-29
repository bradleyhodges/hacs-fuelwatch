"""Immutable source records, serialisable without losing price precision."""

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from .const import PERTH
from .station import StationDetails


@dataclass(frozen=True, slots=True)
class Quote:
    """One station/product price valid from 06:00 on its source day."""

    station_id: str
    product: str
    day: date
    price: Decimal
    name: str
    brand: str
    address: str
    suburb: str
    latitude: float
    longitude: float
    details: StationDetails = field(default_factory=StationDetails)

    @property
    def valid_from(self) -> datetime:
        return datetime.combine(self.day, time(6), PERTH)

    @property
    def valid_until(self) -> datetime:
        return self.valid_from + timedelta(days=1)

    def to_dict(self) -> dict:
        """Produce a version-independent cache record."""
        return asdict(self) | {
            "price": str(self.price),
            "day": self.day.isoformat(),
            "details": self.details.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: dict) -> Quote:
        return cls(
            **(
                value
                | {
                    "price": Decimal(value["price"]),
                    "day": date.fromisoformat(value["day"]),
                    "details": StationDetails.from_dict(value.get("details", {})),
                }
            )
        )


@dataclass(frozen=True, slots=True)
class FeedSnapshot:
    """A validated price period with original worker provenance, including empty publications."""

    quotes: tuple[Quote, ...]
    product: str
    source_date: date
    source: str
    fetched_at: datetime
    publication_status: str

    def metadata(self) -> dict:
        return {
            "product": self.product,
            "source_date": self.source_date.isoformat(),
            "source": self.source,
            "fetched_at": self.fetched_at.astimezone(PERTH).isoformat(),
            "publication_status": self.publication_status,
        }
