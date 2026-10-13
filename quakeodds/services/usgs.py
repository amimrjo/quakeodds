"""Client for the USGS real-time earthquake catalog (ComCat FDSN API).

Docs: https://earthquake.usgs.gov/fdsnws/event/1/
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests

from ..models import Earthquake
from ..utils.geo import haversine_km


class USGSEarthquakeClient:
    BASE_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
    MAX_LIMIT = 20000  # hard cap enforced by the USGS API

    def __init__(self, user_agent: str, timeout_s: float = 30.0) -> None:
        self._session = requests.Session()
        self._session.headers["User-Agent"] = user_agent
        self._timeout = timeout_s

    def fetch_events(
        self,
        latitude: float,
        longitude: float,
        radius_km: float,
        start: datetime,
        end: datetime | None = None,
        min_magnitude: float | None = None,
        limit: int = MAX_LIMIT,
    ) -> list[Earthquake]:
        """Earthquakes within `radius_km` of a point, newest first."""
        params: dict[str, object] = {
            "format": "geojson",
            "eventtype": "earthquake",  # leave out quarry blasts and explosions
            "latitude": round(latitude, 4),
            "longitude": round(longitude, 4),
            "maxradiuskm": radius_km,
            "starttime": self._iso(start),
            "orderby": "time",
            "limit": min(limit, self.MAX_LIMIT),
        }
        if end is not None:
            params["endtime"] = self._iso(end)
        if min_magnitude is not None:
            params["minmagnitude"] = min_magnitude

        resp = self._session.get(self.BASE_URL, params=params, timeout=self._timeout)
        if resp.status_code == 204:  # USGS answers "No Content" when nothing matches
            return []
        resp.raise_for_status()
        return [q for f in resp.json().get("features", [])
                if (q := self._parse(f, latitude, longitude)) is not None]

    @staticmethod
    def _iso(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    @staticmethod
    def _parse(feature: dict, origin_lat: float, origin_lon: float) -> Earthquake | None:
        props = feature.get("properties") or {}
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        mag = props.get("mag")
        if mag is None or len(coords) < 2 or props.get("time") is None:
            return None
        lon, lat = coords[0], coords[1]
        depth = coords[2] if len(coords) > 2 and coords[2] is not None else 0.0
        return Earthquake(
            event_id=str(feature.get("id", "")),
            magnitude=float(mag),
            place=props.get("place") or "Unknown location",
            time=datetime.fromtimestamp(props["time"] / 1000, tz=timezone.utc),
            latitude=lat,
            longitude=lon,
            depth_km=float(depth),
            url=props.get("url") or "",
            distance_km=haversine_km(origin_lat, origin_lon, lat, lon),
        )
