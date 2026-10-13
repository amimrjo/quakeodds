"""Gutenberg–Richter magnitude-frequency model.

The law says log10 N(>=M) = a - b*M: every step up in magnitude makes quakes
roughly 10x rarer (b ~ 1). We fit it to the local catalog so we can estimate
the rate of large, rare quakes from the many small ones actually recorded.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Iterable

from ..models import ModelSummary


class GutenbergRichterModel:
    BIN_WIDTH = 0.1
    MC_CORRECTION = 0.2          # standard correction for the max-curvature method
    B_MIN, B_MAX = 0.5, 1.8      # physically plausible range, guards against tiny samples
    GLOBAL_DEFAULT_B = 1.0

    def __init__(self, magnitudes: Iterable[float], observation_years: float,
                 min_events_for_fit: int = 30) -> None:
        self._mags = [self._bin(m) for m in magnitudes]
        self._years = max(observation_years, 1e-6)
        self._min_events = min_events_for_fit

        self.mc: float | None = None
        self.b_value: float = self.GLOBAL_DEFAULT_B
        self.b_uncertainty: float | None = None
        self.fitted_from_data = False
        self.events_above_mc = 0
        self.annual_rate_above_mc = 0.0
        self._fit()

    # ---- fitting -------------------------------------------------------
    def _bin(self, m: float) -> float:
        return round(round(m / self.BIN_WIDTH) * self.BIN_WIDTH, 1)

    def _estimate_completeness(self) -> float:
        """Magnitude of completeness via maximum curvature: the most common
        magnitude bin, plus a correction. Below it the network misses quakes."""
        counts = Counter(self._mags)
        mode = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0]
        return round(mode + self.MC_CORRECTION, 1)

    def _fit(self) -> None:
        if not self._mags:
            return
        self.mc = self._estimate_completeness()
        above = [m for m in self._mags if m >= self.mc - 1e-9]
        if not above:  # very sparse catalogs: fall back to the smallest recorded bin
            self.mc = min(self._mags)
            above = list(self._mags)

        n = len(above)
        self.events_above_mc = n
        self.annual_rate_above_mc = n / self._years
        if n < self._min_events:
            return

        mean = sum(above) / n
        denom = mean - (self.mc - self.BIN_WIDTH / 2)  # Aki–Utsu estimator
        if denom <= 0:
            return
        b = math.log10(math.e) / denom
        variance = sum((m - mean) ** 2 for m in above) / (n * (n - 1))
        self.b_uncertainty = 2.3 * b * b * math.sqrt(variance)  # Shi & Bolt (1982)
        self.b_value = min(max(b, self.B_MIN), self.B_MAX)
        self.fitted_from_data = True

    # ---- predictions ---------------------------------------------------
    def annual_rate(self, magnitude: float) -> float:
        """Expected number of quakes per year with magnitude >= `magnitude`."""
        if self.mc is None:
            return 0.0
        return self.annual_rate_above_mc * 10 ** (-self.b_value * (magnitude - self.mc))

    def summary(self) -> ModelSummary:
        return ModelSummary(
            completeness_magnitude=self.mc,
            b_value=round(self.b_value, 3),
            b_uncertainty=round(self.b_uncertainty, 3) if self.b_uncertainty else None,
            fitted_from_data=self.fitted_from_data,
            events_used=self.events_above_mc,
            annual_rate_above_mc=round(self.annual_rate_above_mc, 4),
        )
