"""The one shared format every site adapter must produce."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from .normalize import building_key

AVAILABLE = "available"
RENTED = "rented"  # the site still shows the page, but marks it as let
REMOVED = "removed"  # we stopped seeing it on the site


@dataclass
class Listing:
    source: str  # "edc", "savills", ...
    source_id: str  # the site's own id for the listing
    url: str
    title: str | None = None
    address: str | None = None
    zip_code: str | None = None
    city: str | None = None
    property_type: str = "other"  # normalized: office, retail, warehouse_industrial, ...
    size_m2_min: int | None = None
    size_m2_max: int | None = None
    yearly_rent_min: int | None = None  # DKK per year
    yearly_rent_max: int | None = None
    status: str = AVAILABLE
    extra: dict = field(default_factory=dict)  # site-specific fields we keep but don't normalize

    @property
    def rent_per_m2(self) -> float | None:
        """Yearly rent per m², only when both values describe the same unit."""
        if self.yearly_rent_min and self.size_m2_min and self.size_m2_min == self.size_m2_max:
            return round(self.yearly_rent_min / self.size_m2_min, 1)
        return None

    @property
    def building_key(self) -> str | None:
        return building_key(self.address, self.zip_code)

    def content_hash(self) -> str:
        """Changes when anything a user would care about changes (price, size, status...)."""
        data = asdict(self)
        payload = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["rent_per_m2"] = self.rent_per_m2
        data["building_key"] = self.building_key
        return data
