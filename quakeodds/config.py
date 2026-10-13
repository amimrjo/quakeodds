"""Application configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AppConfig:
    """All tunable settings in one place. Override some of them with env vars."""

    user_agent: str = "QuakeOdds/1.0 (educational earthquake probability tool)"
    http_timeout_s: float = 30.0

    # Hazard model defaults
    default_radius_km: int = 100
    min_radius_km: int = 25
    max_radius_km: int = 300
    catalog_years: int = 30
    catalog_min_magnitude: float = 3.0
    magnitude_thresholds: tuple[float, ...] = (4.0, 5.0, 6.0, 7.0)
    time_horizons_years: tuple[int, ...] = (1, 10, 30)
    recent_days: int = 30

    # Caching
    autocomplete_cache_ttl_s: int = 60 * 60 * 24
    hazard_cache_ttl_s: int = 60 * 10

    host: str = "127.0.0.1"
    port: int = 5000
    debug: bool = False

    @classmethod
    def from_env(cls) -> "AppConfig":
        return cls(
            user_agent=os.getenv("QUAKEODDS_USER_AGENT", cls.user_agent),
            catalog_years=int(os.getenv("QUAKEODDS_CATALOG_YEARS", cls.catalog_years)),
            catalog_min_magnitude=float(os.getenv("QUAKEODDS_CATALOG_MIN_MAG", cls.catalog_min_magnitude)),
            host=os.getenv("HOST", cls.host),
            port=int(os.getenv("PORT", cls.port)),
            debug=os.getenv("FLASK_DEBUG", "0") == "1",
        )
