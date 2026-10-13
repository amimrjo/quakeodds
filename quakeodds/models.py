"""Plain data objects passed between the layers of the app."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Location:
    name: str
    latitude: float
    longitude: float
    detail: str = ""  # e.g. "California, United States"
    kind: str = ""    # e.g. "city", "street"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Earthquake:
    event_id: str
    magnitude: float
    place: str
    time: datetime
    latitude: float
    longitude: float
    depth_km: float
    url: str
    distance_km: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["time"] = self.time.isoformat()
        data["distance_km"] = round(self.distance_km, 1)
        return data


@dataclass(frozen=True)
class ProbabilityEstimate:
    """Chance of at least one quake >= `magnitude` within the search radius."""

    magnitude: float
    annual_rate: float
    return_period_years: float | None
    probabilities: dict[int, float]  # horizon (years) -> probability 0..1
    observed_count: int              # how many the catalog actually recorded

    def to_dict(self) -> dict[str, Any]:
        return {
            "magnitude": self.magnitude,
            "annual_rate": self.annual_rate,
            "return_period_years": self.return_period_years,
            "probabilities": {str(k): v for k, v in self.probabilities.items()},
            "observed_count": self.observed_count,
        }


@dataclass(frozen=True)
class ModelSummary:
    completeness_magnitude: float | None
    b_value: float
    b_uncertainty: float | None
    fitted_from_data: bool
    events_used: int
    annual_rate_above_mc: float


@dataclass
class HazardReport:
    location: Location
    radius_km: int
    requested_years: int
    effective_years: float
    catalog_size: int
    catalog_truncated: bool
    model: ModelSummary
    estimates: list[ProbabilityEstimate]
    risk_level: str
    risk_summary: str
    recent_events: list[Earthquake]
    largest_event: Earthquake | None
    generated_at: datetime
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": self.location.to_dict(),
            "radius_km": self.radius_km,
            "requested_years": self.requested_years,
            "effective_years": round(self.effective_years, 2),
            "catalog_size": self.catalog_size,
            "catalog_truncated": self.catalog_truncated,
            "model": asdict(self.model),
            "estimates": [e.to_dict() for e in self.estimates],
            "risk_level": self.risk_level,
            "risk_summary": self.risk_summary,
            "recent_events": [e.to_dict() for e in self.recent_events],
            "largest_event": self.largest_event.to_dict() if self.largest_event else None,
            "generated_at": self.generated_at.isoformat(),
            "notes": self.notes,
        }
