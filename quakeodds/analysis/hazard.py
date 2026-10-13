"""Turns raw catalog data into a location's earthquake probability report."""
from __future__ import annotations

import math
from dataclasses import replace
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone

from ..models import Earthquake, HazardReport, Location, ProbabilityEstimate
from ..services.usgs import USGSEarthquakeClient
from ..utils.cache import TTLCache
from .gutenberg_richter import GutenbergRichterModel


class ProbabilityModel(ABC):
    @abstractmethod
    def probability(self, annual_rate: float, years: float) -> float:
        ...


class PoissonProbabilityModel(ProbabilityModel):
    """Earthquakes treated as independent random events in time:
    P(at least one in T years) = 1 - exp(-rate * T)."""

    def probability(self, annual_rate: float, years: float) -> float:
        return 1.0 - math.exp(-annual_rate * years)


class RiskClassifier:
    """Plain-language level based on P(M5+ within 30 years)."""

    LEVELS = (
        (0.70, "Very high", "A damaging-size earthquake (M5+) nearby is more likely than not within 30 years."),
        (0.35, "High", "M5+ earthquakes nearby are a realistic possibility within 30 years."),
        (0.10, "Moderate", "M5+ earthquakes nearby are uncommon but do happen on a decades scale."),
        (0.02, "Low", "M5+ earthquakes nearby are rare; small tremors are possible."),
        (0.00, "Very low", "Few or no significant earthquakes have been recorded nearby."),
    )

    def __init__(self, reference_magnitude: float = 5.0, reference_years: int = 30) -> None:
        self.reference_magnitude = reference_magnitude
        self.reference_years = reference_years

    def classify(self, probability: float) -> tuple[str, str]:
        for threshold, level, summary in self.LEVELS:
            if probability >= threshold:
                return level, summary
        return self.LEVELS[-1][1], self.LEVELS[-1][2]


class SeismicHazardAnalyzer:
    def __init__(
        self,
        client: USGSEarthquakeClient,
        catalog_years: int = 30,
        catalog_min_magnitude: float = 3.0,
        magnitude_thresholds: tuple[float, ...] = (4.0, 5.0, 6.0, 7.0),
        time_horizons_years: tuple[int, ...] = (1, 10, 30),
        recent_days: int = 30,
        probability_model: ProbabilityModel | None = None,
        risk_classifier: RiskClassifier | None = None,
        cache: TTLCache | None = None,
    ) -> None:
        self._client = client
        self.catalog_years = catalog_years
        self.catalog_min_magnitude = catalog_min_magnitude
        self.thresholds = magnitude_thresholds
        self.horizons = time_horizons_years
        self.recent_days = recent_days
        self._prob = probability_model or PoissonProbabilityModel()
        self._risk = risk_classifier or RiskClassifier()
        self._cache = cache or TTLCache(600)

    def analyze(self, location: Location, radius_km: int) -> HazardReport:
        key = (round(location.latitude, 3), round(location.longitude, 3), radius_km)
        cached = self._cache.get(key)
        if cached is not None:
            return replace(cached, location=location)  # same spot, maybe a different label
        report = self._build_report(location, radius_km)
        self._cache.set(key, report)
        return report

    # ---- internals ----------------------------------------------------
    def _build_report(self, location: Location, radius_km: int) -> HazardReport:
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=round(365.25 * self.catalog_years))
        catalog = self._client.fetch_events(
            location.latitude, location.longitude, radius_km,
            start=start, min_magnitude=self.catalog_min_magnitude,
        )
        recent = self._client.fetch_events(
            location.latitude, location.longitude, radius_km,
            start=now - timedelta(days=self.recent_days), limit=200,
        )

        truncated = len(catalog) >= USGSEarthquakeClient.MAX_LIMIT
        effective_years = float(self.catalog_years)
        if truncated and catalog:
            # Results come newest-first, so the oldest returned event marks
            # how far back the data actually reaches.
            effective_years = max((now - catalog[-1].time).days / 365.25, 0.1)

        gr = GutenbergRichterModel((q.magnitude for q in catalog), effective_years)
        estimates = [self._estimate(gr, m, catalog) for m in self.thresholds]

        ref = gr.annual_rate(self._risk.reference_magnitude)
        level, summary = self._risk.classify(self._prob.probability(ref, self._risk.reference_years))

        return HazardReport(
            location=location,
            radius_km=radius_km,
            requested_years=self.catalog_years,
            effective_years=effective_years,
            catalog_size=len(catalog),
            catalog_truncated=truncated,
            model=gr.summary(),
            estimates=estimates,
            risk_level=level,
            risk_summary=summary,
            recent_events=recent[:100],
            largest_event=max(catalog, key=lambda q: q.magnitude, default=None),
            generated_at=now,
            notes=self._notes(gr, catalog, truncated, effective_years, radius_km),
        )

    def _estimate(self, gr: GutenbergRichterModel, magnitude: float,
                  catalog: list[Earthquake]) -> ProbabilityEstimate:
        rate = gr.annual_rate(magnitude)
        return ProbabilityEstimate(
            magnitude=magnitude,
            annual_rate=rate,
            return_period_years=(1 / rate) if rate > 0 else None,
            probabilities={t: self._prob.probability(rate, t) for t in self.horizons},
            observed_count=sum(1 for q in catalog if q.magnitude >= magnitude),
        )

    def _notes(self, gr: GutenbergRichterModel, catalog: list[Earthquake],
               truncated: bool, years: float, radius_km: int) -> list[str]:
        notes: list[str] = []
        if not catalog:
            notes.append(
                f"No earthquakes of M{self.catalog_min_magnitude:g}+ were recorded within "
                f"{radius_km} km in the last {self.catalog_years} years, so all chances show as zero. "
                "That points to low activity, but it is not a guarantee."
            )
        elif not gr.fitted_from_data:
            notes.append(
                f"Only {gr.events_above_mc} usable events were recorded, too few to measure the local "
                "magnitude pattern, so the global average (b = 1.0) is used. Treat these numbers as rough."
            )
        if gr.mc is not None and gr.mc >= 4.0:
            notes.append(
                f"Monitoring here reliably captures quakes from about M{gr.mc:.1f} up. "
                "Smaller ones are under-reported, which is common outside dense seismic networks."
            )
        if truncated:
            notes.append(f"This area is very active: the data covers the last {years:.1f} years "
                         "(the USGS returns at most 20,000 events per request).")
        return notes
