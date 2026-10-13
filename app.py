"""QuakeOdds: earthquake probability for any place, from live USGS data.

Run:  python app.py   then open http://127.0.0.1:5000
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import requests
from flask import Flask, jsonify, render_template, request

from quakeodds.analysis.hazard import SeismicHazardAnalyzer
from quakeodds.config import AppConfig
from quakeodds.models import Location
from quakeodds.services.geocoding import GeocodingService
from quakeodds.services.usgs import USGSEarthquakeClient
from quakeodds.utils.cache import TTLCache


@dataclass(frozen=True)
class HazardQuery:
    """Validated input for /api/hazard."""

    location: Location
    radius_km: int

    @classmethod
    def from_args(cls, args, config: AppConfig) -> "HazardQuery":
        try:
            lat = float(args["lat"])
            lon = float(args["lon"])
        except (KeyError, ValueError):
            raise ValueError("Pick a place from the list, or give numeric lat and lon.")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Latitude must be -90 to 90 and longitude -180 to 180.")
        try:
            radius = int(args.get("radius", config.default_radius_km))
        except ValueError:
            raise ValueError("Radius must be a whole number of kilometres.")
        radius = max(config.min_radius_km, min(config.max_radius_km, radius))
        name = (args.get("name") or "").strip() or f"{lat:.3f}, {lon:.3f}"
        return cls(Location(name=name[:120], latitude=lat, longitude=lon,
                            detail=(args.get("detail") or "")[:160]), radius)


class EarthquakeProbabilityApp:
    """Wires services together and exposes them over HTTP."""

    def __init__(self, config: AppConfig | None = None,
                 geocoder: GeocodingService | None = None,
                 analyzer: SeismicHazardAnalyzer | None = None) -> None:
        self.config = config or AppConfig.from_env()
        c = self.config
        self.geocoder = geocoder or GeocodingService.default(
            c.user_agent, c.http_timeout_s, c.autocomplete_cache_ttl_s)
        self.analyzer = analyzer or SeismicHazardAnalyzer(
            USGSEarthquakeClient(c.user_agent, c.http_timeout_s),
            catalog_years=c.catalog_years,
            catalog_min_magnitude=c.catalog_min_magnitude,
            magnitude_thresholds=c.magnitude_thresholds,
            time_horizons_years=c.time_horizons_years,
            recent_days=c.recent_days,
            cache=TTLCache(c.hazard_cache_ttl_s),
        )
        self.flask = Flask(__name__)
        self._register_routes()

    def _register_routes(self) -> None:
        self.flask.add_url_rule("/", "index", self.index)
        self.flask.add_url_rule("/api/autocomplete", "autocomplete", self.autocomplete)
        self.flask.add_url_rule("/api/hazard", "hazard", self.hazard)

    # ---- views ---------------------------------------------------------
    def index(self):
        c = self.config
        return render_template("index.html", default_radius=c.default_radius_km,
                               radii=(25, 50, 100, 200, 300), years=c.catalog_years)

    def autocomplete(self):
        query = request.args.get("q", "")
        results = self.geocoder.suggest(query, limit=6)
        return jsonify({"query": query, "results": [r.to_dict() for r in results]})

    def hazard(self):
        try:
            q = HazardQuery.from_args(request.args, self.config)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        try:
            report = self.analyzer.analyze(q.location, q.radius_km)
        except requests.Timeout:
            return jsonify({"error": "The USGS earthquake service took too long to answer. Try again, or use a smaller radius."}), 504
        except requests.RequestException as exc:
            logging.exception("USGS request failed")
            return jsonify({"error": f"Couldn't reach the USGS earthquake service ({exc.__class__.__name__}). Try again in a minute."}), 502
        return jsonify(report.to_dict())

    def run(self) -> None:
        self.flask.run(host=self.config.host, port=self.config.port, debug=self.config.debug)


def create_app() -> Flask:
    """Entry point for production servers, e.g. `gunicorn 'app:create_app()'`."""
    return EarthquakeProbabilityApp().flask


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    EarthquakeProbabilityApp().run()
