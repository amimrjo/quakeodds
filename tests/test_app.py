"""Offline tests: synthetic catalogs + a fake USGS client (no network needed)."""
import math
import random
from datetime import datetime, timedelta, timezone

from app import EarthquakeProbabilityApp
from quakeodds.analysis.gutenberg_richter import GutenbergRichterModel
from quakeodds.analysis.hazard import SeismicHazardAnalyzer
from quakeodds.models import Earthquake, Location


def synthetic_mags(n, b=1.0, mmin=3.0, seed=1):
    rng = random.Random(seed)
    beta = b * math.log(10)
    return [mmin - 0.05 + rng.expovariate(beta) for _ in range(n)]


def test_b_value_recovered():
    gr = GutenbergRichterModel(synthetic_mags(5000, b=1.0), observation_years=30)
    assert gr.fitted_from_data and abs(gr.b_value - 1.0) < 0.1, gr.b_value
    # roughly 10x fewer quakes per magnitude unit
    assert 7 < gr.annual_rate(4) / gr.annual_rate(5) < 14


def test_sparse_catalog_uses_default_b():
    gr = GutenbergRichterModel([4.6, 4.8, 5.1], observation_years=30)
    assert not gr.fitted_from_data and gr.b_value == 1.0 and gr.annual_rate(5) > 0


def test_empty_catalog():
    gr = GutenbergRichterModel([], observation_years=30)
    assert gr.annual_rate(5) == 0


class FakeClient:
    def __init__(self, mags):
        now = datetime.now(timezone.utc)
        self.events = [Earthquake(f"ev{i}", round(m, 2), "Somewhere", now - timedelta(days=i * 2),
                                  37.4, -121.9, 8.0, "https://example.com", 12.0)
                       for i, m in enumerate(mags)]

    def fetch_events(self, lat, lon, radius_km, start, end=None, min_magnitude=None, limit=20000):
        return [e for e in self.events if e.time >= start][:limit]


class FakeGeocoder:
    def suggest(self, q, limit=6):
        return [Location("Milpitas", 37.43, -121.9, "California, United States", "city")] if q else []


def make_client(mags):
    analyzer = SeismicHazardAnalyzer(FakeClient(mags))
    return EarthquakeProbabilityApp(geocoder=FakeGeocoder(), analyzer=analyzer).flask.test_client()


def test_api_end_to_end():
    c = make_client(synthetic_mags(3000, b=0.95))
    assert c.get("/").status_code == 200
    assert c.get("/api/autocomplete?q=Mil").get_json()["results"][0]["name"] == "Milpitas"
    r = c.get("/api/hazard?lat=37.43&lon=-121.9&name=Milpitas&radius=100")
    data = r.get_json()
    assert r.status_code == 200, data
    probs = [e["probabilities"]["30"] for e in data["estimates"]]
    assert probs == sorted(probs, reverse=True) and probs[0] > 0.99
    print("\n", data["risk_level"], [round(p, 3) for p in probs], data["model"])


def test_api_validation():
    c = make_client([])
    assert c.get("/api/hazard?lat=abc&lon=1").status_code == 400
    assert c.get("/api/hazard?lat=95&lon=1").status_code == 400
    d = c.get("/api/hazard?lat=10&lon=10").get_json()
    assert d["risk_level"] == "Very low" and d["notes"]
