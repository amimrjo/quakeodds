"""Place-name search for the autocomplete dropdown.

Photon (built on OpenStreetMap) is designed for search-as-you-type, so it is
the primary provider. Nominatim is used as a fallback if Photon fails.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

import requests

from ..models import Location
from ..utils.cache import TTLCache

log = logging.getLogger(__name__)


class GeocodingProvider(ABC):
    """Common interface for anything that turns text into locations."""

    name: str = "provider"

    def __init__(self, session: requests.Session, timeout_s: float) -> None:
        self._session = session
        self._timeout = timeout_s

    @abstractmethod
    def search(self, query: str, limit: int) -> list[Location]:
        ...

    @staticmethod
    def _join_unique(parts: list[str | None]) -> str:
        seen, out = set(), []
        for p in parts:
            if p and p not in seen:
                seen.add(p)
                out.append(p)
        return ", ".join(out)


class PhotonGeocoder(GeocodingProvider):
    name = "photon"
    URL = "https://photon.komoot.io/api/"

    def search(self, query: str, limit: int) -> list[Location]:
        resp = self._session.get(
            self.URL, params={"q": query, "limit": limit, "lang": "en"}, timeout=self._timeout
        )
        resp.raise_for_status()
        results: list[Location] = []
        for feature in resp.json().get("features", []):
            props = feature.get("properties", {})
            lon, lat = feature["geometry"]["coordinates"][:2]
            name = props.get("name") or props.get("city") or props.get("county")
            if not name:
                continue
            detail = self._join_unique([
                props.get("city") if props.get("city") != name else None,
                props.get("county") if props.get("type") in ("district", "locality") else None,
                props.get("state"),
                props.get("country"),
            ])
            results.append(Location(name=name, latitude=lat, longitude=lon,
                                    detail=detail, kind=props.get("type", "")))
        return results


class OpenMeteoGeocoder(GeocodingProvider):
    """Fast, keyless city/town search. Matches names as you type."""

    name = "open-meteo"
    URL = "https://geocoding-api.open-meteo.com/v1/search"

    def search(self, query: str, limit: int) -> list[Location]:
        resp = self._session.get(
            self.URL, params={"name": query, "count": limit, "language": "en", "format": "json"},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return [
            Location(name=r["name"], latitude=r["latitude"], longitude=r["longitude"],
                     detail=self._join_unique([r.get("admin1"), r.get("country")]),
                     kind="city" if r.get("feature_code", "").startswith("PPL") else "place")
            for r in resp.json().get("results", [])
        ]


class NominatimGeocoder(GeocodingProvider):
    name = "nominatim"
    URL = "https://nominatim.openstreetmap.org/search"

    def search(self, query: str, limit: int) -> list[Location]:
        resp = self._session.get(
            self.URL,
            params={"q": query, "format": "jsonv2", "limit": limit, "addressdetails": 1},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        results: list[Location] = []
        for item in resp.json():
            addr = item.get("address", {})
            name = item.get("name") or item.get("display_name", "").split(",")[0]
            detail = self._join_unique([addr.get("state"), addr.get("country")])
            results.append(Location(name=name, latitude=float(item["lat"]),
                                    longitude=float(item["lon"]), detail=detail,
                                    kind=item.get("addresstype", "")))
        return results


class GeocodingService:
    """Asks several providers at once and returns as soon as it has good results.

    Photon (any kind of place, including streets) and Open-Meteo (cities, very
    fast) run in parallel. When one answers, we wait a short grace period for the
    other, then merge. Nominatim is only tried if both come back empty or fail.
    """

    GRACE_S = 0.5  # how long to wait for the other provider once one has answered

    def __init__(self, fast_providers: list[GeocodingProvider],
                 fallback: GeocodingProvider | None, cache: TTLCache,
                 overall_timeout_s: float = 3.0) -> None:
        self._fast = fast_providers
        self._fallback = fallback
        self._cache = cache
        self._timeout = overall_timeout_s
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="geocode")

    @classmethod
    def default(cls, user_agent: str, timeout_s: float, cache_ttl_s: int) -> "GeocodingService":
        session = requests.Session()
        session.headers["User-Agent"] = user_agent
        per_request = min(timeout_s, 3.0)  # autocomplete must feel instant
        return cls(
            [PhotonGeocoder(session, per_request), OpenMeteoGeocoder(session, per_request)],
            NominatimGeocoder(session, per_request),
            TTLCache(cache_ttl_s, max_size=2000),
            overall_timeout_s=per_request,
        )

    def suggest(self, query: str, limit: int = 6) -> list[Location]:
        query = " ".join(query.split())
        if len(query) < 2:
            return []
        key = (query.lower(), limit)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        results = self._parallel_search(query, limit)
        if not results and self._fallback is not None:
            results = self._safe_search(self._fallback, query, limit)
        if results:
            self._cache.set(key, results)
        return results

    def _parallel_search(self, query: str, limit: int) -> list[Location]:
        futures = {self._pool.submit(self._safe_search, p, query, limit): p for p in self._fast}
        answers: dict[GeocodingProvider, list[Location]] = {}
        deadline = time.monotonic() + self._timeout
        pending = set(futures)
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            done, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            got_results_before = any(answers.values())
            for f in done:
                answers[futures[f]] = f.result()
            if any(answers.values()) and not got_results_before:
                # First useful answer: give the other provider a short grace period.
                deadline = min(deadline, time.monotonic() + self.GRACE_S)

        # Merge in provider priority order, Photon first.
        merged = [loc for p in self._fast for loc in answers.get(p, [])]
        return self._dedupe(merged)[:limit]

    @staticmethod
    def _safe_search(provider: GeocodingProvider, query: str, limit: int) -> list[Location]:
        try:
            return provider.search(query, limit)
        except (requests.RequestException, ValueError, KeyError) as exc:
            log.warning("Geocoder %s failed: %s", provider.name, exc)
            return []

    @staticmethod
    def _dedupe(locations: list[Location]) -> list[Location]:
        seen, out = set(), []
        for loc in locations:
            key = (loc.name.lower(), round(loc.latitude, 1), round(loc.longitude, 1))
            if key not in seen:
                seen.add(key)
                out.append(loc)
        return out